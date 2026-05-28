"""Prompts for Contract Analyzer V2.

Two main system prompts (per-clause deep + whole-contract pass) plus a
classifier for contract type. Kept as module constants — easy to iterate,
no template engine.

See docs/analysis/CONTRACT_ANALYZER_V2.md §9.
"""

from __future__ import annotations

from lai.analyzer.playbooks import PLAYBOOKS

# ---------------------------------------------------------------------------
# Contract-type classifier
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = (
    "Du bist ein juristischer Analyst. Klassifiziere den folgenden deutschen "
    "Vertrag in EINE der folgenden Kategorien. Antworte AUSSCHLIESSLICH mit "
    "dem Kategorienamen, ohne Erklärung.\n\n"
    "Kategorien: Pachtvertrag, Nutzungsvertrag, Wartungsvertrag, "
    "Direktvermarktungsvertrag, Einspeisevertrag, PPA, "
    "Dienstleistungsvertrag, Kaufvertrag, Sonstiges.\n\n"
    "Hinweise:\n"
    "- 'Pachtvertrag' = Grundstückspacht für WEA-Standort.\n"
    "- 'Nutzungsvertrag' = Wegerechte, UW-Nutzung, Kabeltrassen ohne Pacht-Konstrukt.\n"
    "- 'Wartungsvertrag' = O&M / Service Agreement / Full-Service.\n"
    "- 'Direktvermarktungsvertrag' = Direktvermarktung + Marktprämie nach EEG.\n"
    "- 'Einspeisevertrag' = Netzanschluss / Einspeisung beim Netzbetreiber.\n"
    "- 'PPA' = Power Purchase Agreement, Stromabnahme über Marktprämie hinaus.\n"
    "- 'Kaufvertrag' = Anteils-/WEA-/Projektkaufvertrag (SPA).\n"
    "- 'Dienstleistungsvertrag' = Beratung, Repowering-Studien, Gutachten.\n"
)


# ---------------------------------------------------------------------------
# Per-clause deep analysis
# ---------------------------------------------------------------------------

CLAUSE_SYSTEM = (
    "Du bist ein erfahrener deutscher Energieanwalt mit 20 Jahren "
    "Schwerpunkt Windenergie und Projektfinanzierung. Du prüfst die "
    "folgende Klausel akribisch. Antworte AUSSCHLIESSLICH mit einem "
    "JSON-Objekt nach folgendem Schema; keine Markdown-Codeblöcke:\n\n"
    "{\n"
    '  "type": "<Kurzbezeichnung des Klauselthemas>",\n'
    '  "summary": "<1-2 Sätze: was regelt die Klausel inhaltlich>",\n'
    '  "issues": [\n'
    "    {\n"
    '      "severity": <1|2|3|4|5>,\n'
    '      "title": "<Kurztitel>",\n'
    '      "description": "<konkretes Problem in 1-3 Sätzen>",\n'
    '      "affected_clauses": ["<diese Klausel-ID>"],\n'
    '      "rectify_or_ignore": "<rectify|ignore|negotiate>",\n'
    '      "rationale": "<warum diese Disposition; Pflichtfeld>",\n'
    '      "suggested_redline": "<Vorschlag oder null>",\n'
    '      "legal_basis": ["§ ... BGB", "§ ... EEG", ...]\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Severity-Skala:\n"
    "  1 = redaktionell / Schönheitsfehler\n"
    "  2 = unklar formuliert, aber wirtschaftlich harmlos\n"
    "  3 = wirtschaftlich relevant, sollte verhandelt werden\n"
    "  4 = erhebliches Risiko / regulatorisches Problem\n"
    "  5 = blockierend / Deal-Breaker / Unwirksamkeit nach AGB-Recht\n\n"
    "Disposition:\n"
    "  rectify   = muss zwingend geändert werden (z.B. AGB-Verstoß, Unwirksamkeit)\n"
    "  negotiate = sollte verhandelt werden, ist aber nicht zwingend\n"
    "  ignore    = bewusst ignorierbar (z.B. Tippfehler, redundante Regelung)\n\n"
    "Begründe rationale immer mit dem konkreten Risiko oder dem konkreten "
    "Grund, warum kein Risiko besteht. Wenn die Klausel sauber ist: issues=[]."
)


def build_clause_user(
    clause_id: str,
    clause_title: str,
    clause_text: str,
    contract_type: str,
    contract_summary: str,
) -> str:
    return (
        f"Vertragstyp: {contract_type}\n"
        f"Vertragskontext (kurz): {contract_summary[:1500]}\n\n"
        f"Klausel-ID: {clause_id}\n"
        f"Klausel-Titel: {clause_title}\n"
        f"Klausel-Text:\n---\n{clause_text}\n---"
    )


# ---------------------------------------------------------------------------
# Whole-contract pass
# ---------------------------------------------------------------------------

WHOLE_CONTRACT_SYSTEM = (
    "Du bist ein erfahrener deutscher Energieanwalt. Du hast bereits jede "
    "Klausel einzeln geprüft. Jetzt prüfst du den Vertrag als Ganzes auf "
    "(a) widersprüchliche oder inkonsistente Klauseln, (b) fehlende "
    "Pflichtklauseln gemäß Playbook, (c) Auswirkungen rechnerischer "
    "Diskrepanzen aus den Tabellen.\n\n"
    "Du erhältst:\n"
    "  - den Vertragstyp,\n"
    "  - eine Zusammenfassung jeder Klausel,\n"
    "  - die Pflicht-Themen für diesen Vertragstyp (Playbook),\n"
    "  - eine Liste rechnerischer Diskrepanzen aus den Tabellen (sofern vorhanden),\n"
    "  - die wörtlichen Texte jener Klauseln, die in der Einzelprüfung "
    "Probleme der Schwere ≥ 3 hatten.\n\n"
    "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt nach folgendem Schema:\n"
    "{\n"
    '  "metadata": {\n'
    '    "parties": [...], "effective_date": "...", "signing_date": "...",\n'
    '    "term": "...", "jurisdiction": "..."\n'
    "  },\n"
    '  "cross_clause_findings": [\n'
    '    {"title": "...", "involved_clauses": ["..."], "description": "...",\n'
    '     "severity": 1-5, "rectify_or_ignore": "...", "rationale": "..."}\n'
    "  ],\n"
    '  "missing_required_clauses": [\n'
    '    {"severity": 3-5, "title": "Fehlend: <Topic>", "description": "...",\n'
    '     "affected_clauses": [], "rectify_or_ignore": "rectify",\n'
    '     "rationale": "...", "suggested_redline": null, "legal_basis": []}\n'
    "  ],\n"
    '  "reconciliation_interpretation": [\n'
    '    {"table_title": "...", "verdict": "rounding|ocr|real_defect",\n'
    '     "explanation": "..."}\n'
    "  ]\n"
    "}\n\n"
    "Keine Markdown-Codeblöcke. Wenn nichts zu melden ist: leere Listen."
)


def build_whole_contract_user(
    contract_type: str,
    metadata_hint: dict,
    clause_summaries: list[dict],
    flagged_clauses_verbatim: list[dict],
    reconciliation_findings: list[dict],
) -> str:
    playbook = PLAYBOOKS.get(contract_type, [])
    pb_lines = "\n".join(f"  - {topic}: {reason}" for topic, reason in playbook)

    cs_lines = "\n".join(
        f"  [{c['id']}] ({c.get('type', '?')}) {c.get('title', '')[:80]} — {c.get('summary', '')[:200]}"
        for c in clause_summaries
    )

    flagged_lines = (
        "\n\n".join(
            f"### Klausel {c['id']} — {c.get('title', '')}\n{c.get('text', '')}" for c in flagged_clauses_verbatim
        )
        or "(keine ≥ Schwere-3-Befunde aus Einzelprüfung)"
    )

    recon_lines = (
        "\n".join(
            f"  - {f['table_title']} ({f['kind']}, severity={f['severity']}): {f['note']}"
            for f in reconciliation_findings
        )
        or "(keine Diskrepanzen)"
    )

    return (
        f"Vertragstyp: {contract_type}\n"
        f"Metadaten-Hinweis: {metadata_hint}\n\n"
        f"Pflicht-Themen (Playbook für {contract_type}):\n{pb_lines}\n\n"
        f"Klausel-Zusammenfassungen:\n{cs_lines}\n\n"
        f"Wörtliche Texte der ≥3-Befunde:\n{flagged_lines}\n\n"
        f"Rechnerische Diskrepanzen:\n{recon_lines}\n"
    )


# ---------------------------------------------------------------------------
# Section summarization (used only when contract exceeds 48k tokens)
# ---------------------------------------------------------------------------

SECTION_SUMMARY_SYSTEM = (
    "Du fasst einen Abschnitt eines deutschen Vertrags neutral und kompakt "
    "zusammen (max. 250 Wörter). Behalte alle Zahlen, Fristen, "
    "Vertragsparteien, Paragraphen und konkreten Pflichten bei. Keine "
    "Bewertung, keine Empfehlung, keine Markdown-Überschriften."
)
