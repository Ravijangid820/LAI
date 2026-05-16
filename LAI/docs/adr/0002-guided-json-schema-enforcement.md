# 0002 — `lai.common.llm`: guided-JSON schema enforcement via vLLM

- **Status:** **Superseded by [ADR 0004](./0004-response-format-supersedes-guided-json.md)** (2026-05-16).
  The primary mechanism described here — `extra_body.guided_json` — was
  found by the integration test to be **silently ignored** by the live
  `vllm/vllm-openai:latest` build. The OpenAI-standard `response_format`
  is now the primary; this ADR is retained for historical reasoning.
- **Date:** 2026-05-16
- **Owner:** `lai.common.llm`

## Context

The DDiQ engine makes ~45 LLM calls per report; **eight** of those are
single points of failure for an entire report chapter (findings, timeline,
cross-doc consistency, Rückbau bond, Grundbuch match, WEA status,
infrastructure, metadata). The audit found that each fails when the LLM
returns malformed JSON — empty content, code-fence noise, trailing prose,
unmatched braces.

At a generous per-call success rate `p = 0.97`, `p^8 ≈ 0.78` — **roughly
22% of reports lose a whole chapter** to JSON fragility alone, before
counting network or retrieval errors. The smoke-test report's six
failures are consistent with this math.

Today the codebase relies on:

- `ddiq_report.py:516-523` — one retry with a stricter prompt
  (*"CRITICAL: Return ONLY valid JSON."*); on second failure, the bare
  `json.loads` propagates and crashes the phase.
- Light JSON salvage (strip Markdown code fences only).
- No structural enforcement — the model can return any string.

vLLM (which we already run) supports **structured output via guided
decoding**. Two backends are available — `outlines` and `xgrammar` —
both ship with the `vllm/vllm-openai` image we run. The client API is the
OpenAI-compatible `extra_body` field:

```jsonc
{
  "model": "qwen3.6-27b",
  "messages": [...],
  "extra_body": {
    "guided_json": <JSON Schema>,
    "guided_decoding_backend": "xgrammar"
  }
}
```

When `guided_json` is set, the sampler masks logits at every token so the
emitted text is **guaranteed to satisfy the schema** — the model cannot
produce invalid JSON.

The `lai.analyzer` package already uses this pattern; DDiQ does not. We
are adopting it across the new shared `LlmClient`.

## Decision

`LlmClient.generate_json(schema: type[T], ...) -> T` derives the JSON
schema from the supplied Pydantic model via `schema.model_json_schema()`,
passes it to vLLM as `extra_body.guided_json`, parses the (now
schema-guaranteed) response, and returns a validated instance of `T`.

Fallback chain when guided decoding is unavailable (e.g. against a model
endpoint without xgrammar/outlines, in a unit test against a fake server,
or when the schema is rejected with HTTP 400):

1. Re-issue without `guided_json` but with
   `response_format={"type": "json_object"}` (looser).
2. Apply `lai.common.llm.json_salvage` (brace-balanced repair).
3. Validate against the Pydantic schema.
4. On final failure, raise `LlmSchemaValidationError` — the caller decides
   whether to fall through to a typed empty.

## Consequences

- Structurally eliminates the "malformed JSON" failure class for callers
  that use `generate_json`. The chapter-loss rate collapses toward zero
  for the eight SPOF passes.
- One canonical schema per call — vLLM cannot enforce two shapes at once.
  Multi-shape outputs (e.g. "either a finding *or* a refusal") have to be
  expressed as a single discriminated-union schema.
- Slight per-call throughput cost (single-digit percent in published
  benchmarks; we will measure on Qwen3.6-27B).
- Pydantic 2 is required: `model_json_schema()` is the modern API.
- The fallback chain means a development/test environment without a
  vLLM-grade backend still works — `pytest` against a mock returns parseable
  JSON that the same code path validates.

## Alternatives considered

- **`response_format={"type": "json_object"}` only** (OpenAI's loose
  JSON mode). Forces *some* JSON but not the right shape — the failure
  surface drops but doesn't collapse, and we still need a salvage path.
  Rejected as primary; kept as the first fallback.
- **Post-hoc Pydantic validation only** (what the codebase does today).
  Patches the symptom (parse failure) but not the cause (model emitting
  invalid JSON). This is what produced the 22% chapter-loss rate. Rejected.
- **External JSON-repair library** (`json_repair`, `dirtyjson`). Patches
  the symptom further down the chain. Useful as a salvage step (we will
  consider it for `json_salvage`) but not a substitute for structural
  enforcement.
- **Grammar-constrained generation via raw `outlines.generate`.** Same
  guarantee but couples us to `outlines` directly instead of vLLM's
  abstraction. Rejected because vLLM's `extra_body.guided_json` lets us
  switch backends (xgrammar vs outlines) by config without changing client
  code.
