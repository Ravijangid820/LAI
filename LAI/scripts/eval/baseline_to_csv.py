#!/usr/bin/env python3
"""Export a retention-probe baseline JSON to a reviewable CSV.

Phase-3 prep helper / PROGRESS_V2 vm follow-on (CSV-export-of-baseline tool).

The retention-probe baseline JSON
(``LAI/training/fine_tuning/eval/baselines/<base>__retention_probes.json``) is
the lock-down record of how the *base* model answers every probe before any
LoRA touches it. The callback ``RetentionProbeCallback`` reads this artifact
at training start, validates its ``probes_sha256`` against the live probes
file (gap-D), and uses it as the comparison point for every checkpoint.

Two reviewers (engineering + the lawyer running §3.4) want to **spreadsheet**
those base answers ahead of the actual training run:

* engineering, to spot-check that the base model isn't already fabricating
  on the fictional probes (if it were, the LoRA can't make that worse — but
  it also can't be the only signal we trust);
* the lawyer, to skim the German-legal answers and flag anything that
  shouldn't have shipped as the "good" baseline.

JSON is the wrong format for that review. This tool emits a flat CSV that
joins the baseline's per-probe answers with the probes JSONL (so the prompt,
category, language, ``fictional`` flag, and notes ride alongside the answer)
and a sidecar key-value CSV with the baseline's ``meta`` block (model name,
quantization, ``enable_thinking``, ``probes_sha256``, timestamp, …) so the
reviewer never loses the lineage.

Design constraints
------------------
* **Stdlib only.** No torch, no transformers, no pandas. The whole point of
  the tool is to be a quick spreadsheet helper; pulling in the 4-bit stack
  just to read a JSON file would be silly.
* **Read-only.** Never edits the baseline JSON, the probes JSONL, or anything
  under ``training/fine_tuning/``.
* **Probe-file order, not dict order.** Walks the JSONL line-by-line and
  joins each probe with its baseline answer by ID. Reviewers expect to scroll
  through the probes in the order ``retention_probes.jsonl`` lists them.
* **Loud about staleness.** Recomputes ``sha256`` of the probes file and
  compares against the baseline's recorded ``probes_sha256``. A mismatch is
  printed to ``stderr`` (and surfaced in the meta sidecar) but does not abort
  the export — the doc explicitly notes the current baseline is stale, and a
  reviewer may want the CSV anyway. Stale flag is loud so they know.
* **CSV is quote-all.** Baseline answers can contain commas, newlines, and
  German non-ASCII; ``csv.QUOTE_ALL`` is the only setting that survives every
  spreadsheet importer the lawyer might use.

Examples
--------
    # Default: baseline → sibling .csv next to the JSON.
    python3 LAI/scripts/eval/baseline_to_csv.py \\
        --baseline LAI/training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json

    # Explicit out paths + use a different probes file (rare):
    python3 LAI/scripts/eval/baseline_to_csv.py \\
        --baseline LAI/training/fine_tuning/eval/baselines/qwen36-27b__retention_probes.json \\
        --probes LAI/training/fine_tuning/eval/probes/retention_probes.jsonl \\
        --out /tmp/qwen36-27b_baseline.csv \\
        --meta-out /tmp/qwen36-27b_baseline_meta.csv

    # Pipe the main CSV to stdout (skip the meta sidecar):
    python3 LAI/scripts/eval/baseline_to_csv.py \\
        --baseline …/qwen36-27b__retention_probes.json --out - | head

Exit codes
----------
  0  CSV written cleanly (including with a probes_sha256 mismatch warning)
  1  configuration error (file not found, bad JSON, bad probes line)
  2  baseline schema unexpected (missing ``meta`` or ``answers``)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

# Columns emitted by the main CSV, in this order. The reviewer reads
# left-to-right; identity first, then the answer, then the diagnostics.
_COLUMNS: tuple[str, ...] = (
    "probe_id",
    "category",
    "language",
    "fictional",
    "prompt",
    "answer",
    "answer_len",
    "ascii_ratio",
    "notes",
)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_probes(path: Path) -> list[dict]:
    """Stream the probes JSONL into a list of dicts.

    Kept independent of :mod:`training.fine_tuning.eval.retention_probe` so
    this script stays stdlib-only — that module top-imports ``torch``.
    """
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                d = json.loads(s)
            except json.JSONDecodeError as e:
                print(
                    f"ERROR: bad JSON at probes line {i}: {e}",
                    file=sys.stderr,
                )
                raise SystemExit(1) from e
            for required in ("id", "category", "language", "prompt"):
                if required not in d:
                    print(
                        f"ERROR: probes line {i} missing required field '{required}'",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
            out.append(d)
    return out


def _resolve_probes_path(baseline_meta: dict, baseline_path: Path) -> Path:
    """The baseline's meta records the probes path it was generated against.

    The recorded path is repo-relative (set by ``retention_probe.py``'s
    ``--probes`` flag, typically ``training/fine_tuning/eval/probes/...``).
    Resolve it against the repo root by walking up from the baseline file
    until we find a directory that contains the recorded path.
    """
    recorded = baseline_meta.get("probes_path")
    if not recorded:
        return Path("")
    # baseline lives at .../LAI/training/fine_tuning/eval/baselines/X.json,
    # recorded is "training/fine_tuning/eval/probes/retention_probes.jsonl".
    # Walk up from the baseline until we land on a directory that contains
    # the recorded path.
    here = baseline_path.resolve().parent
    for _ in range(8):
        candidate = (here / recorded).resolve()
        if candidate.is_file():
            return candidate
        here = here.parent
    return Path(recorded)  # last-ditch — relative to cwd


def _write_main_csv(
    out: Path | None,
    probes: list[dict],
    answers: dict,
) -> tuple[int, int]:
    """Emit one row per probe. Returns (rows_written, missing_answer_count)."""
    fh = sys.stdout if out is None or str(out) == "-" else out.open("w", encoding="utf-8", newline="")
    try:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(_COLUMNS)
        rows = 0
        missing = 0
        for p in probes:
            pid = p["id"]
            ans_obj = answers.get(pid)
            if ans_obj is None:
                missing += 1
                ans, alen, aratio = "", "", ""
            else:
                ans = ans_obj.get("answer", "")
                alen = ans_obj.get("len", len(ans))
                aratio = ans_obj.get("ascii_ratio", "")
            writer.writerow(
                [
                    pid,
                    p.get("category", ""),
                    p.get("language", ""),
                    "true" if bool(p.get("fictional", False)) else "false",
                    p.get("prompt", ""),
                    ans,
                    alen,
                    aratio,
                    p.get("notes", ""),
                ]
            )
            rows += 1
        return rows, missing
    finally:
        if fh is not sys.stdout:
            fh.close()


def _write_meta_csv(path: Path, meta: dict, *, sha_match: bool, current_sha: str) -> None:
    """Emit the baseline meta block as a 2-column key/value CSV.

    Adds two synthetic rows the original meta block does not carry:
    ``current_probes_sha256`` and ``probes_sha256_match`` so a reviewer
    opening only the sidecar still knows whether the baseline is stale
    against the probes-file-on-disk *right now*.
    """
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(["key", "value"])
        for k, v in meta.items():
            if isinstance(v, (dict, list)):
                writer.writerow([k, json.dumps(v, ensure_ascii=False)])
            else:
                writer.writerow([k, "" if v is None else str(v)])
        writer.writerow(["current_probes_sha256", current_sha])
        writer.writerow(["probes_sha256_match", "true" if sha_match else "false"])


def main() -> int:
    p = argparse.ArgumentParser(
        description="Export a retention-probe baseline JSON to a flat CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to the baseline JSON written by retention_probe.py --save-base-answers.",
    )
    p.add_argument(
        "--probes",
        type=Path,
        default=None,
        help="Optional. Defaults to the path recorded in the baseline meta.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV path. Defaults to <baseline>.csv. Use '-' for stdout.",
    )
    p.add_argument(
        "--meta-out",
        type=Path,
        default=None,
        help="Optional. Writes a 2-column key/value CSV of the baseline meta block.",
    )
    args = p.parse_args()

    if not args.baseline.is_file():
        print(f"ERROR: baseline not found: {args.baseline}", file=sys.stderr)
        return 1
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: baseline JSON parse failed: {e}", file=sys.stderr)
        return 1

    meta = baseline.get("meta")
    answers = baseline.get("answers")
    if not isinstance(meta, dict) or not isinstance(answers, dict):
        print(
            "ERROR: baseline schema unexpected — need top-level 'meta' (dict) and 'answers' (dict).",
            file=sys.stderr,
        )
        return 2

    probes_path = args.probes
    if probes_path is None:
        probes_path = _resolve_probes_path(meta, args.baseline)
    if not probes_path or not probes_path.is_file():
        print(
            f"ERROR: probes file not found (looked for: {probes_path or '<none recorded in meta>'}). "
            "Pass --probes explicitly.",
            file=sys.stderr,
        )
        return 1

    probes = _load_probes(probes_path)
    if not probes:
        print(f"ERROR: probes file {probes_path} produced 0 rows", file=sys.stderr)
        return 1

    # Staleness check — surface it, don't fail. The training-run callback is
    # the authoritative gate for this; here we are an offline review tool.
    current_sha = _sha256_of_file(probes_path)
    recorded_sha = meta.get("probes_sha256", "")
    sha_match = bool(recorded_sha) and recorded_sha == current_sha
    if not sha_match:
        print(
            "WARN: probes_sha256 mismatch — the baseline JSON was generated "
            "against a different probes file than the one on disk now.\n"
            f"      baseline meta : {recorded_sha or '<missing>'}\n"
            f"      current file  : {current_sha}\n"
            "      The CSV will still be written; treat affected rows as stale.",
            file=sys.stderr,
        )

    out = args.out
    if out is None:
        out = args.baseline.with_suffix(".csv")

    rows, missing = _write_main_csv(out, probes, answers)
    if str(out) != "-":
        print(f"Wrote {out} ({rows} rows)", file=sys.stderr)
    if missing:
        print(
            f"WARN: {missing} probe(s) had no matching baseline answer "
            "(probably a probes file widened after the baseline was generated).",
            file=sys.stderr,
        )

    # Surface answers in baseline that no longer match a probe ID — the dual
    # side of the staleness story.
    orphaned = sorted(set(answers) - {p["id"] for p in probes})
    if orphaned:
        print(
            f"WARN: {len(orphaned)} baseline answer(s) without a current probe id "
            f"(probes file shrank or IDs renamed): {', '.join(orphaned)}",
            file=sys.stderr,
        )

    if args.meta_out is not None:
        _write_meta_csv(args.meta_out, meta, sha_match=sha_match, current_sha=current_sha)
        print(f"Wrote {args.meta_out} (meta)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
