# Pilot offer terms — DRAFT

**Status:** DRAFT for boss/rj review. **Important:** pricing, length,
and commercial terms are commercial decisions. I've drafted what's
defensible from an engineering-readiness standpoint; you and the
boss decide what's strategic.

---

## Default offer (the version I'd recommend if no other constraints)

### Duration
**6 weeks** active pilot + 2 weeks ramp/wind-down. Total 8 weeks
calendar.

* Weeks 1-2 (ramp): onboarding, user accounts created, first
  documents uploaded, lawyers familiarise with UI.
* Weeks 3-6 (active): real use on real work, weekly check-ins, bug
  triage.
* Week 7: structured feedback collection.
* Week 8: closing conversation — pilot extension, reference-customer
  agreement, or amicable parting.

### Cost
**€0 to the firm during the pilot window.**

Rationale: we need the pilot more than they do right now. Pricing the
pilot would slow the conversation and reduce the firm's flexibility
to use us on whatever matter they choose.

### What the firm gets

**Access:**
* Full chat path (chat / RAG / contract / contract+RAG modes)
* DDiQ report generator for structured DD output
* `/eval` lawyer-blind A/B route (Phase 3 evaluation gate — they'll
  see it once we run it on their matter)
* Admin audit log at `/dashboard/admin/audit`

**SLAs during the pilot:**
* `/health` uptime target: 99 % weekly (hourly smoke cron is our
  canary; recovery via systemd or manual restart within 1 h of
  detection)
* Chat query response: p95 ≤ 20 s (smoke baseline is 13.9 s on a
  warm cache)
* Bug-triage response: within 24 h of report
* Fix delivery (sev-1): within 3 working days
* Fix delivery (sev-2): within 7 working days

**Support:**
* Weekly 30-minute video check-in with rj (engineering)
* Slack channel or email loop with boss (sales / commercial)
* Direct line for sev-1 bugs

**Data:**
* Their documents stored in a per-organisation-isolated Matter view
* No training reuse without WRITTEN consent
* Pilot-period audit log retained 6 months post-pilot (EU AI Act
  Art. 12 minimum); firm can request earlier export + purge

### What we ask in return

**Mandatory:**
* **1 real matter** — wind park DD, repowering deal, ongoing dispute,
  or operational contract review. Volume target: 50-200 documents.
* **2-3 lawyers** as named pilot users, willing to put real work
  through the system.
* **30-60 minutes/week** of structured feedback per active lawyer
  (template provided below).
* **One closing conversation** at week 8.

**Optional but strongly preferred:**
* **Permission to name them as a reference customer** in subsequent
  pilots / commercial pitches (with veto right on the specific
  context).
* **Two pilot users available for a lawyer-blind A/B eval session**
  during weeks 3-4 — the §3.4 BImSchG 50-question evaluation we built
  in vm-9. This is a one-shot ~90-minute session, and gives us the
  pre/post LoRA training comparison we can't get any other way.

**Nice-to-have, not required:**
* Permission to publish a case-study after the pilot (with full
  approval right on every word; anonymized if preferred).
* Quote-attributable feedback from a named partner.

### Out of scope

We don't sign:
* SLAs stronger than the defaults above (this is a pilot, not GA)
* Indemnification clauses (we don't have a balance sheet to back them)
* Custom feature commits (we accept feature *requests*; delivery
  timing is engineering's call)
* Source-code escrow or IP-transfer terms

If the firm needs any of those, the conversation needs to escalate
to a commercial contract — not a pilot.

---

## Variants — if the standard offer doesn't fit

### Variant A: "Trial-then-commercial"

**Use when:** firm wants to commit faster, has procurement that needs
a price tag for budget reasons.

**Shape:**
* 4 weeks free, then 4 weeks at 50% of intended commercial price
  (which is also TBD).
* Closing conversation moves to "are we buying for year 2?"
* Use rate: capped at typical pilot volume to prevent abuse.

### Variant B: "Per-matter, no time limit"

**Use when:** firm has ONE big matter they want to throw at us, and
"6 weeks" doesn't match the matter's natural lifecycle.

**Shape:**
* Free use for one named matter, no time bound, until that matter
  closes.
* "Closing conversation" trigger = matter conclusion or 6 months,
  whichever first.
* Lower SLA bar (we treat this more like a research engagement than
  a production pilot).

### Variant C: "On-premise pilot"

**Use when:** firm's IT-Sicherheit says "no public cloud."

**Shape:**
* We deploy LAI on their infrastructure (Docker compose or
  Kubernetes — we have both wired).
* Additional 2-week setup buffer.
* They cover infrastructure costs (estimated €200-500/month for the
  compute footprint).
* All other terms as default.

**Engineering caveat:** we've tested on-premise deployment in
sandbox; never run it under a customer's IT-policy. First on-prem
pilot will surface integration friction. Reflect this in the
expectations conversation.

---

## Feedback collection template

Provided to pilot lawyers, filled in each week.

```
WEEK {n} FEEDBACK — {lawyer name} — {date}

1. Matters worked on this week:
   - [Free text]

2. What worked well:
   - [Specific examples — exact question, exact answer, why useful]

3. What didn't work:
   - [Specific examples — exact question, what we expected, what we got, why wrong]
   - [Categorisation: routing / retrieval / generation / UI / latency / other]

4. What would have been most valuable but isn't there:
   - [Feature requests, in plain language — we translate to engineering]

5. Confidence on a single matter, 0-10:
   - [Number, plus one-sentence why]

6. Would you recommend the closing decision be "extend the pilot",
   "convert to commercial", or "stop here"? (No wrong answer.)
   - [Answer + one-sentence reasoning]
```

This template — collected weekly — gives engineering the signal we
need to prioritise the next 4-week sprint after the pilot, AND gives
the boss the signal needed for the commercial conversation.

---

## Success metrics — how WE judge the pilot

These are internal. The firm's "would you renew" is the most
important signal. But internally we look for:

| Metric | Target |
|---|---|
| Lawyers logged in ≥ 4 of 6 weeks | 2 of 3 |
| Real queries per active lawyer per week | ≥ 10 |
| Reported bugs vs. expected (given system maturity) | within ±50 % of internal estimate |
| Routing failures detected | ≤ 1 per 100 queries (current production rate per the audit work) |
| `(unbelegt)` flagged sentences per matter | tracked, not gated — useful telemetry |
| Citation accuracy on lawyer spot-check | ≥ 90 % of `[C-n]` and `[M-n]` handles resolve to the right chunk |
| At least one "this saved me ≥ 4 hours" quote | 1 of 3 lawyers |

If we hit those bars and the firm says "let's not continue" — we've
still got referenceable data. If we miss them, we've got the next
4-week sprint's roadmap.
