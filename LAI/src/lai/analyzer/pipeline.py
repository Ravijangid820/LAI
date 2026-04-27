"""End-to-end V2 contract analysis pipeline.

Public entry point: ``analyze(contract_text, docling_tables, *, llm)``.

The pipeline is deliberately synchronous and single-process. Concurrency
should happen above this layer (one analysis per request) — Qwen3.6-27B
already saturates a Pro 6000 with reasoning enabled.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from lai.analyzer import cadastral_ner
from lai.analyzer import llm_client
from lai.analyzer import prompts
from lai.analyzer.llm_client import AnalyzerLLMConfig
from lai.analyzer.playbooks import PLAYBOOKS, severity_for_topic
from lai.analyzer.reconciler import reconcile_all
from lai.analyzer.schema import (
    Clause,
    ContractAnalysis,
    ContractMetadata,
    ContractType,
    CrossClauseFinding,
    Issue,
    Parcel,
)


# Rough char→token conversion for German legal text (memory: ~3 chars/token)
_CHAR_PER_TOKEN = 3
_LONG_DOC_CHAR_BUDGET = 48_000 * _CHAR_PER_TOKEN  # ~48k tokens


def _parse_json_lenient(s: str) -> object:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        for ch in "[{":
            i = s.find(ch)
            if i >= 0:
                try:
                    return json.loads(s[i:])
                except json.JSONDecodeError:
                    continue
    return None


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def classify(cfg: AnalyzerLLMConfig, contract_text: str) -> ContractType:
    snippet = contract_text[:6000]
    try:
        out, _ = llm_client.call(
            cfg, prompts.CLASSIFY_SYSTEM, snippet,
            enable_thinking=False, max_new_tokens=16, temperature=0.0,
        )
    except Exception:
        return "Sonstiges"
    valid: tuple[ContractType, ...] = (
        "Pachtvertrag", "Nutzungsvertrag", "Wartungsvertrag",
        "Direktvermarktungsvertrag", "Einspeisevertrag", "PPA",
        "Dienstleistungsvertrag", "Kaufvertrag", "Sonstiges",
    )
    for ct in valid:
        if ct.lower() in out.lower():
            return ct
    return "Sonstiges"


def _summarize_section(cfg: AnalyzerLLMConfig, text: str) -> str:
    try:
        out, _ = llm_client.call(
            cfg, prompts.SECTION_SUMMARY_SYSTEM, text,
            enable_thinking=False, max_new_tokens=512, temperature=0.0,
        )
        return out
    except Exception:
        return text[:1500]


def _maybe_compress(cfg: AnalyzerLLMConfig, contract_text: str) -> str:
    """Hybrid long-context handling — see CONTRACT_ANALYZER_V2.md §13."""
    if len(contract_text) <= _LONG_DOC_CHAR_BUDGET:
        return contract_text
    sections: list[str] = re.split(r"\n\s*#{1,3}\s+", contract_text)
    if len(sections) <= 1:
        # Fallback: chunk by paragraph windows of ~30k chars
        windows: list[str] = []
        cur = 0
        while cur < len(contract_text):
            end = min(cur + 30_000, len(contract_text))
            back = contract_text.rfind("\n\n", cur, end)
            if back > cur + 15_000:
                end = back
            windows.append(contract_text[cur:end])
            cur = end
        sections = windows
    summaries = [_summarize_section(cfg, s) for s in sections if s.strip()]
    return "\n\n".join(summaries)


def _build_ner_llm_call(cfg: AnalyzerLLMConfig):
    def _call(system: str, user: str, schema: Optional[dict]) -> str:
        out, _ = llm_client.call(
            cfg, system, user,
            json_schema=schema, enable_thinking=False,
            max_new_tokens=2048, temperature=0.0,
        )
        return out
    return _call


def _analyze_one_clause(
    cfg: AnalyzerLLMConfig,
    clause_id: str,
    clause_title: str,
    clause_text: str,
    contract_type: str,
    contract_summary: str,
) -> Clause:
    user = prompts.build_clause_user(clause_id, clause_title, clause_text, contract_type, contract_summary)
    out, _ = llm_client.call(
        cfg, prompts.CLAUSE_SYSTEM, user,
        enable_thinking=True, max_thinking_tokens=4096, max_new_tokens=2048,
    )
    parsed = _parse_json_lenient(out)
    if not isinstance(parsed, dict):
        return Clause(id=clause_id, title=clause_title, text=clause_text,
                      type="Sonstiges", summary="", issues=[])
    issues: list[Issue] = []
    for raw in parsed.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        try:
            issues.append(Issue(
                severity=int(raw.get("severity", 3)),
                title=str(raw.get("title", ""))[:200],
                description=str(raw.get("description", "")),
                affected_clauses=raw.get("affected_clauses") or [clause_id],
                rectify_or_ignore=raw.get("rectify_or_ignore") or "negotiate",
                rationale=str(raw.get("rationale", "")),
                suggested_redline=raw.get("suggested_redline"),
                legal_basis=raw.get("legal_basis") or [],
            ))
        except Exception:
            continue
    return Clause(
        id=clause_id,
        title=clause_title,
        text=clause_text,
        type=str(parsed.get("type", "Sonstiges"))[:80],
        summary=str(parsed.get("summary", ""))[:1000],
        issues=issues,
    )


def _whole_contract_pass(
    cfg: AnalyzerLLMConfig,
    contract_type: ContractType,
    metadata_hint: dict,
    clauses: list[Clause],
    reconciliation_findings_dicts: list[dict],
) -> tuple[ContractMetadata, list[CrossClauseFinding], list[Issue]]:
    clause_summaries = [
        {"id": c.id, "type": c.type, "title": c.title, "summary": c.summary}
        for c in clauses
    ]
    flagged_verbatim = [
        {"id": c.id, "title": c.title, "text": c.text}
        for c in clauses if any(i.severity >= 3 for i in c.issues)
    ]
    user = prompts.build_whole_contract_user(
        contract_type=contract_type,
        metadata_hint=metadata_hint,
        clause_summaries=clause_summaries,
        flagged_clauses_verbatim=flagged_verbatim,
        reconciliation_findings=reconciliation_findings_dicts,
    )
    out, _ = llm_client.call(
        cfg, prompts.WHOLE_CONTRACT_SYSTEM, user,
        enable_thinking=True, max_thinking_tokens=8192, max_new_tokens=4096,
    )
    parsed = _parse_json_lenient(out) or {}
    if not isinstance(parsed, dict):
        parsed = {}

    md_raw = parsed.get("metadata") or {}
    metadata = ContractMetadata(
        parties=md_raw.get("parties") or [],
        effective_date=md_raw.get("effective_date"),
        signing_date=md_raw.get("signing_date"),
        term=md_raw.get("term"),
        jurisdiction=md_raw.get("jurisdiction"),
    )

    cross: list[CrossClauseFinding] = []
    for raw in parsed.get("cross_clause_findings") or []:
        if not isinstance(raw, dict):
            continue
        try:
            cross.append(CrossClauseFinding(
                title=str(raw.get("title", ""))[:200],
                involved_clauses=raw.get("involved_clauses") or [],
                description=str(raw.get("description", "")),
                severity=int(raw.get("severity", 3)),
                rectify_or_ignore=raw.get("rectify_or_ignore") or "negotiate",
                rationale=str(raw.get("rationale", "")),
            ))
        except Exception:
            continue

    missing_from_llm: list[Issue] = []
    for raw in parsed.get("missing_required_clauses") or []:
        if not isinstance(raw, dict):
            continue
        try:
            missing_from_llm.append(Issue(
                severity=int(raw.get("severity", 3)),
                title=str(raw.get("title", ""))[:200],
                description=str(raw.get("description", "")),
                affected_clauses=raw.get("affected_clauses") or [],
                rectify_or_ignore=raw.get("rectify_or_ignore") or "rectify",
                rationale=str(raw.get("rationale", "")),
                suggested_redline=raw.get("suggested_redline"),
                legal_basis=raw.get("legal_basis") or [],
            ))
        except Exception:
            continue

    # Belt-and-suspenders: deterministic playbook check supplements the LLM.
    seen_topics_lower = {c.type.lower() for c in clauses if c.type}
    seen_topics_lower |= {
        m.title.replace("Fehlend:", "").strip().lower() for m in missing_from_llm
    }
    deterministic: list[Issue] = []
    for topic, reason in PLAYBOOKS.get(contract_type, []):
        if topic.lower() not in seen_topics_lower and not any(topic.lower() in s for s in seen_topics_lower):
            deterministic.append(Issue(
                severity=severity_for_topic(topic),  # type: ignore[arg-type]
                title=f"Fehlend: {topic}",
                description=f"Klausel zum Thema '{topic}' wurde nicht erkannt.",
                affected_clauses=[],
                rectify_or_ignore="rectify",
                rationale=reason,
                suggested_redline=None,
                legal_basis=[],
            ))
    # Merge — LLM findings first (they may carry richer rationale),
    # deterministic ones supplement gaps.
    have = {m.title.lower() for m in missing_from_llm}
    for d in deterministic:
        if d.title.lower() not in have:
            missing_from_llm.append(d)

    return metadata, cross, missing_from_llm


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def analyze(
    contract_text: str,
    *,
    cfg: AnalyzerLLMConfig,
    clauses_input: list[dict],
    docling_tables: Optional[list[dict]] = None,
) -> ContractAnalysis:
    """Run the V2 pipeline.

    Args:
        contract_text: full markdown of the contract (post-Docling).
        cfg: analyzer LLM config (Qwen3.6-27B endpoint).
        clauses_input: list of {"id", "title", "text"} from upstream segmentation.
        docling_tables: optional list of {"title"|"caption", "rows"} dicts.

    Returns: ContractAnalysis (Pydantic).
    """
    t0 = time.time()

    # 1) Classify
    contract_type = classify(cfg, contract_text)

    # 2) Compress for whole-contract pass if needed
    compressed_for_summary = _maybe_compress(cfg, contract_text)
    contract_summary = compressed_for_summary[:4000]

    # 3) Reconcile tables (deterministic, no LLM)
    fin_tables, recon_findings = reconcile_all(docling_tables or [])
    recon_dicts = [f.model_dump() for f in recon_findings]

    # 4) Cadastral NER
    parcels: list[Parcel] = cadastral_ner.extract_parcels(
        contract_text, _build_ner_llm_call(cfg),
    )

    # 5) Per-clause deep analysis
    clauses_out: list[Clause] = []
    for c in clauses_input:
        cid = str(c.get("id", len(clauses_out) + 1))
        title = str(c.get("title", ""))[:200]
        text = str(c.get("text", ""))
        if not text.strip():
            continue
        try:
            clauses_out.append(_analyze_one_clause(
                cfg, cid, title, text, contract_type, contract_summary,
            ))
        except Exception as e:
            clauses_out.append(Clause(
                id=cid, title=title, text=text, type="Sonstiges",
                summary=f"[Analyse fehlgeschlagen: {e}]", issues=[],
            ))

    # 6) Whole-contract pass
    metadata, cross_findings, missing = _whole_contract_pass(
        cfg, contract_type,
        metadata_hint={"first_chars": contract_text[:1000]},
        clauses=clauses_out,
        reconciliation_findings_dicts=recon_dicts,
    )

    elapsed = time.time() - t0  # noqa: F841 — caller may also time

    return ContractAnalysis(
        metadata=metadata,
        contract_type=contract_type,
        parcels=parcels,
        financial_tables=fin_tables,
        reconciliation_findings=recon_findings,
        clauses=clauses_out,
        cross_clause_findings=cross_findings,
        missing_required_clauses=missing,
        degraded=False,
        model=cfg.model,
        thinking_tokens=0,
        analyzer_version="2.0",
    )
