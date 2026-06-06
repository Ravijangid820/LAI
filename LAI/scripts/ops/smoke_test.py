#!/usr/bin/env python3
"""LAI system smoke test — guards against the reranker-on-CPU regression.

Roadmap 1.2 / PROGRESS_V2 vm-1.

The boss-test failure root cause: on a host reboot the serve_rag process lost
GPU access, PyTorch silently fell back to CPU, the in-process Qwen3 reranker ran
on CPU, and every chat query blocked for 60-180s while ``/health`` stayed green.
This script makes that failure LOUD before a real user hits it.

What it checks
--------------
1. GET  /health     — service reachable and the LLM is loaded.
2. POST /auth/login — obtain a bearer token (or reuse ``LAI_SMOKE_TOKEN``).
3. POST /sessions   — seed an empty session.
4. POST /query      — a RAG-mode question, wall-clock timed.
5. Assert (a) the round-trip finished within ``LAI_SMOKE_MAX_S`` (default 20s),
          (b) the most recent ``Loading reranker ... on <device>`` line in
              serve_rag.log says ``cuda``, not ``cpu``.

Why ``force_mode=rag`` rather than a plain chat query: the chat path skips
retrieval and the reranker entirely, so a chat round-trip cannot surface a
CPU-bound reranker through latency. A RAG question routes through the exact
path that regressed. Check (b) reads the device straight from the log, so it
catches the regression even on a fast box where latency alone would not.

Exit codes (distinct so a cron can alert on the cause)
------------------------------------------------------
  0  all checks passed
  1  configuration error (missing credentials / bad env)
  2  service unreachable or still loading
  3  authentication failed
  4  query failed / server error
  5  query slower than the latency budget (possible CPU fallback)
  6  reranker is on CPU per the log (the regression), or the log is unreadable
  7  DDiQ report leg failed (start, status, or never advanced)

Environment
-----------
  LAI_SMOKE_URL          base URL                 (default http://localhost:18000)
  LAI_SMOKE_TOKEN        pre-minted access token  (skips login when set)
  LAI_SMOKE_EMAIL        login email              (used when no TOKEN)
  LAI_SMOKE_PASSWORD     login password           (used when no TOKEN)
  LAI_SMOKE_USER         alias for LAI_SMOKE_EMAIL (matches vm-3 spec naming)
  LAI_SMOKE_PASS         alias for LAI_SMOKE_PASSWORD
  LAI_SMOKE_QUESTION     question to send         (default: a BImSchG corpus query)
  LAI_SMOKE_FORCE_MODE   "rag" | "chat" | ""      (default "rag")
  LAI_SMOKE_MAX_S        pass/fail latency budget (default 20)
  LAI_SMOKE_TIMEOUT      HTTP timeout seconds     (default 120)
  LAI_SERVE_RAG_LOG      serve_rag log path       (default <repo>/logs/host/serve_rag.log)
  LAI_SMOKE_DDIQ_URL     DDiQ base URL            (default http://localhost:18001)
  LAI_SMOKE_DDIQ_DOC_ID  seeded ddiq_documents id (REQUIRED when --report is set)
  LAI_SMOKE_DDIQ_PRESET  report preset            (default "comprehensive")
  LAI_SMOKE_DDIQ_MAX_S   report budget seconds    (default 600 — DDiQ is slow)
  LAI_SMOKE_DDIQ_POLL_S  status poll interval     (default 10)

Usage
-----
  # after a restart_serve_rag.sh:
  export LAI_SMOKE_EMAIL=ops@yourfirm.de LAI_SMOKE_PASSWORD=...
  python3 LAI/scripts/ops/smoke_test.py

  # also exercise the DDiQ report pipeline (needs a seeded document id):
  export LAI_SMOKE_DDIQ_DOC_ID=<uuid-of-a-tiny-ddiq_documents-row>
  python3 LAI/scripts/ops/smoke_test.py --report

This script uses only the Python standard library, so any python3 runs it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

# ── Exit codes ──────────────────────────────────────────────────────────────
EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_UNREACHABLE = 2
EXIT_AUTH = 3
EXIT_QUERY = 4
EXIT_SLOW = 5
EXIT_RERANKER_CPU = 6
EXIT_REPORT = 7

# serve_rag prints this once at startup (search/eval.py: "Loading reranker
# {model} on {device}..."). We take the LAST match in the log, which reflects
# the device of the currently-running process.
_RERANKER_RE = re.compile(r"Loading reranker .*? on (cuda(?::\d+)?|cpu)\b")

# Read at most the tail of the log; the line we want is from the latest start.
_LOG_TAIL_BYTES = 512 * 1024

# Default repo root = two levels up from scripts/ops/smoke_test.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _ok(msg: str) -> None:
    print(f"  ok   {msg}")


def _info(msg: str) -> None:
    print(f"  ..   {msg}")


def _fail(code: int, msg: str) -> NoReturn:
    print(f"\nSMOKE FAIL [{code}]: {msg}", file=sys.stderr)
    sys.exit(code)


def _http(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float,
) -> tuple[int, dict | None, str]:
    """Return (status_code, parsed_json_or_None, raw_text).

    Raises urllib.error.URLError (incl. timeout) for transport failures; HTTP
    error responses (4xx/5xx) are returned, not raised, so callers can read the
    server's detail message.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    parsed: dict | None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            parsed = None
    except (ValueError, TypeError):
        parsed = None
    return status, parsed, raw


def _detail(parsed: dict | None, raw: str) -> str:
    if parsed and isinstance(parsed.get("detail"), str):
        return parsed["detail"]
    return (raw or "").strip()[:300] or "<empty response>"


def _reranker_device(log_path: Path) -> str | None:
    """Most recent reranker device from the log, or None if not found."""
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as fh:
            if size > _LOG_TAIL_BYTES:
                fh.seek(size - _LOG_TAIL_BYTES)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    last = None
    for m in _RERANKER_RE.finditer(text):
        last = m.group(1)
    return last


# A DDiQ report progresses through these statuses; we accept any terminal hit on
# "done" within budget, or — when the budget runs out — at least one observable
# advance from the initial state ("queued"→"running" / progress > 0) so a slow
# but healthy box still passes. "failed"/"error"/"cancelled" are immediate fails.
_DDIQ_TERMINAL_PASS = {"done", "complete", "completed", "ready"}
_DDIQ_TERMINAL_FAIL = {"failed", "error", "errored", "cancelled", "canceled"}


def _ddiq_progress(parsed: dict | None) -> tuple[str, float]:
    """Pull (status, progress_pct) from a /ddiq/report/{id}/status payload.

    The DDiQ payload shape has bounced around between versions (``status`` vs
    ``state``; ``progress`` 0-1 vs 0-100), so be forgiving — we only need it to
    tell "advanced or didn't" from a smoke perspective.
    """
    if not parsed:
        return ("", 0.0)
    status = str(parsed.get("status") or parsed.get("state") or "").lower()
    raw = parsed.get("progress")
    if raw is None:
        raw = parsed.get("progress_pct", 0)
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        pct = 0.0
    # Normalise 0-1 fractions to a percentage.
    if 0.0 < pct <= 1.0:
        pct *= 100.0
    return (status, pct)


def _run_report_leg(*, ddiq_base: str, doc_id: str, token: str | None) -> None:
    """Start an async DDiQ report and poll until done / advanced / budget elapsed.

    Exits via :func:`_fail` on configuration, transport, or status failures;
    returns normally on pass. Sends the serve_rag bearer token along — DDiQ
    ignores it if the route is unauthenticated, and uses it where it's needed
    (e.g. the audited export route). Failure exit code 7.
    """
    preset = _env("LAI_SMOKE_DDIQ_PRESET", "comprehensive")
    try:
        budget = float(_env("LAI_SMOKE_DDIQ_MAX_S", "600"))
        poll_s = float(_env("LAI_SMOKE_DDIQ_POLL_S", "10"))
    except ValueError:
        _fail(EXIT_CONFIG, "LAI_SMOKE_DDIQ_MAX_S / LAI_SMOKE_DDIQ_POLL_S must be numeric")

    _info(f"ddiq report: doc_id={doc_id}, preset={preset}, budget={budget:.0f}s")
    try:
        status, parsed, raw = _http(
            "POST",
            f"{ddiq_base}/ddiq/report/generate/async",
            token=token,
            body={"document_ids": [doc_id], "preset": preset},
            timeout=60.0,
        )
    except urllib.error.URLError as exc:
        _fail(EXIT_UNREACHABLE, f"cannot reach {ddiq_base}: {exc.reason}")
    except TimeoutError:
        _fail(EXIT_UNREACHABLE, f"timed out POSTing to {ddiq_base}/ddiq/report/generate/async")
    if status != 200 or not parsed or not parsed.get("report_id"):
        _fail(EXIT_REPORT, f"ddiq generate/async failed ({status}): {_detail(parsed, raw)}")
    report_id = parsed["report_id"]
    _ok(f"ddiq report kicked off: {report_id}")

    t0 = time.monotonic()
    last_status, last_pct = ("", -1.0)
    advanced = False
    while True:
        elapsed = time.monotonic() - t0
        try:
            code, body, raw = _http("GET", f"{ddiq_base}/ddiq/report/{report_id}/status", token=token, timeout=30.0)
        except urllib.error.URLError as exc:
            _fail(EXIT_REPORT, f"ddiq status fetch failed: {exc.reason}")
        if code != 200 or not body:
            _fail(EXIT_REPORT, f"ddiq status returned {code}: {_detail(body, raw)}")
        rstatus, rpct = _ddiq_progress(body)
        if rstatus != last_status or rpct > last_pct + 0.5:
            _info(f"ddiq @ {elapsed:5.1f}s: status={rstatus or '?'} progress={rpct:5.1f}%")
            if rstatus and rstatus != last_status:
                advanced = True
            if rpct > last_pct + 0.5 and last_pct >= 0:
                advanced = True
            last_status, last_pct = rstatus, rpct

        if rstatus in _DDIQ_TERMINAL_FAIL:
            _fail(EXIT_REPORT, f"ddiq report ended in {rstatus!r} after {elapsed:.0f}s")
        if rstatus in _DDIQ_TERMINAL_PASS:
            _ok(f"ddiq report done in {elapsed:.0f}s (status={rstatus})")
            return
        if elapsed >= budget:
            if advanced:
                _ok(
                    f"ddiq report still {rstatus or '?'} at budget ({budget:.0f}s) "
                    f"but progress advanced to {rpct:.1f}% — acceptable"
                )
                return
            _fail(
                EXIT_REPORT,
                f"ddiq report did not advance in {budget:.0f}s (status={rstatus or '?'}, progress={rpct:.1f}%)",
            )
        time.sleep(poll_s)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smoke_test.py",
        description=(
            "LAI system smoke test — health/login/query + reranker-on-CPU guard. "
            "With --report also runs a DDiQ async-report leg against :18001."
        ),
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "After the chat/RAG leg, kick off a DDiQ async report against "
            "LAI_SMOKE_DDIQ_DOC_ID and poll until it reaches 'done' or the "
            "LAI_SMOKE_DDIQ_MAX_S budget elapses. Requires the doc id env."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base = _env("LAI_SMOKE_URL", "http://localhost:18000").rstrip("/")
    token = _env("LAI_SMOKE_TOKEN")
    # Honour both the original vm-1 env names and the LAI_SMOKE_USER/PASS pair
    # the vm-3 task spec wrote out; LAI_SMOKE_EMAIL/PASSWORD win when both are set
    # since that's what the README + cron line already document.
    email = _env("LAI_SMOKE_EMAIL") or _env("LAI_SMOKE_USER")
    password = os.environ.get("LAI_SMOKE_PASSWORD") or os.environ.get("LAI_SMOKE_PASS", "")
    question = _env(
        "LAI_SMOKE_QUESTION",
        "Welche Genehmigung ist nach dem BImSchG fuer eine Windenergieanlage erforderlich?",
    )
    force_mode = _env("LAI_SMOKE_FORCE_MODE", "rag")
    log_path = Path(_env("LAI_SERVE_RAG_LOG", str(_REPO_ROOT / "logs/host/serve_rag.log")))

    try:
        max_s = float(_env("LAI_SMOKE_MAX_S", "20"))
        timeout = float(_env("LAI_SMOKE_TIMEOUT", "120"))
    except ValueError:
        _fail(EXIT_CONFIG, "LAI_SMOKE_MAX_S / LAI_SMOKE_TIMEOUT must be numeric")

    if not token and not (email and password):
        _fail(
            EXIT_CONFIG,
            "no credentials: set LAI_SMOKE_TOKEN, or LAI_SMOKE_EMAIL + LAI_SMOKE_PASSWORD",
        )

    print(f"LAI smoke test -> {base}")

    # 1. Health ---------------------------------------------------------------
    try:
        status, parsed, raw = _http("GET", f"{base}/health", timeout=timeout)
    except urllib.error.URLError as exc:
        _fail(EXIT_UNREACHABLE, f"cannot reach {base}/health: {exc.reason}")
    except TimeoutError:
        _fail(EXIT_UNREACHABLE, f"timed out reaching {base}/health")
    if status != 200 or not parsed:
        _fail(EXIT_UNREACHABLE, f"/health returned {status}: {_detail(parsed, raw)}")
    if not parsed.get("loaded"):
        _fail(EXIT_UNREACHABLE, "/health reports the model is not loaded yet")
    _ok(
        "health: loaded="
        f"{parsed.get('loaded')} backend={parsed.get('llm_backend')} "
        f"retrieval_ready={parsed.get('retrieval_ready')}"
    )

    # 2. Auth -----------------------------------------------------------------
    if token:
        _ok("auth: using LAI_SMOKE_TOKEN")
    else:
        status, parsed, raw = _http(
            "POST",
            f"{base}/auth/login",
            body={"email": email, "password": password, "remember_me": False},
            timeout=timeout,
        )
        if status != 200 or not parsed or not parsed.get("access_token"):
            _fail(EXIT_AUTH, f"login failed ({status}): {_detail(parsed, raw)}")
        token = parsed["access_token"]
        _ok(f"auth: logged in as {email}")

    # 3. Seed a session -------------------------------------------------------
    status, parsed, raw = _http("POST", f"{base}/sessions", token=token, timeout=timeout)
    if status != 200 or not parsed or not parsed.get("session_id"):
        _fail(EXIT_QUERY, f"could not create session ({status}): {_detail(parsed, raw)}")
    session_id = parsed["session_id"]
    _ok(f"session: {session_id}")

    # Register cleanup so the smoke session is DELETEd at exit (success OR
    # failure). Without this, each hourly cron run leaves a session behind
    # in sessions.db — observed 500+ accumulated rows for the cron user
    # (cd5a4a1b…), which polluted the chat sidebar of anyone who logged
    # into the smoke-test account. Best-effort: a failed DELETE is silently
    # ignored so it can't mask the real smoke result. ``atexit`` runs even
    # after _fail's sys.exit (SystemExit is caught by atexit handlers).
    import atexit
    def _cleanup_smoke_session() -> None:
        try:
            _http("DELETE", f"{base}/sessions/{session_id}", token=token, timeout=10)
        except Exception:
            pass
    atexit.register(_cleanup_smoke_session)

    # 4. Timed query ----------------------------------------------------------
    payload: dict = {"question": question, "session_id": session_id}
    if force_mode:
        payload["force_mode"] = force_mode
    _info(f"query (force_mode={force_mode or 'auto'}): {question!r}")
    t0 = time.monotonic()
    try:
        status, parsed, raw = _http("POST", f"{base}/query", token=token, body=payload, timeout=timeout)
    except (urllib.error.URLError, TimeoutError):
        elapsed = time.monotonic() - t0
        _fail(
            EXIT_SLOW,
            f"no response within {timeout:.0f}s (elapsed {elapsed:.1f}s) — a CPU reranker stalls here",
        )
    elapsed = time.monotonic() - t0
    if status != 200 or not parsed:
        _fail(EXIT_QUERY, f"query failed ({status}): {_detail(parsed, raw)}")

    timings = parsed.get("timings") or {}
    server_t = ", ".join(
        f"{k}={timings[k]:.1f}s"
        for k in ("embed_s", "retrieve_s", "rerank_s", "generate_s", "total_s")
        if isinstance(timings.get(k), (int, float))
    )
    _ok(f"query: {elapsed:.1f}s wall, mode={parsed.get('mode')}, answer={len(parsed.get('answer', ''))} chars")
    if server_t:
        _info(f"server timings: {server_t}")

    # 5a. Latency assertion ---------------------------------------------------
    if elapsed > max_s:
        _fail(
            EXIT_SLOW,
            f"query took {elapsed:.1f}s > budget {max_s:.0f}s"
            + (f" (server {server_t})" if server_t else "")
            + " — check the reranker device",
        )
    _ok(f"latency within budget ({elapsed:.1f}s <= {max_s:.0f}s)")

    # 5b. Reranker-device assertion ------------------------------------------
    device = _reranker_device(log_path)
    if device is None:
        _fail(
            EXIT_RERANKER_CPU,
            f"could not find a 'Loading reranker ... on <device>' line in {log_path} "
            "— cannot confirm GPU placement (set LAI_SERVE_RAG_LOG)",
        )
    if not device.startswith("cuda"):
        _fail(
            EXIT_RERANKER_CPU,
            f"reranker is on {device!r} (CPU fallback) per {log_path} — "
            "the boss-test regression; restore GPU access and restart serve_rag",
        )
    _ok(f"reranker on {device}")

    # 6. Optional DDiQ report leg --------------------------------------------
    if args.report:
        ddiq_base = _env("LAI_SMOKE_DDIQ_URL", "http://localhost:18001").rstrip("/")
        ddiq_doc_id = _env("LAI_SMOKE_DDIQ_DOC_ID")
        if not ddiq_doc_id:
            _fail(
                EXIT_CONFIG,
                "--report needs LAI_SMOKE_DDIQ_DOC_ID (a seeded ddiq_documents id); "
                "see scripts/ops/README.md for how to seed one once",
            )
        _run_report_leg(ddiq_base=ddiq_base, doc_id=ddiq_doc_id, token=token)
        print("\nSMOKE PASS: serve_rag healthy, query fast, reranker on GPU, ddiq report ok.")
    else:
        print("\nSMOKE PASS: serve_rag healthy, query fast, reranker on GPU.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
