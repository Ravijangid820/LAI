"""Unit tests for ``scripts.eval.filter_val_german._classify_text``.

The val.jsonl quality filter only adds value if it correctly:

* Classifies clearly-German legal text as ``de`` (no false negatives that
  shrink the kept set artificially).
* Classifies Danish auditor text and English DRLs as ``non_de`` (no
  false positives that re-introduce the very rows we're trying to
  drop).
* Classifies too-short or table-only text as ``unknown`` (safer to
  drop than to guess).

Each test sources an example from the 2026-06-02 spot-check audit so
the classifier's correctness is checked against the same data that
motivated it.
"""

from __future__ import annotations

import os

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-filter-val-0123456789abcdef")

import pytest

from lai.search.val_language import classify_text as _classify_text

pytestmark = pytest.mark.unit


# ── German legal text — must be kept ────────────────────────────────────


GERMAN_LEGAL_TEXTS = [
    # Real BImSchG-style commentary (miss #7 gold in the spot-check)
    """Für begünstigende Verwaltungsakte wie der Genehmigung begrenzt der
    Gesetzgeber die Aufhebungsbefugnis durch die erlassende Behörde
    (§ 48 Abs. 1 Satz 2 VwVfG). Das schutzwürdige Interesse des
    Begünstigten am Fortbestand des Verwaltungsaktes kann einer
    Aufhebung entgegenstehen (§ 48 Abs. 2 und 4 VwVfG).""",
    # Real Pachtvertrag clause (miss #19 gold)
    """Nutzungsentgelt: 4% der Stromerlöse, mind. aber EUR 11.760,00. Ab 11.
    vollen Betriebsjahr zusätzlich 2% der Stromerlöse, mind. aber EUR
    5.880,00/WEA. Ab 16. vollen Betriebsjahr weiteres zusätzliches
    Entgelt von 2%, mind. aber EUR 5.880,00/WEA.""",
    # AOM 4000-Vertrag (miss #8 gold)
    """Die K/S Wind Partner 33 hat am 26.06./31.07.2012 mit der Vestas
    Deutschland GmbH einen AOM 4000-Vertrag über die Wartung der WEA
    ab Oberkante Fundament abgeschlossen. Die garantierte technische
    Einzelverfügbarkeit beträgt 95 %. Vertragsbeginn ist der
    04.06.2014, die Laufzeit 10 Jahre.""",
]


@pytest.mark.parametrize("text", GERMAN_LEGAL_TEXTS)
def test_keeps_german_legal_text(text: str) -> None:
    assert _classify_text(text) == "de"


# ── Danish auditor text — must be dropped ────────────────────────────────


DANISH_TEXTS = [
    # Miss #1 gold — Danish auditor's conclusion
    """Konklusion Vi har udført udvidet gennemgang af årsregnskabet for for
    regnskabsåret -, der omfatter resultatopgørelse, balance,
    egenkapitalopgørelse og noter, herunder anvendt regnskabspraksis.
    Årsregnskabet udarbejdes efter årsregnskabsloven.""",
    # Miss #2 gold — Udtalelse om ledelsesberetningen
    """Udtalelse om ledelsesberetningen Ledelsen er ansvarlig for
    ledelsesberetningen. Vores konklusion om årsregnskabet omfatter
    ikke ledelsesberetningen, og vi udtrykker ingen form for konklusion
    med sikkerhed om ledelsesberetningen.""",
    # Miss #5 gold — accounting principles
    """Generelt om indregning og måling Aktiver indregnes i balancen, når
    det som følge af en tidligere begivenhed er sandsynligt, at
    fremtidige økonomiske fordele vil tilflyde virksomheden, og
    aktivets værdi kan måles pålideligt.""",
]


@pytest.mark.parametrize("text", DANISH_TEXTS)
def test_drops_danish_auditor_text(text: str) -> None:
    assert _classify_text(text) == "non_de"


# ── English text — must be dropped ──────────────────────────────────────


ENGLISH_TEXTS = [
    # Miss #6 gold — DRL request list
    """Document Request List concerning Wind Park Projects (hereinafter SPV)
    Please provide the following documents or information. Please make
    a note if a respective request is not applicable. Permitting.
    Copies of all licenses, permits or consents, including all
    requirements and auxiliary conditions as well as alterations,
    approvals, certificates, specifications, qualifications.""",
    # Miss #10 gold — English BImSchG permit summary
    """The Hude Wind Farm is covered by a permit under the German Immission
    Control Act (BImSchG) issued by the district of Oldenburg and
    dated 12 June 2002 as well as a corresponding amendment permit
    dated 10 October 2002 covering a change in turbine type. The
    Hatten Wind Farm is covered by a BImSchG permit issued by the
    district of Oldenburg and dated 19 June 2002.""",
]


@pytest.mark.parametrize("text", ENGLISH_TEXTS)
def test_drops_english_text(text: str) -> None:
    assert _classify_text(text) == "non_de"


# ── Too-short / table-only / metadata — must be unknown ──────────────────


def test_returns_unknown_for_short_text() -> None:
    assert _classify_text("Nein.") == "unknown"
    assert _classify_text("§ 1 BImSchG") == "unknown"


def test_returns_unknown_for_pure_table_data() -> None:
    # Pure numeric table — no function words, no umlauts.
    text = "10 20 30 40 50 60 70 80 90 100 EUR EUR 2018 2019 2020"
    assert _classify_text(text) == "unknown"


# ── Mixed-language defense ──────────────────────────────────────────────


def test_german_with_english_legal_quotes_still_de() -> None:
    """German legal text often quotes English DRL items inline.
    Umlauts + several German function words should rescue it."""
    text = (
        "Der Vertrag enthält folgende Klausel: 'The Operator shall provide "
        "monthly reports of the wind farm performance.' Die Frist für die "
        "Übermittlung ist auf 30 Tage ab Monatsende festgelegt. § 4 Abs. 2 "
        "des Vertrags regelt die Berichtspflichten."
    )
    assert _classify_text(text) == "de"


def test_danish_with_some_german_words_still_non_de() -> None:
    """Danish dominance must win over a few German hint words — the
    hard reject on Danish letters / tokens cannot be overridden."""
    text = (
        "Vores konklusion er at årsregnskabet for regnskabsåret 2020 ikke "
        "indeholder væsentlig fejlinformation. Der Vertrag wurde im Datenraum "
        "abgelegt. Ledelsesberetningen er ansvarlig for ledelsen."
    )
    assert _classify_text(text) == "non_de"
