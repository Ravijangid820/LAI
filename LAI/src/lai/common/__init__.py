"""Shared production-grade primitives for LAI's backends.

`lai.common` is the single home for code that ``serve_rag``, the DDiQ
microservice, the data pipeline, and any future module need to share. It
exists so that bug fixes land *once* instead of being copy-pasted across
three codebases.

Everything in this package is held to the strict quality gate documented in
:doc:`/CONTRIBUTING`:

- ``mypy --strict`` clean.
- Full ruff rule set.
- ≥ 85% line + branch coverage on every module.
- A bandit security scan with no findings.

Modules are added incrementally — see ``LAI/docs/adr`` for the architecture
decisions that motivated each.
"""

from __future__ import annotations

__all__: list[str] = []

# Package version is independent of the top-level ``lai`` distribution so the
# shared primitives can evolve with their own semver cadence once consumed by
# external callers.
__version__: str = "0.1.0"
