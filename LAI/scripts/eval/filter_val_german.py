"""Filter val.jsonl to rows whose gold parent text is German.

The 2026-06-02 spot-check found 28 % of hybrid misses had Danish or
English gold against a German question — unmeasurable from the model
side, since no retrieval system can be expected to surface a Danish
financial table when the question is in German. This script writes a
filtered val set that keeps only rows where the gold parent text is
reliably German.

Detection heuristic (parent-text-grade, not question-grade — the
in-tree ``_detect_question_language`` is tuned for short queries):

* Strong German signal: umlauts (ä ö ü ß) and one or more of the most
  common German function tokens (der, die, das, und, ist, …) AND
  enough text to be meaningful.
* Strong **non-German** signal that forces rejection: Danish-specific
  letters (ø, å, æ) OR Danish-only function tokens (vi, har, vores,
  er, det, om), even if a few German hint words also appear.

Conservative by design — a row is kept ONLY if the German signal is
clear AND no strong non-German signal exists. Borderline rows
(generic table data, very short text) are dropped rather than kept,
because false-positives degrade the val set the same way they
degraded the original.

Usage
-----
::

    python -m scripts.eval.filter_val_german \\
        --val training/fine_tuning/data/val.jsonl \\
        --output training/fine_tuning/data/val_de.jsonl \\
        --max-rows 10000
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from lai.common.retrieval import RetrievalClient

LAI_DIR = Path(__file__).resolve().parents[2]
DEFAULT_VAL = LAI_DIR / "training" / "fine_tuning" / "data" / "val.jsonl"


# ── Language signals ────────────────────────────────────────────────────


_GERMAN_HINT_WORDS = frozenset(
    {
        "der", "die", "das", "den", "dem", "des",
        "und", "oder", "auch", "nicht",
        "ist", "sind", "war", "waren", "wird", "werden", "wurde",
        "ein", "eine", "einen", "einem", "einer", "eines",
        "für", "von", "mit", "auf", "bei", "nach", "über", "unter",
        "sich", "kann", "soll", "muss", "darf", "möchte",
        "im", "am", "vom", "zur", "zum",
        "vertrag", "vertrages", "vertraglich",
        "gesetz", "gesetze", "gesetzlich",
        "genehmigung", "anlage", "anlagen",
    }
)

# Danish letters that almost never appear in German legal text.
_DANISH_LETTERS = re.compile(r"[øåæ]")

# Danish-specific tokens — high-confidence reject signal even alongside
# German-looking text (mixed-language docs exist; we want monolingual).
_DANISH_TOKENS = frozenset(
    {
        "vi", "har", "er", "det", "om", "som", "vores", "vor",
        "ikke", "også", "men", "kan", "skal", "bliver",
        "konklusion", "udført", "udvidet", "gennemgang",
        "årsregnskabet", "regnskabsåret",
        "udtalelse", "ledelsesberetningen", "ledelsen", "ansvarlig",
        "resultatopgørelse", "resultatdisponering",
        "indregning", "balancen", "aktiver", "forpligtelser",
        "kapitalandele", "ejerandel", "kostpris",
    }
)

# English tokens — same rationale; if many appear in legal-section text,
# the gold is an English DRL or summary, not a German legal text.
_ENGLISH_TOKENS = frozenset(
    {
        "the", "and", "of", "in", "to", "is", "are", "was", "were",
        "this", "that", "these", "those",
        "permit", "permits", "license", "licenses", "wind", "farm",
        "agreement", "contract", "consent", "consents",
        "please", "provide", "request", "comment",
        "company", "operation", "operations",
    }
)


def _classify_text(text: str) -> str:
    """Return ``"de"`` / ``"non_de"`` / ``"unknown"``."""
    if not text or len(text) < 100:
        return "unknown"
    lower = text.lower()
    toks = re.findall(r"[a-zäöüßøåæ]+", lower)
    if len(toks) < 20:
        return "unknown"

    # Hard reject on Danish letters present in any meaningful density.
    danish_letters = len(_DANISH_LETTERS.findall(lower))
    if danish_letters >= 3:
        return "non_de"

    danish_hits = sum(1 for t in toks if t in _DANISH_TOKENS)
    if danish_hits >= 3:
        return "non_de"

    english_hits = sum(1 for t in toks if t in _ENGLISH_TOKENS)
    german_hits = sum(1 for t in toks if t in _GERMAN_HINT_WORDS)
    umlauts = bool(re.search(r"[äöüß]", lower))

    # English-dominated text: more English tokens than German, AND
    # no umlauts. The umlaut check rescues mixed German-with-English-
    # quotes legal commentary.
    if english_hits > german_hits and not umlauts:
        return "non_de"

    # German-positive: needs hint words OR umlauts, plus enough length.
    if german_hits >= 3 or (umlauts and german_hits >= 1):
        return "de"

    return "unknown"


# ── Main ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--val", type=Path, default=DEFAULT_VAL)
    ap.add_argument(
        "--output",
        type=Path,
        required=True,
        help="output path for the filtered jsonl (do not overwrite val.jsonl)",
    )
    ap.add_argument(
        "--max-rows",
        type=int,
        default=10000,
        help="stop reading after this many input rows (default 10000)",
    )
    args = ap.parse_args(argv)

    if args.output.resolve() == args.val.resolve():
        print("--output must differ from --val; refuse to overwrite source")
        return 2

    # Load val rows we can classify
    raw_rows: list[dict] = []
    with args.val.open("r", encoding="utf-8") as fh:
        for line in fh:
            if len(raw_rows) >= args.max_rows:
                break
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("parent_id") is None:
                continue
            raw_rows.append(d)
    print(f"[load] {len(raw_rows)} val rows with parent_id (from {args.val})")

    # Batch-fetch gold parent texts
    gold_pids = sorted({int(d["parent_id"]) for d in raw_rows})
    print(f"[fetch] {len(gold_pids)} unique gold parents from pgvector …")
    client = RetrievalClient()
    try:
        texts = client.fetch_parent_texts(gold_pids)
    finally:
        client.close()
    print(f"  {len(texts):,} parents resolved (missing: {len(gold_pids) - len(texts):,})")

    # Classify and filter
    counts = {"de": 0, "non_de": 0, "unknown": 0, "not_in_corpus": 0}
    kept: list[dict] = []
    for d in raw_rows:
        pid = int(d["parent_id"])
        text = texts.get(pid)
        if text is None:
            counts["not_in_corpus"] += 1
            continue
        verdict = _classify_text(text)
        counts[verdict] += 1
        if verdict == "de":
            kept.append(d)

    print(
        f"\n=== classification breakdown ===\n"
        f"  de          {counts['de']:>5}  → kept\n"
        f"  non_de      {counts['non_de']:>5}  → dropped (Danish / English)\n"
        f"  unknown     {counts['unknown']:>5}  → dropped (too short / ambiguous)\n"
        f"  no parent   {counts['not_in_corpus']:>5}  → dropped (stale val gold)"
    )
    keep_frac = len(kept) / len(raw_rows) if raw_rows else 0.0
    print(f"\nkept {len(kept)}/{len(raw_rows)} rows ({100 * keep_frac:.1f} %)")

    # Write filtered jsonl
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for d in kept:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"→ wrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
