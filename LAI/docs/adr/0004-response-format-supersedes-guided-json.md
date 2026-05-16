# 0004 — Use OpenAI-standard `response_format` for structured output; supersede ADR 0002's primary mechanism

- **Status:** Accepted
- **Date:** 2026-05-16
- **Owner:** `lai.common.llm`
- **Supersedes:** [ADR 0002](./0002-guided-json-schema-enforcement.md)'s
  primary mechanism (`extra_body.guided_json`). The *fallback* path in
  ADR 0002 (`response_format: {"type": "json_object"}` + salvage_json) is
  retained as the secondary fallback in this ADR.

## Context

ADR 0002 specified `extra_body.guided_json` (with `guided_decoding_backend:
"xgrammar"`) as the primary way to enforce JSON-schema output from the
LLM. The integration test built in `tests/integration/common/llm/
test_client_live.py` against the **live** `lai_analyzer_llm` endpoint
(`vllm/vllm-openai:latest`, served with
`--model Qwen/Qwen3.6-27B --reasoning-parser qwen3
--enable-prefix-caching`) discovered that **`guided_json` is silently
ignored** on this build:

| Request body | Response |
|--------------|----------|
| `{"extra_body": {"guided_json": {…}}, …}` | unconstrained prose ("Germany: Berlin") |
| `{"guided_json": {…}, …}` (top-level) | unconstrained prose ("Germany: Berlin") |
| `{"response_format": {"type": "json_schema", "json_schema": {…}}, …}` | valid JSON matching the schema |
| `{"response_format": {"type": "json_object"}, …}` | valid JSON (loose) |

The `guided_json` parameter does not error — it does not return HTTP 400,
it does not log a warning — the request simply produces unstructured text.
This is the worst kind of "silent feature-flag absent" failure mode.

The probes that produced the table above were issued directly via `curl`
in the same session that discovered the bug, against the live container,
so the result is reproducible and not a transport-layer artifact.

Two unit-test mocks against `guided_json` had given us false confidence —
they were checking that our code *sent* the field, not that vLLM *honoured*
it.

## Decision

The `lai.common.llm.client.LlmClient` switches its primary
structured-output mechanism from `extra_body.guided_json` to the
**OpenAI-standard `response_format`**:

```jsonc
{
  "model": "qwen3.6-27b",
  "messages": [...],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "<Pydantic model name>",
      "schema": <model.model_json_schema()>,
      "strict": true
    }
  }
}
```

The fallback chain becomes:

1. **Primary**: `response_format: {"type": "json_schema", ...}` —
   strict-mode structural enforcement.
2. **Fallback A** (on HTTP 400 from primary): `response_format:
   {"type": "json_object"}` — loose JSON-mode; the schema is *not*
   enforced server-side, so we still run :func:`salvage_json` +
   Pydantic validation locally.
3. **Fallback B** (if Fallback A still produces malformed JSON):
   :class:`~lai.common.exceptions.LlmJsonParseError` /
   :class:`~lai.common.exceptions.LlmSchemaValidationError` — the
   caller decides whether to fall through to a typed empty.

`extra_body.guided_json` is removed from the request body entirely. The
`guided_decoding_backend` config field is also removed — it has no
referent in the new design. (We will not delete the field from
`LlmConfig` in this ADR's change set to keep the migration narrow; it
becomes a no-op and is removed in a later cleanup once no callers
import it.)

## Consequences

### What gets better

- Structured output actually works on the live endpoint. The integration
  tests that were failing pass after this change.
- The mechanism is OpenAI-standard, so a future migration to a different
  inference backend (vLLM → SGLang, OpenAI Cloud, etc.) does not require
  changing the client API.
- The fallback chain still works: `json_object` mode + `salvage_json` +
  Pydantic validation handles the case where the primary mechanism is
  rejected (e.g., a future build that doesn't support `json_schema`
  yet).

### What gets worse / what we accept

- We lose the *only* part of `guided_json` that was unambiguously
  better than `response_format`: vLLM's guided decoding (xgrammar) is
  formally token-level constrained, whereas `response_format` relies on
  the model's training to satisfy the schema. In practice, the
  `response_format: json_schema` path with `strict: true` produces
  valid JSON nearly always on Qwen3.6-27B — but we do *not* have the
  formal guarantee that ADR 0002 promised.
- Some schemas that `xgrammar` would reject (recursive, complex
  `$ref` chains) might be accepted by `json_schema` mode but produced
  with non-strict adherence. The Pydantic validation step is the
  backstop.

### What unit tests must change

The unit tests in `tests/unit/common/llm/test_client.py` that asserted on
the *shape* of the request body — specifically checking
`body["extra_body"]["guided_json"]` and
`body["extra_body"]["guided_decoding_backend"]` — must move to checking
`body["response_format"]["type"] == "json_schema"` and
`body["response_format"]["json_schema"]["schema"]`. The mocks that
returned valid JSON regardless of the request continue to work; the
assertions about *what we send* change.

### Future supersession

If a later vLLM build (or a different inference backend we adopt)
exposes a more strictly-enforced mechanism — vLLM's `guided_json` if
it becomes honoured, an `xgrammar`-backed mode with a different
parameter name, the as-yet-future `response_format: structured_output`
— a new ADR supersedes this one. The supersession contract is the
same: a new ADR, link both directions, do not edit the historical
reasoning here.

## Alternatives considered

- **Keep `extra_body.guided_json` and reject the live endpoint.**
  Rejected because the entire program is committed to running on
  this specific vLLM build (on-prem, no further budget per the
  locked constraints). Changing inference servers is a much larger
  decision than swapping a request parameter.
- **Switch to direct `outlines.generate` calls** outside the vLLM
  server. Rejected because it bypasses vLLM's batch scheduler — we
  lose `--max-num-seqs` concurrency and `--enable-prefix-caching`
  optimisations, both of which matter for the DDiQ workload.
- **Stay on `extra_body.guided_json` and hope a future vLLM build
  honours it.** Rejected: silently broken is worse than explicitly
  switched. The OpenAI-standard mechanism is also more portable.
- **Add a runtime probe** at LlmClient startup that detects which
  mechanism the endpoint honours and chooses dynamically. Rejected
  for v1: extra complexity for a one-server deployment. Worth
  revisiting if we ever support multiple inference backends.
