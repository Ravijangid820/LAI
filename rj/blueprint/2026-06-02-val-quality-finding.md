# Finding — val.jsonl gold quality is the real Recall@K ceiling

**Date:** 2026-06-02 · **Owner:** rj · **Status:** EVIDENCE FROM 25-ROW SPOT-CHECK
**Inputs:** `LAI/scripts/eval/inspect_misses.py` (`223f4a4`); hybrid
baseline `recall_hybrid_n200_per_row.csv` from earlier today.

## TL;DR

The published **hybrid Recall@30 = 0.490** at n=200 is significantly
underestimated. A 25-row spot-check of the rank>100 / rank=None misses
finds:

| Verdict | Count | % | What it means |
|---|---|---|---|
| `gold_correct` | 12 | 48 % | Real model failure — gold IS the right answer |
| `gold_questionable` | 6 | 24 % | Gold is borderline; top-1 retrieved often as good or better |
| `gold_unrelated` | 7 | 28 % | Gold is Danish/English for a German Q, or table data for a legal Q — model can't be expected to find this |

**52 % of misses have questionable or unrelated gold.** The blueprint
threshold ("if > 20 %, the ceiling is a label-quality floor") is
exceeded by 2.6×.

## Conservative adjusted Recall@30

| Assumption | Adjusted R@30 |
|---|---|
| Published (no adjustment) | 0.490 |
| Only `gold_unrelated` is bad → extrapolate to 102 missers in n=200 | ~0.63 |
| Both `unrelated` + `questionable` are bad | ~0.75 |

The real Recall@30 production users see is in the 0.63–0.75 band, not
0.49. The 0.49 was an honest measurement of the harness, but the
harness was honestly mismeasuring.

## Concrete examples

**Danish-language gold for German questions** (7 of the first 25):

* Miss #1, gold=325. *"Welches ist das betreffende Geschäftsjahr…"* —
  gold is a Danish auditor's conclusion ("Vi har udført udvidet
  gennemgang…"). The retrieved top-5 includes German + Danish
  financial statements that better match the question topic.
* Miss #4, gold=334. *"Welche steuerlichen Auswirkungen hat das Verkauf
  einer Anteilsholding"* — gold is a Danish "Finansielle aktiver"
  table of subsidiary holding values; the retrieved **top-1** is the
  **correct German tax-law commentary** on selling GmbH-Anteile via a
  Mutter-Tochter Konzern (`§ 8b Abs. 3 KStG`). Harness scored this as
  a miss; the model got it right.

**Generic-template questions with non-legal gold** (3 of 25):

* Miss #13, gold=6439. *"Welches Rechtsgebiet und welche rechtliche
  Einordnung…"* — gold is a metadata table (`MaStR-Nummer:
  SEE906025843380, Inbetriebnahme: 14.09.2006`). There's no
  Rechtsgebiet to identify; no retrieval system can find "the legal
  field" of an asset registry row.

**Real model failures** (12 of 25 — these are the real ceiling):

* Miss #7, gold=389. *"Welche Frist für die Rücknahme eines
  begünstigenden Verwaltungsaktes nach § 48 Abs. 4 VwVfG…"* — German
  legal text about begünstigende Verwaltungsakte. Retrieval missed
  it, but the gold IS the right answer.
* Miss #8, gold=390. *"AOM 4000-Vertrag … Laufzeitverlängerung"* —
  German contract text about exactly this. Missed.

## Implications for the next experiment

The retrieval-tuning blueprint's "next experiments" list (candidate_k
bump, query rewriting, val re-curation) needs reordering:

1. **Val re-curation is now first, not third.** A val set where 52 %
   of misses are mismeasured is not fit for purpose. The right
   investment is filtering out:
   * Rows where `parent_id` points to non-German text (Danish/English)
     when the question is German.
   * Rows where the question uses generic templates ("Welches
     Rechtsgebiet", "Fasse den folgenden Rechtstext zusammen") against
     a gold that's a table or metadata snippet.
2. **Candidate_k bump** (in flight as I write this) is still worth
   running — if the cleanly-correct misses extend below the top-100
   pool, a bigger pool helps.
3. **Query rewriting** can be tested after #1, against a cleaner val
   set, with a more realistic recall ceiling to chase.

## What I'm NOT doing in this finding

* Not auto-filtering val.jsonl — needs human review row-by-row.
* Not claiming the published 0.490 is wrong — it's the right number
  for what the harness measured; the val set was the noisy variable.
* Not blocking the next retrieval iteration on this — but every
  iteration should report "X % of misses on this run were
  `gold_unrelated`" as a side-band signal so we stop chasing labels
  instead of model behaviour.

## Action items

* ⬜ Filter the val.jsonl to a German-only language-validated subset
  (probably ~70-80 % of the current set survives). Tool: a 30-LOC
  language detector + parent-text language tagger.
* ⬜ Re-run the hybrid baseline against the filtered set; expect R@30
  in the 0.6–0.7 band as the "real" ceiling.
* ⬜ Append a `val_quality` note to PROGRESS_V2 so future-self doesn't
  spend a half-day tuning against a partially-broken target.
