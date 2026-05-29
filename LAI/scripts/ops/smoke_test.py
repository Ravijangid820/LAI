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

Environment
-----------
  LAI_SMOKE_URL        base URL                 (default http://localhost:18000)
  LAI_SMOKE_TOKEN      pre-minted access token  (skips login when set)
  LAI_SMOKE_EMAIL      login email              (used when no TOKEN)
  LAI_SMOKE_PASSWORD   login password           (used when no TOKEN)
  LAI_SMOKE_QUESTION   question to send         (default: a BImSchG corpus query)
  LAI_SMOKE_FORCE_MODE "rag" | "chat" | ""      (default "rag")
  LAI_SMOKE_MAX_S      pass/fail latency budget (default 20)
  LAI_SMOKE_TIMEOUT    HTTP timeout seconds     (default 120)
  LAI_SERVE_RAG_LOG    serve_rag log path       (default <repo>/logs/host/serve_rag.log)

Usage
-----
  # after a restart_serve_rag.sh:
  export LAI_SMOKE_EMAIL=ops@yourfirm.de LAI_SMOKE_PASSWORD=...
  python3 LAI/scripts/ops/smoke_test.py

This script uses only the Python standard library, so any python3 runs it.
"""

from __future__ import annotations

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


def main() -> int:
    base = _env("LAI_SMOKE_URL", "http://localhost:18000").rstrip("/")
    token = _env("LAI_SMOKE_TOKEN")
    email = _env("LAI_SMOKE_EMAIL")
    password = os.environ.get("LAI_SMOKE_PASSWORD", "")
    question = _env(
        "LAI_SMOKE_QUESTION",
        "Welche Genehmigung ist nach dem BImSchG fuer eine "
        "Windenergieanlage erforderlich?",
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
            "no credentials: set LAI_SMOKE_TOKEN, or LAI_SMOKE_EMAIL + "
            "LAI_SMOKE_PASSWORD",
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

    # 4. Timed query ----------------------------------------------------------
    payload: dict = {"question": question, "session_id": session_id}
    if force_mode:
        payload["force_mode"] = force_mode
    _info(f"query (force_mode={force_mode or 'auto'}): {question!r}")
    t0 = time.monotonic()
    try:
        status, parsed, raw = _http(
            "POST", f"{base}/query", token=token, body=payload, timeout=timeout
        )
    except (urllib.error.URLError, TimeoutError):
        elapsed = time.monotonic() - t0
        _fail(
            EXIT_SLOW,
            f"no response within {timeout:.0f}s (elapsed {elapsed:.1f}s) — a CPU "
            "reranker stalls here",
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
    _ok(
        f"query: {elapsed:.1f}s wall, mode={parsed.get('mode')}, "
        f"answer={len(parsed.get('answer', ''))} chars"
    )
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

    print("\nSMOKE PASS: serve_rag healthy, query fast, reranker on GPU.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
