"""Deterministic table reconciliation for German financial contracts.

The LLM never does the arithmetic; it only interprets the findings this
module produces. See docs/analysis/CONTRACT_ANALYZER_V2.md §7.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from lai.analyzer.schema import FinancialTable, ReconciliationFinding


# ---------------------------------------------------------------------------
# German number parsing
# ---------------------------------------------------------------------------

_NUMBER_RUN_RE = re.compile(r"-?\d(?:[\d.,\s ]*\d)?")
_CURRENCY_TOKENS = ("€", "EUR", "Euro", "USD", "$")
_PERCENT_RE = re.compile(r"%|\bProzent\b", re.IGNORECASE)


def parse_german_number(s: str) -> Optional[float]:
    """Parse '1.234,56' → 1234.56, '1,5%' → 1.5, '€ 12.500' → 12500.0.

    Returns None when the string contains no recognizable number. Accepts
    both German (1.234,56) and US-style (1,234.56) because Docling output
    sometimes mixes them when extracting tables from tagged PDFs.

    Disambiguation rules when a single separator is present:
      - "1.234"   → 1234     (3 trailing digits → thousands)
      - "12.5"    → 12.5     (non-3 trailing digits → decimal dot)
      - "1,234"   → 1.234    (default to German decimal comma)
      - "1,5"     → 1.5      (decimal comma)
    """
    if s is None:
        return None
    raw = str(s).strip()
    if not raw:
        return None

    cleaned = raw
    for tok in _CURRENCY_TOKENS:
        cleaned = cleaned.replace(tok, "")
    cleaned = _PERCENT_RE.sub("", cleaned).strip()

    m = _NUMBER_RUN_RE.search(cleaned)
    if not m:
        return None
    candidate = re.sub(r"[\s ]", "", m.group(0))

    has_comma = "," in candidate
    has_dot = "." in candidate
    if has_comma and has_dot:
        # Rightmost separator is the decimal one
        if candidate.rfind(",") > candidate.rfind("."):
            candidate = candidate.replace(".", "").replace(",", ".")
        else:
            candidate = candidate.replace(",", "")
    elif has_comma:
        # Single comma → German decimal
        candidate = candidate.replace(",", ".")
    elif has_dot:
        # Single dot — could be German thousands or US decimal.
        # If exactly one dot and exactly 3 trailing digits → thousands.
        if candidate.count(".") == 1:
            head, tail = candidate.split(".")
            if len(tail) == 3 and tail.isdigit():
                candidate = head + tail
        # Otherwise leave as-is (US decimal)

    try:
        return float(candidate)
    except ValueError:
        return None


def detect_currency(*texts: str) -> str:
    blob = " ".join(t for t in texts if t)
    if "€" in blob or "EUR" in blob or "Euro" in blob.lower():
        return "EUR"
    if "$" in blob or "USD" in blob:
        return "USD"
    return "EUR"


# ---------------------------------------------------------------------------
# Table normalization
# ---------------------------------------------------------------------------

_TOTAL_LABELS = (
    "summe", "gesamt", "gesamtsumme", "gesamtbetrag", "total",
    "endbetrag", "rechnungsbetrag",
)
_VAT_LABELS = ("ust", "umsatzsteuer", "mwst", "mehrwertsteuer", "vat")
_NET_LABELS = ("netto",)
_GROSS_LABELS = ("brutto",)


def _row_label(row: dict) -> str:
    """First non-empty cell, lowercased — best heuristic for row label."""
    for v in row.values():
        if v and str(v).strip():
            return str(v).strip().lower()
    return ""


def _row_numeric(row: dict) -> Optional[float]:
    """Last numeric cell in the row — typically the amount column."""
    last: Optional[float] = None
    for v in row.values():
        n = parse_german_number(v) if v is not None else None
        if n is not None:
            last = n
    return last


def _is_total_row(label: str) -> bool:
    return any(t in label for t in _TOTAL_LABELS)


def _is_vat_row(label: str) -> bool:
    return any(t in label for t in _VAT_LABELS)


def _is_net_row(label: str) -> bool:
    return any(t in label for t in _NET_LABELS) and "brutto" not in label


def _is_gross_row(label: str) -> bool:
    return any(t in label for t in _GROSS_LABELS)


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _SeverityBands:
    abs_info: float = 0.5
    abs_low: float = 5.0
    abs_med: float = 100.0
    rel_info: float = 0.001   # 0.1%
    rel_low: float = 0.005    # 0.5%
    rel_med: float = 0.01     # 1.0%


_BANDS = _SeverityBands()


def _classify(stated: float, computed: float) -> tuple[str, float, float]:
    delta = stated - computed
    abs_d = abs(delta)
    rel_d = abs_d / max(abs(stated), 1e-9)

    def band_abs() -> str:
        if abs_d <= _BANDS.abs_info:
            return "info"
        if abs_d <= _BANDS.abs_low:
            return "low"
        if abs_d <= _BANDS.abs_med:
            return "medium"
        return "high"

    def band_rel() -> str:
        if rel_d <= _BANDS.rel_info:
            return "info"
        if rel_d <= _BANDS.rel_low:
            return "low"
        if rel_d <= _BANDS.rel_med:
            return "medium"
        return "high"

    order = {"info": 0, "low": 1, "medium": 2, "high": 3}
    chosen = max(band_abs(), band_rel(), key=lambda b: order[b])
    return chosen, abs_d, rel_d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reconcile_table(
    title: str,
    rows: list[dict],
    currency: Optional[str] = None,
) -> tuple[FinancialTable, list[ReconciliationFinding]]:
    """Reconcile one Docling-extracted table.

    Returns (table_with_totals, findings). ``findings`` is empty when the
    arithmetic checks out (or when no sufficient signal exists to check).
    """
    findings: list[ReconciliationFinding] = []
    line_items: list[float] = []
    stated_total: Optional[float] = None
    net_value: Optional[float] = None
    vat_value: Optional[float] = None
    gross_value: Optional[float] = None

    for row in rows:
        label = _row_label(row)
        amount = _row_numeric(row)
        if amount is None:
            continue
        if _is_total_row(label) or _is_gross_row(label):
            stated_total = amount
            if _is_gross_row(label):
                gross_value = amount
        elif _is_vat_row(label):
            vat_value = amount
        elif _is_net_row(label):
            net_value = amount
        else:
            line_items.append(amount)

    computed_total: Optional[float] = sum(line_items) if line_items else None
    cur = currency or detect_currency(title, *(str(v) for r in rows for v in r.values()))

    # Sum check — line items vs stated total
    if stated_total is not None and computed_total is not None:
        sev, abs_d, rel_d = _classify(stated_total, computed_total)
        if sev != "info":
            findings.append(ReconciliationFinding(
                table_title=title,
                kind="sum_mismatch",
                stated=stated_total,
                computed=computed_total,
                delta=stated_total - computed_total,
                severity=sev,  # type: ignore[arg-type]
                note=(
                    f"Posten summieren auf {computed_total:.2f} {cur}; "
                    f"ausgewiesener Gesamtbetrag {stated_total:.2f} {cur} "
                    f"(Δ {abs_d:.2f}, {rel_d*100:.2f}%)."
                ),
            ))

    # VAT check — net + vat ≈ gross/total
    if net_value is not None and vat_value is not None:
        target = gross_value if gross_value is not None else stated_total
        if target is not None:
            computed = net_value + vat_value
            sev, abs_d, rel_d = _classify(target, computed)
            if sev != "info":
                # If the VAT rate looks off, label it vat_mismatch; otherwise sum
                inferred_rate = vat_value / net_value if net_value else 0.0
                kind = "vat_mismatch" if abs(inferred_rate - 0.19) > 0.005 and abs(inferred_rate - 0.07) > 0.005 else "sum_mismatch"
                findings.append(ReconciliationFinding(
                    table_title=title,
                    kind=kind,  # type: ignore[arg-type]
                    stated=target,
                    computed=computed,
                    delta=target - computed,
                    severity=sev,  # type: ignore[arg-type]
                    note=(
                        f"Netto {net_value:.2f} + USt {vat_value:.2f} = "
                        f"{computed:.2f} {cur}; ausgewiesen {target:.2f} {cur} "
                        f"(Δ {abs_d:.2f}, {rel_d*100:.2f}%, "
                        f"impliziter Steuersatz {inferred_rate*100:.2f}%)."
                    ),
                ))

    table = FinancialTable(
        title=title,
        rows=rows,
        stated_total=stated_total,
        computed_total=computed_total,
        discrepancy=(
            stated_total - computed_total
            if stated_total is not None and computed_total is not None
            else None
        ),
        currency=cur,
    )
    return table, findings


_TOC_LABELS = ("seite", "page", "s.", "page no", "seitenzahl")
_TOC_TITLE_HINTS = ("inhaltsverzeichnis", "inhalt", "table of contents", "toc")


def _looks_like_toc(title: str, rows: list[dict]) -> bool:
    """Heuristic: skip Docling-extracted tables of contents.

    PDFs frequently have their TOC parsed as a two-column table. If we
    treat it as financial, the reconciler sums page numbers and reports
    bogus 'totals'. Triggers when ANY of:
      - title contains 'Inhalt'/'TOC'
      - column header looks like 'Seite'/'Page'
      - >70% of numeric cells are small integers (≤ ~500, plausible page numbers)
    """
    t = (title or "").lower()
    if any(h in t for h in _TOC_TITLE_HINTS):
        return True
    if not rows:
        return False
    # Column-header check
    headers = list(rows[0].keys())
    for h in headers:
        if h is None:
            continue
        hl = str(h).strip().lower()
        if hl in _TOC_LABELS:
            return True
    # Numeric distribution check
    nums: list[float] = []
    for row in rows:
        for v in row.values():
            n = parse_german_number(v) if v is not None else None
            if n is not None:
                nums.append(n)
    if len(nums) < 3:
        return False
    small_int = sum(1 for n in nums if abs(n) < 500 and abs(n - round(n)) < 1e-6)
    return (small_int / len(nums)) > 0.7


def reconcile_all(
    docling_tables: Iterable[dict],
) -> tuple[list[FinancialTable], list[ReconciliationFinding]]:
    """Reconcile every table from a Docling document.

    Each ``docling_tables`` element is expected to have ``title`` and
    ``rows`` keys; ``rows`` is a list of dicts (column → cell text).
    Tables of contents are detected and skipped.
    """
    out_tables: list[FinancialTable] = []
    out_findings: list[ReconciliationFinding] = []
    for t in docling_tables:
        title = str(t.get("title") or t.get("caption") or "Tabelle")
        rows = t.get("rows") or []
        if not rows or _looks_like_toc(title, rows):
            continue
        table, findings = reconcile_table(title, rows)
        out_tables.append(table)
        out_findings.extend(findings)
    return out_tables, out_findings
