# 0003 — `lai.common.llm`: strip `<think>` traces server-side

- **Status:** Accepted
- **Date:** 2026-05-16
- **Owner:** `lai.common.llm`

## Context

Qwen3.6-27B (the live `lai_analyzer_llm`) runs in **thinking mode** and
emits a structured reasoning trace before the answer:

```
<think>
The user is asking about Rückbauverpflichtung under § 35 BauGB.
Let me check the supplied contract for the bond clause...
</think>
The Rückbauverpflichtung under § 35 Abs. 5 BauGB requires...
```

The `<think>` block is **not** part of the user-visible answer. Three
problems with how the codebase handles it today:

1. `serve_rag.py:406` (`_strip_reasoning_trace`) and
   `analyzer/llm_client.py:39` (`_strip_thinking`) each implement their own
   stripper. Two near-duplicate regexes.
2. `ddiq_report.py` does **not** strip at all. The trailing `</think>...`
   text often leaves stray characters that break `json.loads` —
   contributing to the chapter-loss rate covered in ADR 0002.
3. Disabling thinking mode globally is not an option: the V2 contract
   analyzer relies on chain-of-thought for the qualitative reasoning that
   distinguishes its output from a one-shot answer
   (`micro-services/.env` confirms thinking mode is intentionally on).

We need one strip implementation, used by every caller, that defaults to
"clean" and lets the rare debug caller see the raw text.

## Decision

`LlmClient` **strips `<think>...</think>` blocks server-side by default**.
A `keep_thinking: bool = False` parameter on `generate()` and
`generate_json()` exposes the raw text for the cases that need it.

The stripper lives in `lai.common.llm.think_strip` as a pure function with
property-based tests:

```python
def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks, including the closing tag.

    Robust to: unclosed blocks (model truncated mid-trace), nested blocks
    (rare but seen), and whitespace before the answer. Returns the
    answer-only text.
    """
```

The structured logger records the raw token count and the post-strip token
count as a single log field, so we can monitor "wasted thinking" cost
without re-running calls in debug mode.

## Consequences

- Every caller — `serve_rag`, DDiQ, the analyzer, future modules — sees
  clean answers from one stripper. The current 3× duplication (with one
  copy missing) collapses to one implementation.
- DDiQ's JSON parses get materially more reliable because the leading
  garbage that was breaking `json.loads` is gone before parsing.
- The minority of callers that want the raw text (the analyzer's
  trace-log path, debugging tools) pass `keep_thinking=True` explicitly —
  the intent is visible at the call site.
- Tokens used for the thinking trace are still billed; we observe them via
  the logger field and can decide later whether to disable thinking mode
  per-call for the cheap structured-extraction passes (likely a later ADR).

## Alternatives considered

- **Pass-through; let every caller strip.** This is what we have today and
  it failed: one of the three callers forgets, the JSON breaks, and the
  bug is rediscovered when the report chapter goes missing. Rejected.
- **Disable thinking mode globally** via
  `extra_body.chat_template_kwargs={"enable_thinking": False}`. Faster
  generation, but the V2 analyzer's quality drops measurably without it.
  Rejected as a *default*; remains a per-call toggle a future ADR may
  apply to the structured-extraction passes only.
- **Strip at the streaming layer (in `/query`)** rather than in
  `LlmClient`. Couples the strip to streaming, which not every caller uses.
  Rejected.
- **Use vLLM's `reasoning_parser="qwen3"` server-side** (vLLM ≥ 0.9 has a
  `--reasoning-parser` flag that splits reasoning into a separate response
  field). This is the cleanest option and we will adopt it *once we
  confirm our deployed vLLM build exposes it* — the current container's
  flag set hasn't been audited. The client-side stripper here is the
  always-works baseline that does not depend on a specific vLLM build.
  A future ADR can supersede this one once vLLM-side splitting is
  confirmed.
