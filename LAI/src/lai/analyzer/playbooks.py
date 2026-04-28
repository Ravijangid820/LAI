"""Per-contract-type required-clause playbooks.

Each playbook is a list of (required_clause_topic, why_required) tuples.
The whole-contract analysis pass turns absences into
``missing_required_clauses`` issues with severity drawn from the
``CRITICAL_TOPICS`` set (severity 4) vs. default (severity 3).

These are V2.0 baselines, edit-in-place by legal review. See
docs/analysis/CONTRACT_ANALYZER_V2.md §6.
"""
from __future__ import annotations

from typing import Iterable

from lai.analyzer.schema import ContractType


# Topics flagged at severity 4 if missing; everything else severity 3.
CRITICAL_TOPICS: set[str] = {
    "Vertragsdauer",
    "Pacht/Vergütung",
    "Vergütung",
    "Rückbauverpflichtung",
    "Haftung",
    "Kündigungsrechte",
    "Verfügbarkeitsgarantie",
    "Force Majeure",
    "EEG-Vergütung / Marktprämie",
    "Anschlusspunkt",
}


PLAYBOOKS: dict[ContractType, list[tuple[str, str]]] = {
    "Pachtvertrag": [
        ("Vertragsdauer", "Wind-Pachtverträge laufen typisch 25–30 Jahre; Fehlen führt zu vorzeitiger Beendigung."),
        ("Pacht/Vergütung", "Höhe und Anpassungsmechanismus müssen klar geregelt sein."),
        ("Verlängerungsoption", "Option zur Verlängerung sichert die Betriebsphase über die Mindestlaufzeit hinaus."),
        ("Rückbauverpflichtung", "Pflicht nach § 35 BauGB; Allokation der Rückbaukosten ist kritisch."),
        ("Kündigungsrechte", "Außerordentliche Kündigungsgründe und Heilungsfristen müssen definiert sein."),
        ("Untervermietung/Überlassung", "Übertragung an Projektgesellschaften / Käufer regeln."),
        ("Grunddienstbarkeit", "Dingliche Sicherung des Pachtrechts gegen Eigentümerwechsel."),
        ("Wegerecht/Zufahrt", "Zugang zur WEA muss dauerhaft gesichert sein."),
        ("Genehmigungsrisiko", "Allokation des Risikos bei Versagung der BImSchG-Genehmigung."),
        ("Vorkaufsrecht", "Schutz des Betreibers bei Veräußerung des Grundstücks."),
        ("Übertragung/Sukzession", "Übergang der Rechte/Pflichten bei Eigentümerwechsel."),
    ],
    "Nutzungsvertrag": [
        ("Nutzungsumfang", "Klare Definition der zulässigen Nutzung verhindert Streit."),
        ("Vergütung", "Höhe und Anpassung des Entgelts."),
        ("Vertragsdauer", "Laufzeit und Mindestnutzungsdauer."),
        ("Kündigungsrechte", "Außerordentliche Kündigungsrechte beider Seiten."),
        ("Haftung", "Haftungsverteilung zwischen Nutzer und Eigentümer."),
        ("Versicherung", "Pflicht zur Haftpflicht-/Betriebsversicherung."),
        ("Rückbauverpflichtung", "Wiederherstellung des ursprünglichen Zustands."),
    ],
    "Wartungsvertrag": [
        ("Leistungsumfang", "Genaue Abgrenzung Full-Service vs. Basic-Service ist preisbestimmend."),
        ("Verfügbarkeitsgarantie", "Technische und/oder kommerzielle Verfügbarkeit (typisch ≥ 97%)."),
        ("Reaktionszeiten", "SLA für Störungsbeseitigung."),
        ("Vergütung", "Festpreis, indexiert, oder leistungsabhängig."),
        ("Pönale/Bonus-Malus", "Sanktion bei Unterschreitung der Verfügbarkeitsgarantie."),
        ("Vertragsdauer", "Laufzeit, Verlängerungsoptionen, Termination for Convenience."),
        ("Kündigungsrechte", "Außerordentliche Kündigung bei Schlechtleistung."),
        ("Haftungsbegrenzung", "Cap auf Jahresvergütung üblich; Personenschäden ausnehmen."),
        ("Ersatzteilversorgung", "Verfügbarkeit von Ersatzteilen für die Vertragslaufzeit."),
    ],
    "Direktvermarktungsvertrag": [
        ("Vergütungsformel", "Marktprämie + Bonus / Profil-/Ausgleichsenergie-Kosten."),
        ("Marktprämie", "Anbindung an EEG-Marktprämienmodell."),
        ("Abnahmeverpflichtung", "Pflicht des Direktvermarkters zur Abnahme der Strommengen."),
        ("Bilanzkreismanagement", "Übergabe in den Bilanzkreis und Verantwortlichkeit für Prognoseabweichungen."),
        ("Force Majeure", "Höhere Gewalt incl. Curtailment / Netzengpässe."),
        ("Curtailment / Einspeisemanagement", "Behandlung von EinsMan-Eingriffen und Entschädigungen."),
        ("Vertragsdauer", "Laufzeit und Verlängerung."),
        ("Kündigungsrechte", "Insolvenz, Schlechtleistung, Marktveränderung."),
    ],
    "Einspeisevertrag": [
        ("Anschlusspunkt", "Genauer Netzverknüpfungspunkt mit Spannungsebene."),
        ("Einspeiseleistung", "Maximale Einspeiseleistung in MW."),
        ("EEG-Vergütung / Marktprämie", "Vergütungsmechanismus nach EEG."),
        ("Mess- und Abrechnungsmodalitäten", "Messkonzept, Abrechnungsperiode, Datenübermittlung."),
        ("Haftung", "Haftung für Netzeinspeisung und Einspeisemanagement."),
        ("Force Majeure", "Höhere Gewalt und Netzengpässe."),
        ("Vertragsdauer", "Laufzeit gekoppelt an EEG-Vergütungszeitraum."),
    ],
    "PPA": [
        ("Vergütungsformel", "Festpreis, Floor/Cap oder Indexierung — Hauptpreismechanismus."),
        ("Abnahmeverpflichtung", "Take-or-Pay Struktur, Volume Commitment."),
        ("Lieferprofil", "Baseload, As-Produced, oder Pay-as-Forecasted."),
        ("Bilanzkreismanagement", "Profilkostentragung."),
        ("Force Majeure", "Pflichten und Vergütung bei höherer Gewalt."),
        ("Curtailment / Einspeisemanagement", "EinsMan-Behandlung und Vergütung."),
        ("Herkunftsnachweise", "Übertragung der Guarantees of Origin / GoO."),
        ("Vertragsdauer", "Typisch 10–15 Jahre; Effektiv- und Anfangsdatum."),
        ("Kündigungsrechte", "Insolvenz, Change of Control, Material Adverse Change."),
        ("Change of Law", "Behandlung gesetzlicher Änderungen (EEG-Reform, CO2-Bepreisung)."),
    ],
    "Dienstleistungsvertrag": [
        ("Leistungsumfang", "Abgrenzung der zu erbringenden Dienstleistungen."),
        ("Vergütung", "Festpreis oder Aufwand; Zahlungstermine."),
        ("Vertragsdauer", "Laufzeit und Kündigungsfristen."),
        ("Kündigungsrechte", "Außerordentliche Kündigungsgründe."),
        ("Haftung", "Haftungsumfang und -begrenzung."),
        ("Vertraulichkeit", "NDA-artige Regelungen für Projektinformationen."),
    ],
    "Kaufvertrag": [
        ("Kaufgegenstand", "Eindeutige Identifikation der WEA / Anteile / Projektrechte."),
        ("Kaufpreis", "Höhe, Anpassungsmechanismen, Zahlungstermine."),
        ("Garantien", "Zusicherungen des Verkäufers (Title, Permits, Compliance)."),
        ("Haftungsbegrenzung", "Cap, Basket, Übergabezeitraum für Gewährleistung."),
        ("Closing-Bedingungen", "Conditions Precedent (Genehmigungen, Finanzierung)."),
        ("Übergangsstichtag", "Gefahrenübergang und wirtschaftlicher Stichtag."),
    ],
    "Sonstiges": [
        ("Vertragsdauer", "Jeder Vertrag braucht eine geregelte Laufzeit."),
        ("Vergütung", "Wirtschaftliche Gegenleistung muss klar sein."),
        ("Kündigungsrechte", "Mindestens ordentliche Kündigungsregelung."),
        ("Haftung", "Haftungsverteilung zwischen den Parteien."),
    ],
}


def required_topics(contract_type: ContractType) -> list[tuple[str, str]]:
    return PLAYBOOKS.get(contract_type, PLAYBOOKS["Sonstiges"])


def severity_for_topic(topic: str) -> int:
    return 4 if topic in CRITICAL_TOPICS else 3


def all_topics(types: Iterable[ContractType] = ()) -> set[str]:
    """Union of every topic across the given types (for soft-prompt hints)."""
    out: set[str] = set()
    for t in types or PLAYBOOKS.keys():
        for topic, _ in PLAYBOOKS.get(t, []):
            out.add(topic)
    return out


# ── Topic synonym map for the playbook coverage check ─────────────────────
# When deciding whether a required topic is "covered" by the segmented
# clauses, we previously did a literal substring match: required topic
# string vs. each clause's type string. That misses the way the analyzer
# (and German legal practice) labels things — e.g. clause type
# "Laufzeit und Verlängerung" is the Vertragsdauer of the contract;
# clause type "Haftungsbeschränkung" is the Haftungsbegrenzung. Without
# a synonym map both got flagged as missing.
#
# Each entry maps a playbook topic → list of lowercase patterns. If ANY
# pattern is a substring of any clause type seen in the contract, the
# topic counts as covered. Patterns default to ``[topic.lower()]`` for
# topics not listed here.
TOPIC_SYNONYMS: dict[str, list[str]] = {
    "Vertragsdauer":           ["vertragsdauer", "laufzeit", "vertragslaufzeit",
                                "beginn und ende", "vertragsverhältnis"],
    "Verlängerungsoption":     ["verlängerung", "verlängerungsoption", "renewal"],
    "Pacht/Vergütung":         ["pacht", "vergütung", "entgelt", "miete"],
    "Vergütung":               ["vergütung", "entgelt", "honorar", "preis"],
    "Vergütungsformel":        ["vergütungsformel", "preisformel", "marktprämie", "vergütung"],
    "Kündigungsrechte":        ["kündigung"],
    "Haftung":                 ["haftung"],
    "Haftungsbegrenzung":      ["haftungsbegrenzung", "haftungsbeschränkung",
                                "haftungsausschluss", "haftung"],
    "Versicherung":            ["versicherung", "haftpflicht"],
    "Verfügbarkeitsgarantie":  ["verfügbarkeit", "availability"],
    "Reaktionszeiten":         ["reaktionszeit", "sla", "service level"],
    "Pönale/Bonus-Malus":      ["pönale", "bonus", "malus", "vertragsstrafe"],
    "Force Majeure":           ["force majeure", "höhere gewalt"],
    "Rückbauverpflichtung":    ["rückbau", "demontage", "wiederherstellung"],
    "Standsicherheitsnachweis":["standsicherheit"],
    "Wegerecht/Zufahrt":       ["wegerecht", "zufahrt", "zugang"],
    "Übertragung/Sukzession":  ["übertragung", "sukzession", "abtretung"],
    "Vorkaufsrecht":           ["vorkaufsrecht"],
    "Genehmigungsrisiko":      ["genehmigung", "bimschg"],
    "Untervermietung/Überlassung": ["untervermiet", "überlassung"],
    "Grunddienstbarkeit":      ["dienstbarkeit", "grunddienstbarkeit"],
    "Nutzungsumfang":          ["nutzungsumfang", "gegenstand", "nutzung"],
    "Leistungsumfang":         ["leistungsumfang", "umfang", "instandhaltung", "wartungsumfang"],
    "Ersatzteilversorgung":    ["ersatzteil"],
    "Anschlusspunkt":          ["anschlusspunkt", "netzanschluss", "netzverknüpfung"],
    "Einspeiseleistung":       ["einspeise"],
    "EEG-Vergütung / Marktprämie": ["eeg", "marktprämie"],
    "Mess- und Abrechnungsmodalitäten": ["abrechnung", "messung", "messkonzept"],
    "Curtailment / Einspeisemanagement": ["curtailment", "einspeisemanagement", "einsman"],
    "Bilanzkreismanagement":   ["bilanzkreis"],
    "Abnahmeverpflichtung":    ["abnahme", "take-or-pay"],
    "Marktprämie":             ["marktprämie"],
    "Lieferprofil":            ["lieferprofil", "baseload", "as-produced", "as-forecasted"],
    "Herkunftsnachweise":      ["herkunftsnachweis", "guarantees of origin", "goo"],
    "Change of Law":           ["change of law", "gesetzliche änderung"],
    "Kaufgegenstand":          ["kaufgegenstand"],
    "Kaufpreis":               ["kaufpreis"],
    "Garantien":               ["garantie", "zusicherung", "warranties"],
    "Closing-Bedingungen":     ["closing", "conditions precedent", "vollzugsvoraussetzung"],
    "Übergangsstichtag":       ["übergangsstichtag", "übergabestichtag", "stichtag",
                                "gefahrenübergang"],
    "Vertraulichkeit":         ["vertraulichkeit", "geheimhaltung", "nda"],
}


def topic_covered(topic: str, seen_clause_types: Iterable[str]) -> bool:
    """True iff any synonym for ``topic`` is a substring of any clause
    type seen in the contract. Falls back to a direct substring search
    on ``topic.lower()`` for topics not in ``TOPIC_SYNONYMS``."""
    patterns = TOPIC_SYNONYMS.get(topic) or [topic.lower()]
    seen = list(seen_clause_types)
    return any(pat in s for pat in patterns for s in seen)
