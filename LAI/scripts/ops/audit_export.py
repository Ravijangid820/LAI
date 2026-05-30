#!/usr/bin/env python3
"""Export and (optionally) age out ``audit_log`` rows.

Roadmap 2.3 follow-up / PROGRESS_V2 vm-4.

Migration 006 shipped an append-only ``audit_log`` table — every login, query,
upload, report, and export now leaves a row. The table is queryable from the
admin UI, but compliance & retention need two things the UI doesn't give them:

* a **bulk export** in CSV (for the audit binder) or JSON (for tooling), with
  date / action / principal filters; and
* a **retention purge** that deletes rows older than N days. Migration 006's
  ``audit_log_no_update`` trigger blocks ``UPDATE`` (tamper-evidence) but
  intentionally leaves ``DELETE`` to a privileged retention job — that's this
  script.

Design constraints
------------------
* **Read-only import of ``lai.common.audit``.** Reuses :func:`audit.query` for
  the export read path (same single read primitive the admin endpoint uses);
  the purge issues its own ``DELETE`` because :mod:`lai.common.audit` has no
  delete API and adding one is out-of-scope for ops tooling.
* **Single DB.** All connections target the same ``lai_db`` Postgres the
  service writes to — env names mirror :func:`audit._get_pool` so ``.env``
  drives both.
* **Safe by default.** Export is read-only. ``--purge-older-than`` is a no-op
  without ``--yes``; with ``--yes`` it deletes only rows whose ``ts`` is older
  than the cutoff (bound parameter, never string-interpolated).

Examples
--------
    # CSV export of the last 7 days to a file:
    python3 scripts/ops/audit_export.py \
        --since 2026-05-23 --format csv --out audit_2026-05.csv

    # JSON export filtered to login failures for one org:
    python3 scripts/ops/audit_export.py \
        --action login --org-id <uuid> --format json

    # Dry-run a retention cull (always do this first):
    python3 scripts/ops/audit_export.py --purge-older-than 365

    # Actually delete rows older than 365 days:
    python3 scripts/ops/audit_export.py --purge-older-than 365 --yes

Exit codes
----------
  0  export / purge completed
  1  configuration error (bad date, bad UUID, missing destination, etc.)
  2  database connection / query failure
  3  ``--purge-older-than`` invoked without ``--yes`` (dry-run notice)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from lai.common import audit

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_DB = 2
EXIT_DRY_RUN = 3

# Page size used to iterate audit.query for export. Large enough to amortise
# round-trips, small enough to keep memory bounded on very long histories.
_PAGE = 1000

# Column order chosen so the CSV reads like the admin UI listing top-to-bottom.
_FIELDS = (
    "id",
    "ts",
    "user_id",
    "org_id",
    "action",
    "outcome",
    "session_id",
    "latency_ms",
    "detail",
)


def _db_dsn() -> dict[str, Any]:
    """Build asyncpg connect kwargs from the same env :mod:`audit` reads."""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5433")),
        "database": os.getenv("DB_NAME", "lai_db"),
        "user": os.getenv("DB_USER", "lai_user"),
        "password": os.getenv("DB_PASSWORD", "lai_test_password_2024"),
    }


def _parse_ts(label: str, value: str | None) -> datetime | None:
    """Accept ``YYYY-MM-DD`` or any ISO 8601 timestamp; assume UTC if naive."""
    if value is None:
        return None
    try:
        # ``fromisoformat`` handles both date and datetime forms in 3.11+.
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        print(f"error: --{label} must be ISO 8601 (YYYY-MM-DD or full): {exc}", file=sys.stderr)
        sys.exit(EXIT_CONFIG)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_uuid(label: str, value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        print(f"error: --{label} must be a UUID, got {value!r}", file=sys.stderr)
        sys.exit(EXIT_CONFIG)


def _serialise_value(key: str, value: Any) -> Any:
    """Make a row JSON/CSV friendly: datetimes → iso, UUIDs → str, detail intact."""
    if value is None:
        return None
    if key == "ts" and isinstance(value, datetime):
        return value.isoformat()
    if key in ("user_id", "org_id") and isinstance(value, UUID):
        return str(value)
    return value


def _row_for_export(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: _serialise_value(k, raw.get(k)) for k in _FIELDS}


def _filter_in_range(
    rows: list[dict[str, Any]],
    *,
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Apply optional ``since``/``until`` to a page.

    Rows come back newest-first. The second tuple element is ``True`` when we
    crossed below ``since`` — the caller stops paging at that point.
    """
    out: list[dict[str, Any]] = []
    crossed_lower = False
    for row in rows:
        ts = row.get("ts")
        if not isinstance(ts, datetime):
            out.append(row)
            continue
        if until is not None and ts >= until:
            continue
        if since is not None and ts < since:
            crossed_lower = True
            continue
        out.append(row)
    return out, crossed_lower


async def _iter_rows(
    conn: asyncpg.Connection,
    *,
    org_id: UUID | None,
    action: str | None,
    user_id: UUID | None,
    since: datetime | None,
    until: datetime | None,
    hard_limit: int | None,
) -> list[dict[str, Any]]:
    """Page through :func:`audit.query` newest-first, applying date filters.

    ``audit.query`` doesn't expose a date range (the admin UI doesn't need
    one), so we ask it for pages and trim by ``ts`` here. Since rows are
    newest-first, the moment a page contains a row older than ``since`` we
    know every subsequent page is older too and can stop.
    """
    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await audit.query(
            conn,
            org_id=org_id,
            action=action,
            user_id=user_id,
            limit=_PAGE,
            offset=offset,
        )
        if not page:
            break
        filtered, hit_lower = _filter_in_range(page, since=since, until=until)
        collected.extend(filtered)
        if hard_limit is not None and len(collected) >= hard_limit:
            return collected[:hard_limit]
        if hit_lower:
            break
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return collected


def _write_csv(rows: list[dict[str, Any]], out: Path | None) -> None:
    fh = sys.stdout if out is None else out.open("w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        for row in rows:
            export = _row_for_export(row)
            # JSONB detail is dict|None → embed as a JSON string in the cell so
            # the CSV is single-row-per-event and round-trips through pandas.
            if export.get("detail") is not None:
                export["detail"] = json.dumps(export["detail"], default=str)
            writer.writerow(export)
    finally:
        if out is not None:
            fh.close()


def _write_json(rows: list[dict[str, Any]], out: Path | None) -> None:
    payload = [_row_for_export(row) for row in rows]
    text = json.dumps(payload, indent=2, default=str)
    if out is None:
        sys.stdout.write(text + "\n")
    else:
        out.write_text(text + "\n", encoding="utf-8")


async def _purge(conn: asyncpg.Connection, *, older_than_days: int, do_it: bool) -> tuple[int, datetime]:
    """Count (or delete) audit rows older than ``older_than_days`` days.

    Returns ``(rowcount, cutoff)``. With ``do_it=False`` it only counts so the
    caller can preview the impact; with ``do_it=True`` it actually deletes.
    The cutoff is computed in UTC and bound as a parameter — no f-strings, no
    interpolation, no SQL surface.
    """
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    if not do_it:
        n = await conn.fetchval("SELECT COUNT(*) FROM audit_log WHERE ts < $1", cutoff)
        return int(n or 0), cutoff
    # asyncpg's ``execute`` returns a command tag like "DELETE 1234"; parse the
    # trailing integer so callers get a real row count for the log line.
    tag = await conn.execute("DELETE FROM audit_log WHERE ts < $1", cutoff)
    try:
        deleted = int(tag.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        deleted = 0
    return deleted, cutoff


async def _run(args: argparse.Namespace) -> int:
    since = _parse_ts("since", args.since)
    until = _parse_ts("until", args.until)
    org_id = _parse_uuid("org-id", args.org_id)
    user_id = _parse_uuid("user-id", args.user_id)

    if args.out is not None and args.out.parent and not args.out.parent.exists():
        print(f"error: --out parent does not exist: {args.out.parent}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        conn = await asyncpg.connect(**_db_dsn())
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"error: cannot connect to audit DB: {exc}", file=sys.stderr)
        return EXIT_DB

    try:
        # Purge path runs INSTEAD of export when requested, to keep failure
        # modes isolated (a botched export shouldn't leave a half-purged log).
        if args.purge_older_than is not None:
            if args.purge_older_than < 0:
                print("error: --purge-older-than must be >= 0", file=sys.stderr)
                return EXIT_CONFIG
            if not args.yes:
                count, cutoff = await _purge(conn, older_than_days=args.purge_older_than, do_it=False)
                print(
                    f"dry-run: {count} audit_log row(s) older than "
                    f"{cutoff.isoformat()} would be deleted "
                    f"(--purge-older-than {args.purge_older_than}). "
                    "Re-run with --yes to actually delete."
                )
                return EXIT_DRY_RUN
            deleted, cutoff = await _purge(conn, older_than_days=args.purge_older_than, do_it=True)
            print(
                f"purged: {deleted} audit_log row(s) older than {cutoff.isoformat()} "
                f"(--purge-older-than {args.purge_older_than})."
            )
            return EXIT_OK

        # Export path.
        rows = await _iter_rows(
            conn,
            org_id=org_id,
            action=args.action,
            user_id=user_id,
            since=since,
            until=until,
            hard_limit=args.limit,
        )
    except asyncpg.PostgresError as exc:
        print(f"error: audit query failed: {exc}", file=sys.stderr)
        return EXIT_DB
    finally:
        await conn.close()

    if args.format == "csv":
        _write_csv(rows, args.out)
    else:
        _write_json(rows, args.out)
    where = args.out if args.out is not None else "stdout"
    print(f"exported {len(rows)} row(s) as {args.format} -> {where}", file=sys.stderr)
    return EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="audit_export.py",
        description="Export or age-out audit_log rows (compliance / retention).",
    )
    parser.add_argument("--since", help="lower bound on ts (ISO 8601; UTC if naive)")
    parser.add_argument("--until", help="upper bound on ts, exclusive (ISO 8601)")
    parser.add_argument(
        "--action",
        help="filter by action (e.g. login, query, upload, report, export)",
    )
    parser.add_argument("--org-id", help="filter by org UUID")
    parser.add_argument("--user-id", help="filter by user UUID")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="hard cap on exported rows (default: no cap)",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="output format (default csv)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output file (default stdout)",
    )
    parser.add_argument(
        "--purge-older-than",
        type=int,
        default=None,
        metavar="DAYS",
        help="DELETE rows older than this many days (dry-run unless --yes)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm a purge; required by --purge-older-than to actually delete",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
