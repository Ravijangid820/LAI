"""Per-domain LLM-driven extractors for the DDiQ report pipeline.

Moved out of the legacy ``ddiq_report`` god-module in H-5 phase 2.
Each module here owns ONE extraction pass — same signature and
return shape as the legacy function, just lifted out so it can be
tested in isolation and so the orchestrator (``ddiq_report``) is no
longer a 3-kLOC file.

Layering:

  ddiq_report.py
      ├── ddiq.extractors.<domain>
      │       ├── ddiq.models
      │       ├── ddiq.llm  (singletons + llm_json + EXTRACTION_SYSTEM)
      │       └── ddiq.rag  (rag_context_with_meta + evidence_from_chunks)
      ├── ddiq.models
      ├── ddiq.db
      ├── ddiq.llm
      └── ddiq.rag

No extractor imports from ``ddiq_report`` — the dependency graph is
strictly downward. Extractors that still need pipeline-orchestrator
state (project_center, geocoded WEAs) take it as a parameter rather
than reaching back up.

Re-exports below let call sites do ``from ddiq.extractors import
extract_timeline`` for ergonomics without needing to know which
submodule owns it.
"""

from ddiq.extractors.consistency import check_cross_doc_consistency
from ddiq.extractors.findings import (
    _finding_from_llm_obj,
    _findings_prompt_for_issue,
    _placeholder_finding_for_issue,
    generate_findings,
)
from ddiq.extractors.grundbuch import check_grundbuch_match
from ddiq.extractors.rueckbau import extract_rueckbau_bond
from ddiq.extractors.timeline import extract_timeline

__all__ = [
    "_finding_from_llm_obj",
    "_findings_prompt_for_issue",
    "_placeholder_finding_for_issue",
    "check_cross_doc_consistency",
    "check_grundbuch_match",
    "extract_rueckbau_bond",
    "extract_timeline",
    "generate_findings",
]
