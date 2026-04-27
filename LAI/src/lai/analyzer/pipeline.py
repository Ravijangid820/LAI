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
    ExtractionQuality,
    Issue,
    Parcel,
)


# Empirical thresholds (German legal text typically runs ~2000-3500 chars/page
# under decent extraction). Below 1800 the body is usually fragmentary; below
# 1200 we've often only captured the cover/signature pages.
_EXTRACTION_LOW_CHARS_PER_PAGE = 1800.0
_EXTRACTION_VERY_LOW_CHARS_PER_PAGE = 1200.0


def assess_extraction_quality(
    text: str,
    n_pages: int,
) -> ExtractionQuality:
    total_chars = len(text or "")
    pages = max(int(n_pages or 0), 0)
    chars_per_page = (total_chars / pages) if pages else float(total_chars)

    if pages == 0:
        # Plain-text upload (.txt/.md) — no page metric. Treat short
        # documents as lower confidence; otherwise high.
        if total_chars < 5000:
            return ExtractionQuality(
                confidence="low",
                chars_per_page=chars_per_page,
                total_chars=total_chars,
                n_pages=0,
                reason=f"Eingabetext sehr kurz ({total_chars} Zeichen, keine Seitenangabe).",
            )
        return ExtractionQuality(
            confidence="high",
            chars_per_page=chars_per_page,
            total_chars=total_chars,
            n_pages=0,
            reason="Plain-text-Eingabe ohne Seitenangabe.",
        )

    if chars_per_page < _EXTRACTION_VERY_LOW_CHARS_PER_PAGE:
        return ExtractionQuality(
            confidence="low",
            chars_per_page=chars_per_page,
            total_chars=total_chars,
            n_pages=pages,
            reason=(
                f"Nur {chars_per_page:.0f} Zeichen pro Seite extrahiert "
                f"({total_chars:,} Zeichen / {pages} Seiten). "
                "Vermutlich gescanntes/signiertes PDF mit unvollständiger OCR — "
                "Fehlende-Klauseln-Befunde können falsch positiv sein."
            ),
        )
    if chars_per_page < _EXTRACTION_LOW_CHARS_PER_PAGE:
        return ExtractionQuality(
            confidence="low",
            chars_per_page=chars_per_page,
            total_chars=total_chars,
            n_pages=pages,
            reason=(
                f"Niedrige Textdichte: {chars_per_page:.0f} Zeichen pro Seite "
                f"({total_chars:,} / {pages}). Teile des Vertragstextes wurden "
                "möglicherweise nicht extrahiert."
            ),
        )
    if chars_per_page < 2500:
        return ExtractionQuality(
            confidence="medium",
            chars_per_page=chars_per_page,
            total_chars=total_chars,
            n_pages=pages,
            reason=(
                f"Mittlere Textdichte: {chars_per_page:.0f} Zeichen pro Seite. "
                "Extraktion plausibel, einzelne Abschnitte könnten fehlen."
            ),
        )
    return ExtractionQuality(
        confidence="high",
        chars_per_page=chars_per_page,
        total_chars=total_chars,
        n_pages=pages,
        reason=(
            f"Gute Textdichte: {chars_per_page:.0f} Zeichen pro Seite "
            f"({total_chars:,} / {pages})."
        ),
    )


def _mark_low_confidence(issues: list[Issue]) -> list[Issue]:
    """Mark missing-clause findings as low-confidence and downgrade severity
    by 1 (capped at 2 = 'unklar formuliert'). Reason: when extraction is
    poor, these are over-reports of absent text rather than absent terms."""
    out: list[Issue] = []
    for i in issues:
        new_sev = max(2, int(i.severity) - 1)
        out.append(i.model_copy(update={
            "severity": new_sev,
            "low_confidence": True,
            "description": (
                "[Niedrige Extraktionsqualität — Klausel könnte vorhanden sein, "
                "aber nicht erkannt] " + i.description
            ),
            "rectify_or_ignore": "negotiate",
            "rationale": (
                "Erst PDF-Extraktion prüfen, bevor diese Klausel als fehlend behandelt wird. "
                + i.rationale
            ),
        }))
    return out


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

ProgressCallback = Optional[
    "object"  # placeholder; real type Callable[[dict], None] below
]


def analyze(
    contract_text: str,
    *,
    cfg: AnalyzerLLMConfig,
    clauses_input: list[dict],
    docling_tables: Optional[list[dict]] = None,
    n_pages: int = 0,
    on_progress=None,  # Callable[[dict], None] — invoked at each major step
) -> ContractAnalysis:
    """Run the V2 pipeline.

    Args:
        contract_text: full markdown of the contract (post-Docling).
        cfg: analyzer LLM config (Qwen3.6-27B endpoint).
        clauses_input: list of {"id", "title", "text"} from upstream segmentation.
        docling_tables: optional list of {"title"|"caption", "rows"} dicts.
        n_pages: number of pages in the source PDF. Used to compute
            extraction quality; missing-clause findings are marked
            low-confidence when chars/page is below the legal-text threshold.
        on_progress: optional callback receiving a dict with keys
            ``step`` (str), ``current`` (int), ``total`` (int),
            ``elapsed_s`` (float), ``percent`` (0.0-1.0). Called at each
            major step. Used by the API layer to surface live progress.

    Returns: ContractAnalysis (Pydantic).
    """
    t0 = time.time()
    n_clauses_total = sum(1 for c in clauses_input if str(c.get("text", "")).strip())

    def _emit(step: str, current: int = 0, total: int = 0, percent: float = 0.0) -> None:
        if on_progress is None:
            return
        try:
            on_progress({
                "step": step,
                "current": current,
                "total": total,
                "elapsed_s": time.time() - t0,
                "percent": max(0.0, min(1.0, percent)),
            })
        except Exception:
            # Progress is observability only — never let a callback bug
            # take down the analysis.
            pass

    # Rough percent budget — tuned to reality (per-clause thinking pass
    # dominates, ~70% of total wall time on a 10-clause contract):
    #   classify        → 1%
    #   compress        → 3%
    #   reconcile/parc  → 6% (parcel NER hits the LLM per candidate)
    #   per-clause      → 70% (split evenly across n_clauses_total)
    #   whole-contract  → 20%
    PCT_CLASSIFY_DONE = 0.01
    PCT_COMPRESS_DONE = 0.04
    PCT_RECON_DONE    = 0.06
    PCT_PARCELS_DONE  = 0.10
    PCT_CLAUSES_BASE  = 0.10
    PCT_CLAUSES_END   = 0.80
    PCT_WHOLE_DONE    = 1.00

    _emit("starting", total=n_clauses_total)

    # 0) Extraction quality — gate downstream confidence on this.
    extraction_quality = assess_extraction_quality(contract_text, n_pages)

    # 1) Classify
    _emit("classifying", percent=0.0)
    contract_type = classify(cfg, contract_text)
    _emit("classify_done", percent=PCT_CLASSIFY_DONE)

    # 2) Compress for whole-contract pass if needed
    _emit("preparing_context", percent=PCT_CLASSIFY_DONE)
    compressed_for_summary = _maybe_compress(cfg, contract_text)
    contract_summary = compressed_for_summary[:4000]
    _emit("preparing_context_done", percent=PCT_COMPRESS_DONE)

    # 3) Reconcile tables (deterministic, no LLM)
    fin_tables, recon_findings = reconcile_all(docling_tables or [])
    recon_dicts = [f.model_dump() for f in recon_findings]
    _emit("tables_reconciled", current=len(fin_tables), percent=PCT_RECON_DONE)

    # 4) Cadastral NER
    _emit("extracting_parcels", percent=PCT_RECON_DONE)
    parcels: list[Parcel] = cadastral_ner.extract_parcels(
        contract_text, _build_ner_llm_call(cfg),
    )
    _emit("parcels_done", current=len(parcels), percent=PCT_PARCELS_DONE)

    # 5) Per-clause deep analysis (the bulk of the work)
    clauses_out: list[Clause] = []
    for idx, c in enumerate(clauses_input, start=1):
        cid = str(c.get("id", len(clauses_out) + 1))
        title = str(c.get("title", ""))[:200]
        text = str(c.get("text", ""))
        if not text.strip():
            continue
        progress_share = (
            PCT_CLAUSES_BASE
            + (PCT_CLAUSES_END - PCT_CLAUSES_BASE)
              * ((idx - 1) / max(n_clauses_total, 1))
        )
        _emit("analyzing_clause", current=idx, total=n_clauses_total, percent=progress_share)
        try:
            clauses_out.append(_analyze_one_clause(
                cfg, cid, title, text, contract_type, contract_summary,
            ))
        except Exception as e:
            clauses_out.append(Clause(
                id=cid, title=title, text=text, type="Sonstiges",
                summary=f"[Analyse fehlgeschlagen: {e}]", issues=[],
            ))

    _emit("clauses_done", current=n_clauses_total, total=n_clauses_total, percent=PCT_CLAUSES_END)

    # 6) Whole-contract pass
    _emit("whole_contract", percent=PCT_CLAUSES_END)
    metadata, cross_findings, missing = _whole_contract_pass(
        cfg, contract_type,
        metadata_hint={"first_chars": contract_text[:1000]},
        clauses=clauses_out,
        reconciliation_findings_dicts=recon_dicts,
    )

    elapsed = time.time() - t0  # noqa: F841 — caller may also time

    # When extraction is low-confidence, the "missing clauses" list is
    # mostly over-reports of unseen text. Downgrade severity and tag.
    if extraction_quality.confidence == "low":
        missing = _mark_low_confidence(missing)

    _emit("done", current=n_clauses_total, total=n_clauses_total, percent=PCT_WHOLE_DONE)

    return ContractAnalysis(
        metadata=metadata,
        contract_type=contract_type,
        parcels=parcels,
        financial_tables=fin_tables,
        reconciliation_findings=recon_findings,
        clauses=clauses_out,
        cross_clause_findings=cross_findings,
        missing_required_clauses=missing,
        extraction_quality=extraction_quality,
        degraded=False,
        model=cfg.model,
        thinking_tokens=0,
        analyzer_version="2.0",
    )
