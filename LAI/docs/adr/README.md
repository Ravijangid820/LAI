# Architecture Decision Records (ADRs)

This directory captures non-obvious design choices made during the LAI v1
build. One file per decision. We use [MADR][madr] form
(Markdown Architectural Decision Records), trimmed to the minimum:

- **Context** — what forced the decision.
- **Decision** — what we are doing.
- **Consequences** — what becomes true now (good and bad).
- **Alternatives considered** — what we rejected and why.

An ADR is short — half a page is normal. It is a *snapshot at the moment of
the decision*, not a living document. If the decision is revisited, write a
new ADR that supersedes the old one (link both directions); do **not** edit
the original to reflect the new state.

## Index

| # | Title | Status |
|---|-------|--------|
| 0000 | [Record architecture decisions](./0000-record-architecture-decisions.md) | Accepted |
| 0001 | [`lai.common.llm` — async-primary client surface](./0001-llm-client-async-primary.md) | Accepted |
| 0002 | [`lai.common.llm` — guided-JSON schema enforcement via vLLM](./0002-guided-json-schema-enforcement.md) | Accepted |
| 0003 | [`lai.common.llm` — strip `<think>` traces server-side](./0003-think-trace-server-side-stripping.md) | Accepted |

## Numbering

Files are numbered `NNNN-kebab-case-title.md` starting at `0000`. The next
ADR gets the next free integer; no gaps, no renumbering after merge.

## When to write one

Write an ADR when the answer to "why is the code like this?" requires more
than the code itself can show. Typical triggers:

- A choice between two reasonable approaches where the trade-offs are not
  obvious from the diff.
- A decision driven by a constraint that is not in the codebase (legal,
  business, infrastructure).
- A pattern that we will deliberately apply elsewhere (so future readers
  recognise it as intentional, not coincidence).

If a single inline comment in the code is enough, write that instead.

[madr]: https://adr.github.io/madr/
