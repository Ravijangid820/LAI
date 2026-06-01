"""Populate ``model_a_answer`` / ``model_b_answer`` in ``eval_questions/bimschg_50.jsonl``.

Operational counterpart to ``vm-9`` (LAI/micro-services/eval_api.py) — the eval API
loads the JSONL once on startup and only ever READS the two answer fields; this
script is what makes those fields non-empty before the lawyer-blind session runs.

WHY a separate offline step (rather than the eval API querying the models live):

    The lawyer-blind session must be deterministic and reproducible:
      * The lawyer can pause and resume across days; the answers shown for
        question ``q12`` must never change between sessions.
      * If vLLM is restarted (e.g. for the BM25 perf patch) mid-session the
        eval would otherwise return a different answer for the same prompt.
      * Greedy decode against a hot KV-cache is not the same as greedy decode
        against a cold one — minor numerical drift is real.
    Pre-generating once, writing to disk, and reading from disk completely
    decouples the labelling session from the model-serving uptime.

Usage (typical Phase-3 §3.4 invocation — base Qwen on :8005, LoRA on :8006):

    cd /data/projects/lai/LAI
    ./.venv/bin/python scripts/eval/generate_eval_answers.py \\
        --model-a-url   http://localhost:8005 \\
        --model-a-name  qwen3.6-27b \\
        --model-b-url   http://localhost:8006 \\
        --model-b-name  qwen3.6-27b-lai-bimschg \\
        --enable-thinking off

Env-var overrides (cleaner for ops scripts):
    LAI_EVAL_MODEL_A_URL / LAI_EVAL_MODEL_A_NAME
    LAI_EVAL_MODEL_B_URL / LAI_EVAL_MODEL_B_NAME

Re-running is safe by default: rows whose ``model_a_answer`` is already non-empty
are left alone; same for ``model_b_answer``. Pass ``--force`` to overwrite.

Exit codes:
    0  ok
    2  bad CLI / input file missing / required env unset
    3  endpoint failure (4xx/5xx) on enough rows that we bailed early
    4  malformed JSONL or write failure

Stdlib only (urllib + json + argparse) — no third-party deps so it can run from
``./.venv/bin/python`` or system Python equally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LAI_DIR = Path(__file__).resolve().parents[2]
DEFAULT_JSONL = LAI_DIR / "eval_questions" / "bimschg_50.jsonl"

# How many consecutive endpoint failures before we abort the run rather than
# silently produce a half-populated file. Per side, not global.
MAX_CONSECUTIVE_FAILURES = 5


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input",
        default=str(DEFAULT_JSONL),
        help="Path to bimschg_50.jsonl (input AND output — atomic-replaced).",
    )

    # --- model A ---
    p.add_argument(
        "--model-a-url",
        default=os.environ.get("LAI_EVAL_MODEL_A_URL"),
        help="Base URL of the OpenAI-compatible API for model A (e.g. http://localhost:8005). "
             "Env: LAI_EVAL_MODEL_A_URL.",
    )
    p.add_argument(
        "--model-a-name",
        default=os.environ.get("LAI_EVAL_MODEL_A_NAME"),
        help="`model` field for model A's chat.completions requests. Env: LAI_EVAL_MODEL_A_NAME.",
    )

    # --- model B ---
    p.add_argument(
        "--model-b-url",
        default=os.environ.get("LAI_EVAL_MODEL_B_URL"),
        help="Base URL of the OpenAI-compatible API for model B. Env: LAI_EVAL_MODEL_B_URL.",
    )
    p.add_argument(
        "--model-b-name",
        default=os.environ.get("LAI_EVAL_MODEL_B_NAME"),
        help="`model` field for model B's chat.completions requests. Env: LAI_EVAL_MODEL_B_NAME.",
    )

    # --- only-one-side flags (useful when staging) ---
    p.add_argument("--skip-a", action="store_true", help="Don't query model A at all this run.")
    p.add_argument("--skip-b", action="store_true", help="Don't query model B at all this run.")

    # --- generation knobs (match the §3.4 production-realistic eval) ---
    p.add_argument("--max-new-tokens", type=int, default=768,
                   help="Max completion tokens per question (default 768 — German legal answers "
                        "need headroom; the eval API shows the full answer in a scrolling card).")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="0 = greedy; the default. Keep at 0 unless explicitly studying variance.")
    p.add_argument("--enable-thinking", choices=("default", "on", "off"), default="off",
                   help="Qwen3 chat-template thinking-mode toggle, passed as "
                        "chat_template_kwargs.enable_thinking. 'off' for the lawyer eval — "
                        "matches what the retention-probe baseline was computed with and keeps "
                        "lawyer-visible answers free of <think> traces.")
    p.add_argument("--timeout-s", type=float, default=180.0,
                   help="HTTP timeout per request (default 180s — a long legal answer with "
                        "thinking-off greedy decode is typically <60s but we leave headroom).")

    p.add_argument("--force", action="store_true",
                   help="Overwrite already-populated rows. Default: skip rows whose answer is non-empty.")
    p.add_argument("--quiet", action="store_true", help="Suppress per-row progress lines.")
    return p.parse_args()


def _build_body(
    *,
    question: str,
    model: str,
    max_tokens: int,
    temperature: float,
    enable_thinking: str,
) -> dict[str, Any]:
    """Construct the OpenAI-compatible chat.completions request body.

    chat_template_kwargs is the vLLM-side path for Qwen3's enable_thinking
    toggle — matches how retention_probe.py threads the same flag through.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Greedy by default; explicit top_p=1 in case the server defaults to
        # nucleus sampling for non-zero temperature elsewhere.
        "top_p": 1.0,
    }
    if enable_thinking == "on":
        body["chat_template_kwargs"] = {"enable_thinking": True}
    elif enable_thinking == "off":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


def _post_chat(url: str, body: dict[str, Any], timeout_s: float) -> tuple[bool, str]:
    """POST to {url}/v1/chat/completions. Return (ok, content_or_error_msg)."""
    full = url.rstrip("/") + "/v1/chat/completions"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        full,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")[:400]
        return False, f"HTTP {e.code}: {body_txt}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"network: {e}"
    try:
        d = json.loads(raw)
        content = d["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return False, f"unparseable response: {e}: {raw[:200]}"
    return True, content.strip()


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"ERROR: malformed JSONL at line {i + 1}: {e}", file=sys.stderr)
            sys.exit(4)
    return rows


def _atomic_write(path: Path, rows: list[dict[str, Any]]) -> None:
    """Sibling .tmp + rename — never leave the canonical file in a half-written state."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def _generate_side(
    *,
    rows: list[dict[str, Any]],
    answer_field: str,
    url: str,
    model: str,
    args: argparse.Namespace,
    side_label: str,
) -> tuple[int, int]:
    """Populate one side (``model_a_answer`` OR ``model_b_answer``) across all rows.

    Returns (filled, skipped). Aborts with exit 3 if we hit ``MAX_CONSECUTIVE_FAILURES``.
    """
    filled = 0
    skipped = 0
    consecutive_fail = 0
    t_run = time.time()
    for i, row in enumerate(rows):
        rid = row.get("id", f"#{i}")
        existing = (row.get(answer_field) or "").strip()
        if existing and not args.force:
            skipped += 1
            continue
        body = _build_body(
            question=row["question"],
            model=model,
            max_tokens=args.max_new_tokens,
            temperature=args.temperature,
            enable_thinking=args.enable_thinking,
        )
        t0 = time.time()
        ok, content = _post_chat(url, body, args.timeout_s)
        dt = time.time() - t0
        if not ok:
            consecutive_fail += 1
            print(
                f"  [{side_label} {rid}] FAIL after {dt:.1f}s: {content}",
                file=sys.stderr,
                flush=True,
            )
            if consecutive_fail >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"\nABORT: {MAX_CONSECUTIVE_FAILURES} consecutive failures on side "
                    f"{side_label}. Endpoint at {url} may be down. Re-run when fixed.",
                    file=sys.stderr,
                )
                # Still write what we have so the run isn't wasted.
                _atomic_write(Path(args.input), rows)
                sys.exit(3)
            continue
        consecutive_fail = 0
        row[answer_field] = content
        filled += 1
        if not args.quiet:
            print(
                f"  [{side_label} {rid}] {dt:5.1f}s  {len(content):>5} chars",
                flush=True,
            )
    print(
        f"  [{side_label}] filled={filled}  skipped={skipped}  total_time={time.time() - t_run:.1f}s",
        flush=True,
    )
    return filled, skipped


def main() -> int:
    args = _parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"ERROR: input file not found: {in_path}", file=sys.stderr)
        return 2

    # Which sides are we doing? Validate up-front so we don't load the file just
    # to then bail at the first row.
    sides: list[tuple[str, str, str | None, str | None]] = []
    if not args.skip_a:
        sides.append(("A", "model_a_answer", args.model_a_url, args.model_a_name))
    if not args.skip_b:
        sides.append(("B", "model_b_answer", args.model_b_url, args.model_b_name))
    if not sides:
        print("ERROR: both --skip-a and --skip-b set — nothing to do.", file=sys.stderr)
        return 2
    for label, _, url, name in sides:
        if not url or not name:
            print(
                f"ERROR: side {label} needs both --model-{label.lower()}-url and "
                f"--model-{label.lower()}-name (or the env-var equivalents).",
                file=sys.stderr,
            )
            return 2

    rows = _load_rows(in_path)
    print(
        f"Loaded {len(rows)} questions from {in_path}\n"
        f"  enable_thinking={args.enable_thinking}  max_new_tokens={args.max_new_tokens}  "
        f"temperature={args.temperature}\n"
        f"  force={args.force}",
        flush=True,
    )

    for label, field, url, name in sides:
        print(f"\n=== Generating side {label}: {name} @ {url} ===", flush=True)
        # type-narrow url/name (already validated above)
        assert url is not None and name is not None
        _generate_side(
            rows=rows,
            answer_field=field,
            url=url,
            model=name,
            args=args,
            side_label=label,
        )

    _atomic_write(in_path, rows)
    print(f"\nWrote {in_path}")
    # Tiny on-disk summary so the eval API health-line can pick it up later.
    nonempty_a = sum(1 for r in rows if (r.get("model_a_answer") or "").strip())
    nonempty_b = sum(1 for r in rows if (r.get("model_b_answer") or "").strip())
    print(f"  rows with model_a_answer: {nonempty_a}/{len(rows)}")
    print(f"  rows with model_b_answer: {nonempty_b}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
