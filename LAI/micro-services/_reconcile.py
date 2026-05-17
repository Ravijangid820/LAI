"""Deterministic reconciler for cross-source value disagreements in DDiQ.

Track A item 4. The DDiQ pipeline extracts the same physical fact
(``total_capacity_mw``, ``turbine_count``, ``bundesland``, …) from
multiple sources — a regex on the overview cell, an LLM extraction of
the same field elsewhere, a sum derived from per-WEA attributes, a
bbox derivation from coordinates. The sources disagree often enough
that the same report has historically shown four different turbine
counts in four different sections (the failure mode the strategy doc
calls "four-conflicting-turbine-counts").

This module gives each downstream consumer ONE canonical value per
field, with a deterministic precedence rule plus an audit trail of
rejected candidates. Precedence is fixed across the pipeline:

    cadastral > llm > regex > fallback

Rationale: cadastral data comes from authoritative public registries
(ALKIS, MaStR, the project's own measured coords); the LLM has read
the actual documents and can quote a clause; a regex sees only the
shape it was told to look for; "fallback" is a hard-coded default
when everything else returned ``None``.

Module-local (``micro-services/_reconcile.py``) rather than shared in
``lai.common`` because the precedence values here are tuned for
DDiQ's source set. If a second consumer (e.g. ``serve_rag``) needs
the same pattern, promote then.

Design notes
------------

- ``Candidate`` is intentionally flat and frozen — one ``value`` per
  source, no nested fan-out. Callers building multiple-source values
  (e.g. summing per-WEA capacity to get a total) compute outside and
  hand the scalar to a single ``Candidate``.
- ``reconcile_numeric`` and ``reconcile_categorical`` both accept a
  list of candidates and return ``Reconciled | None``. ``None`` is the
  honest answer when every candidate was ``None`` — never invent a
  zero or a fallback value silently; the caller decides what to do
  in the empty case.
- Divergence logging is at WARNING level so it shows up in the
  operator's pipeline logs without being noise on the happy path. The
  caller can read ``Reconciled.rejected`` if it wants to attach the
  full audit trail to a finding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

__all__ = [
    "Candidate",
    "Provenance",
    "Reconciled",
    "reconcile_categorical",
    "reconcile_numeric",
]

logger = logging.getLogger("ddiq.reconcile")

Provenance = Literal["cadastral", "llm", "regex", "fallback"]

# Lower is better. Order is the contract — change only via review.
_PRECEDENCE: dict[str, int] = {
    "cadastral": 0,
    "llm": 1,
    "regex": 2,
    "fallback": 3,
}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One source's value for a reconciled field.

    Attributes:
        value: The value itself. ``None`` means "this source had nothing
            to say" — :func:`reconcile_numeric` / :func:`reconcile_categorical`
            drop ``None``-valued candidates before sorting, so it's safe
            to construct candidates eagerly and let the reconciler decide.
        provenance: One of ``"cadastral" | "llm" | "regex" | "fallback"``.
            Drives precedence; see module docstring.
        confidence: Tie-breaker within the same precedence tier.
            ``1.0`` for "I'm sure"; lower values for heuristic-derived
            sources. Most callers leave this at the default.
        source: Human-readable description of where the value came from
            (e.g. ``"overview.Total Capacity regex"``,
            ``"sum(weas.rated_power_kw)"``). Used in the divergence log
            so an operator can trace any disagreement back to the line
            that produced it.
    """

    value: Any = None
    provenance: Provenance = "fallback"
    confidence: float = 1.0
    source: str = ""


@dataclass(frozen=True, slots=True)
class Reconciled:
    """The winning value + the rejected candidates.

    Attributes:
        value: The winning ``Candidate.value`` — convenience accessor so
            callers that don't care about the audit trail can use it
            directly: ``reconciled.value if reconciled else None``.
        winner: The full winning ``Candidate``.
        rejected: All other non-None candidates, ordered as they would
            have appeared if the winner were removed — useful when
            attaching a "we ignored these other readings: …" note to a
            downstream finding.
    """

    value: Any
    winner: Candidate
    rejected: tuple[Candidate, ...] = field(default_factory=tuple)


def _drop_nones(candidates: list[Candidate]) -> list[Candidate]:
    """Filter candidates whose value is ``None``."""
    return [c for c in candidates if c.value is not None]


def _sort_by_precedence(candidates: list[Candidate]) -> list[Candidate]:
    """Sort by precedence (lower first), then by confidence (higher first).

    Python's sort is stable, so ties beyond those two keys keep the
    caller's original ordering — useful when two sources are genuinely
    equivalent and the caller wants insertion order to break the tie.
    """
    return sorted(
        candidates,
        key=lambda c: (_PRECEDENCE.get(c.provenance, 99), -c.confidence),
    )


def reconcile_numeric(
    name: str,
    candidates: list[Candidate],
    *,
    abs_tol: float = 1e-6,
    rel_tol: float = 0.02,
) -> Optional[Reconciled]:
    """Pick a canonical numeric value, warning on meaningful divergence.

    Args:
        name: Field name used in divergence log lines so operators can
            grep (e.g. ``"total_capacity_mw"``).
        candidates: List of candidates. ``None``-valued ones are dropped.
        abs_tol: Absolute tolerance for the divergence-warning check;
            differences below this are silent.
        rel_tol: Relative tolerance (fraction of the winner) for the
            divergence-warning check. ``0.02`` = 2 %; typical for
            wind-DD numerics where a per-mille mismatch in capacity is
            rounding noise but a 5 % gap is a real disagreement worth
            surfacing.

    Returns:
        ``Reconciled`` with the winner + the rejected tail, or ``None``
        if every candidate was ``None``.
    """
    cands = _drop_nones(candidates)
    if not cands:
        return None

    sorted_cands = _sort_by_precedence(cands)
    winner = sorted_cands[0]
    rejected = tuple(sorted_cands[1:])

    # Divergence check — only meaningful for numeric winners. Skip
    # silently if the winner can't be coerced to float; logging an
    # error here would be noise (the winner is still returned).
    try:
        w_val = float(winner.value)
        scale = max(abs(w_val), 1.0)
        for r in rejected:
            try:
                r_val = float(r.value)
            except (TypeError, ValueError):
                continue
            diff = abs(r_val - w_val)
            if diff > abs_tol and diff / scale > rel_tol:
                logger.warning(
                    "reconcile.%s: candidate %s=%r (provenance=%s, conf=%.2f) "
                    "diverges from winner %s=%r (provenance=%s); ignored.",
                    name,
                    r.source or "?", r.value, r.provenance, r.confidence,
                    winner.source or "?", winner.value, winner.provenance,
                )
    except (TypeError, ValueError):
        # winner is non-numeric — skip divergence checking, still return it
        pass

    return Reconciled(value=winner.value, winner=winner, rejected=rejected)


def reconcile_categorical(
    name: str,
    candidates: list[Candidate],
) -> Optional[Reconciled]:
    """Pick a canonical categorical value (e.g. Bundesland name).

    Same precedence rule as :func:`reconcile_numeric`. Divergence is any
    non-equal value among the rejects — logged at WARNING level.

    Args:
        name: Field name for the divergence log.
        candidates: List of candidates. ``None``-valued ones are dropped.

    Returns:
        ``Reconciled`` or ``None`` if every candidate was ``None``.
    """
    cands = _drop_nones(candidates)
    if not cands:
        return None

    sorted_cands = _sort_by_precedence(cands)
    winner = sorted_cands[0]
    rejected = tuple(sorted_cands[1:])

    for r in rejected:
        if r.value != winner.value:
            logger.warning(
                "reconcile.%s: candidate %s=%r (provenance=%s, conf=%.2f) "
                "diverges from winner %s=%r (provenance=%s); ignored.",
                name,
                r.source or "?", r.value, r.provenance, r.confidence,
                winner.source or "?", winner.value, winner.provenance,
            )

    return Reconciled(value=winner.value, winner=winner, rejected=rejected)
