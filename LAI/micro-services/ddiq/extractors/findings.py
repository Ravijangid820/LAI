"""Findings chapter — evidence-aware per-row LLM pass.

Builds the FINDINGS chapter one entry at a time: for every red/yellow
row in the analysed Ausgabeblatt sections we run a dedicated LLM
call that returns ``{domain, severity, text, legal_basis,
recommended_action, quantification}``. Evidence pointers come from
the row's ``evidence`` field (attached upstream during
section analysis) rather than re-querying the LLM, so the prompt
stays narrow and the evidence stays grounded.

The per-row design (Track A item 2) replaces the historical single
batched call. The old shape was fragile: if the LLM emitted a
malformed array OR its response was truncated mid-element, the
entire ``llm_json`` parse would fail and the whole chapter was
lost ("Manual review required" placeholder for everything). Per-
row, one bad response loses ONE finding (placeholder in its slot)
and the rest come through cleanly.

Cost: N× more LLM calls. Measured single-call latency against the
live ``lai_analyzer_llm`` (Qwen3.6-27B in thinking-mode) is
~120-150s for a realistic findings prompt — substantially higher
than a chat completion because the model reasons through the legal
basis + quantification before emitting JSON. For a 10-row report
that's ~20-25 min of extra wall-time on top of the existing
multi-minute pipeline. The reliability win outweighs this, but two
follow-ups can claw most of it back if latency starts mattering
for live demos:

1. Parallelise via ``concurrent.futures.ThreadPoolExecutor`` over a
   single shared :class:`SyncLlmClient` (its underlying
   ``httpx.Client`` is thread-safe). The analyzer LLM container
   still serialises GPU-side, but pipeline overlap + HTTP
   concurrency typically cut total wall-time 30-50%.
2. Disable thinking-mode for the findings pass
   (``keep_thinking=False`` +
   ``LlmConfig(thinking_mode_enabled=False)``) since the prompt is
   narrow enough that we don't need the reasoning trace —
   typically halves per-call latency.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ddiq.llm import EXTRACTION_SYSTEM, llm_json
from ddiq.models import (
    AusgabeblattSection,
    Evidence,
    Finding,
    Quantification,
)

__all__ = [
    "_finding_from_llm_obj",
    "_findings_prompt_for_issue",
    "_placeholder_finding_for_issue",
    "generate_findings",
]


_log = logging.getLogger("ddiq")

# E1: default fan-out for the per-row findings calls. vLLM batches
# concurrent requests on the GPU, so issuing them in parallel cuts the
# findings phase's wall-time well below the sequential sum. 4 is
# conservative against the analyzer's max-concurrent-sequences; override
# via env if the serving capacity changes.
_FINDINGS_DEFAULT_WORKERS = int(os.getenv("DDIQ_FINDINGS_WORKERS", "4"))


def _findings_llm_for_issue(
    issue: dict[str, Any],
    total_capacity_mw: Optional[float],
) -> Any:
    """One findings LLM call → raw obj (``{}`` / dict / list).

    Never raises: ``llm_json`` returns ``{}`` on hard failure, and the
    transport-crash guard here means one bad row can't kill the thread
    pool. Pure per-row unit so it can run concurrently over the shared
    (thread-safe) ``SyncLlmClient``.
    """
    try:
        return llm_json(EXTRACTION_SYSTEM, _findings_prompt_for_issue(issue, total_capacity_mw))
    except Exception as e:  # pragma: no cover — llm_json already swallows
        _log.warning("findings: llm_json raised — %s", e)
        return {}


def _findings_prompt_for_issue(
    issue: dict[str, Any],
    total_capacity_mw: Optional[float] = None,
) -> str:
    """Build the per-row LLM prompt asking for ONE Finding object.

    Pre-serialise the issue dict outside the f-string so its JSON
    braces don't collide with f-string brace escaping.
    """
    issue_json = json.dumps(issue, ensure_ascii=False)
    capacity_hint = (
        f"\nProject total capacity (for MW-affected sizing): {total_capacity_mw} MW"
        if total_capacity_mw
        else ""
    )
    return f"""You are drafting ONE entry of the FINDINGS chapter of a wind-park red-flag DD report.
For the single material issue below, produce ONE Finding with:
- domain: "Land" | "Permits" | "Economics" | "Regulatory" | "General"
- severity: "red" (deal-blocker) | "yellow" (open issue, manageable) | "green" (resolved)
- text: 1-2 sentence factual statement of the issue.
- legal_basis: cite the specific German statute (e.g. "BImSchG §6", "BauGB §35 Abs. 5",
  "BNatSchG §44", "VwGO §70") if known. null otherwise.
- recommended_action: concrete next step a lawyer would take (e.g. "Obtain certified
  Grundbuch extract for parcel 12/4 and verify lessor identity", "Renew Bürgschaft
  with bank guarantee letter before 2027-06-30").
- quantification: object with mw_affected (number, null if unknown), eur_impact_estimate
  (number in EUR, null if unknown), days_until_deadline (integer, null if no
  date-bound deadline), rationale (one short sentence justifying the numbers).{capacity_hint}

Issue to draft for:
{issue_json}

Return a single JSON object (not an array). Use null for any field you cannot determine."""


def _finding_from_llm_obj(
    obj: dict[str, Any],
    source_issue: dict[str, Any],
) -> Optional[Finding]:
    """Convert one LLM-returned JSON object into a :class:`Finding`.

    Returns ``None`` if the object is unusable (wrong shape, missing
    the mandatory ``text`` field, etc.) so the caller can emit a
    structured placeholder for this specific issue instead.

    Evidence is attached directly from ``source_issue`` rather than
    asked from the LLM — the old batched prompt routed Evidence
    through a 1-indexed ``evidence_indices`` array because each LLM
    response covered multiple issues; per-row that indirection is
    dead weight.
    """
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("text", "")).strip()
    if not text:
        return None
    sev = obj.get("severity") if obj.get("severity") in ("green", "yellow", "red") else "yellow"

    ev: list[Evidence] = []
    for e in source_issue.get("evidence") or []:
        if isinstance(e, dict):
            ev.append(Evidence(**{k: v for k, v in e.items() if k in Evidence.model_fields}))

    q_raw = obj.get("quantification") or {}
    quant: Optional[Quantification] = None
    if isinstance(q_raw, dict) and any(
        q_raw.get(k) is not None
        for k in ("mw_affected", "eur_impact_estimate", "days_until_deadline")
    ):
        quant = Quantification(
            mw_affected=q_raw.get("mw_affected"),
            eur_impact_estimate=q_raw.get("eur_impact_estimate"),
            days_until_deadline=q_raw.get("days_until_deadline"),
            rationale=q_raw.get("rationale"),
        )

    return Finding(
        domain=str(obj.get("domain", "General")),
        severity=sev,
        text=text,
        evidence=ev,
        quantification=quant,
        legal_basis=obj.get("legal_basis"),
        recommended_action=obj.get("recommended_action"),
        kind="section",
    )


def _placeholder_finding_for_issue(i: int, issue: dict[str, Any]) -> Finding:
    """Emit a structured stand-in when the LLM call for one issue failed.

    Carries the issue's section + label + Evidence so a human reviewer
    can locate the source row immediately. Previously the whole
    chapter was replaced with "Manual review required" on the first
    parse failure; per-row, the lawyer still sees the other findings
    in their slots and only this one shows the placeholder.
    """
    ev: list[Evidence] = []
    for e in issue.get("evidence") or []:
        if isinstance(e, dict):
            ev.append(Evidence(**{k: v for k, v in e.items() if k in Evidence.model_fields}))
    return Finding(
        domain="General",
        severity="yellow",
        text=(
            f"(Extraction failed for issue #{i}: "
            f"{issue.get('section', '?')} → {issue.get('label', '?')}). "
            "Manual review of the source row required."
        ),
        evidence=ev,
        kind="section",
    )


def generate_findings(
    doc_ids: list[str],  # noqa: ARG001 — kept for signature compatibility
    sections: list[AusgabeblattSection],
    total_capacity_mw: Optional[float] = None,
    max_workers: Optional[int] = None,
) -> list[Finding]:
    """Build evidence-aware findings, one LLM call per flagged row.

    ``doc_ids`` is currently unused (evidence comes from the row's
    ``evidence`` field attached during section analysis, not a fresh RAG
    pass) — kept in the signature for backwards compatibility.

    E1: the per-row LLM calls run CONCURRENTLY over a thread pool
    (``max_workers``, default :data:`_FINDINGS_DEFAULT_WORKERS`). The
    shared ``SyncLlmClient``'s ``httpx.Client`` is thread-safe and vLLM
    batches the concurrent requests on the GPU, so the findings phase no
    longer costs the sequential sum of ~per-call latency — the bottleneck
    that blew the Celery hard limit in the §14 re-smoke. Results are
    assembled in the original flagged-row order (``executor.map``), so the
    output ordering and per-row placeholder semantics are unchanged.
    ``max_workers=1`` forces sequential execution (used by tests for
    deterministic response ordering).
    """
    flagged: list[dict[str, Any]] = []
    for sec in sections:
        for row in sec.rows:
            if row.ampel not in ("red", "yellow"):
                continue
            # E10: evidence + anchor are real AusgabeblattRow fields now
            # (were __dict__ shadow attrs). ``getattr`` with a default
            # keeps this resilient if an older row dict without the field
            # is rehydrated from a pre-E10 JSONB checkpoint.
            ev = getattr(row, "evidence", None) or []
            anchor = getattr(row, "anchor", None)
            flagged.append({
                "section": sec.title, "label": row.label, "value": row.value,
                "ampel": row.ampel, "note": row.note, "anchor": anchor,
                "evidence": [
                    (e.model_dump() if hasattr(e, "model_dump") else (e.dict() if hasattr(e, "dict") else e))
                    for e in ev
                ],
            })

    if not flagged:
        return [Finding(
            domain="General", severity="green",
            text="No material issues identified across the analysed sections.",
            kind="section",
        )]

    # Fan the per-row LLM calls out concurrently (order-preserving).
    workers = max_workers if max_workers is not None else _FINDINGS_DEFAULT_WORKERS
    workers = max(1, min(workers, len(flagged)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        objs = list(ex.map(
            lambda issue: _findings_llm_for_issue(issue, total_capacity_mw),
            flagged,
        ))

    out: list[Finding] = []
    failures = 0
    for i, (issue, obj) in enumerate(zip(flagged, objs), start=1):
        finding: Optional[Finding] = None
        if isinstance(obj, dict):
            finding = _finding_from_llm_obj(obj, issue)
        elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
            # Be lenient: the prompt asks for a single object, but if
            # the model returned a single-element array we'll still
            # take it.
            finding = _finding_from_llm_obj(obj[0], issue)

        if finding is None:
            failures += 1
            out.append(_placeholder_finding_for_issue(i, issue))
        else:
            out.append(finding)

    if failures:
        _log.warning(
            f"findings: {failures}/{len(flagged)} issues fell through to "
            "placeholder (extraction failed). Other findings unaffected."
        )

    return out
