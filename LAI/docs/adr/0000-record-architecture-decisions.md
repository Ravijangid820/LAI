# 0000 — Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-05-16

## Context

The LAI v1 build is held to a single production-grade quality bar
(see `CONTRIBUTING.md`). That bar relies on future readers (us in six
months, new teammates, auditors) being able to reconstruct *why* the code
looks the way it does. Inline comments cover the *what*; commit messages
cover the *change*; neither is the right place for a multi-paragraph
justification of a non-obvious design choice.

The codebase has already accumulated two cases where this hurt: the
SQLite-as-prod-corpus choice ("drift, not design", per the audit) and the
`EXTERNAL_LAW_REFS` regex gate (which the audit treated as a bug for a day
before re-reading the code revealed it was intentional). In both cases an
ADR at the moment of the original decision would have saved hours.

## Decision

We adopt [MADR][madr] (Markdown Architectural Decision Records), stored in
`LAI/docs/adr/`. One ADR per decision. Numbered sequentially with no gaps.
Trimmed to four sections: Context, Decision, Consequences, Alternatives
Considered.

We write an ADR when the answer to *"why is the code like this?"* requires
more than an inline comment.

## Consequences

- New developers can read the ADR index and understand the architecture's
  load-bearing choices without spelunking commit history.
- Reviewers can challenge a design by writing a superseding ADR rather than
  arguing in PR comments.
- We will accumulate dead ADRs (superseded ones) — that is intentional. We
  link both directions; we never edit a historical ADR to reflect a new
  reality.

## Alternatives considered

- **No ADRs, rely on commit messages and code comments.** Rejected because
  the audit demonstrated the cost: design intent gets lost.
- **A `DESIGN.md` master document.** Rejected because monolithic design docs
  rot — every change requires editing one file, merge conflicts proliferate,
  and the historical reasoning for an *earlier* decision gets overwritten.
  ADRs are append-only.
- **Confluence / Notion / external wiki.** Rejected because the decisions
  belong with the code that implements them. External wikis go stale.

[madr]: https://adr.github.io/madr/
