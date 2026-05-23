# LAI — Architecture Brief (for the boss)

**Date:** 2026-05-14
**Locked decisions:** (1) No further budget — public/free data sources only.
(2) Everything on-prem — no cloud.

This brief explains what LAI is built from today, *why the current structure
causes the problems seen in the smoke test*, and what we are changing it to.
Everything here is verified against the code and the running system.

---

## 0. What the two decisions change (and why neither hurts)

**On-prem only** — LAI already runs on our 2-GPU box; the models, databases and
backends are all on-prem today. The *only* real change: the **web frontend must
move on-prem too** (it currently has Vercel/Cloudflare deployment config — we
delete that and serve it from the box behind a normal web server). This actually
**strengthens** the pitch: "your contracts never leave the building" is a
genuine selling point for German law firms. The honest trade-off: scaling is
bounded by one box — we solve that with efficiency, not more hardware.

**No further budget** — the *high-value* external data sources are already free
and public:
- **Marktstammdatenregister (MaStR)** — free public API. Confirms turbine
  registration, commissioning dates, capacity.
- **ALKIS cadastral WFS** — free public, already partly integrated.
- **Handelsregister** — publicly accessible. Company verification.
- **Nominatim / OpenStreetMap** — free. Geocoding + maps.

What's out: paid credit bureaus and paid Grundbuch-access services. The
Grundbuch simply stays a *"request this document from the client"* action item —
which is correct due-diligence practice anyway. **Net effect on the plan: minimal.**

---

## 1. What LAI is made of today

```
   ┌──────────────────────────────────────────────────────────────┐
   │  FRONTEND  (React web app)                                   │
   │  — today deployed to Vercel/Cloudflare; must move on-prem     │
   └───────────────┬──────────────────────┬───────────────────────┘
                   │ talks to TWO          │
                   │ separate backends     │
        ┌──────────▼─────────┐   ┌─────────▼──────────────────────┐
        │ serve_rag          │   │ DDiQ engine (lai-backend)      │
        │ — the chat / Q&A   │   │ — the due-diligence report     │
        │   assistant        │   │   generator (the smoke test)   │
        │ — host process     │   │ — Docker container             │
        └──────────┬─────────┘   └─────────┬──────────────────────┘
                   │                       │
        ┌──────────▼─────────┐   ┌─────────▼──────────┐
        │ Legal corpus       │   │ Postgres database  │
        │ 350 GB SQLite file │   │ — only the user's  │
        │ 9.46M embedded     │   │   uploaded PDFs    │
        │ chunks, loaded     │   │                    │
        │ entirely into RAM  │   │                    │
        │ (155 GB)           │   │                    │
        └────────────────────┘   └────────────────────┘

        Shared by both: 3 AI models on the 2 GPUs —
          • Qwen3.6-27B  (writes the analysis)      GPU 0
          • Qwen3-Embedding-8B  (search)            GPU 1
          • Qwen3-Reranker-8B  (ranks results)

   + a THIRD, abandoned codebase (~3,200 lines) wired to nothing.
```

The pieces themselves are good: strong models, a real 672 GB German legal
corpus, a working extraction pipeline. **The problem is how they are wired
together.**

---

## 2. Why the current architecture causes the symptoms the boss saw

| Architectural fault | Symptom in the smoke test |
|---------------------|---------------------------|
| **The two backends are siloed.** The due-diligence engine has its *own* small database and **never touches the 672 GB legal corpus**. It can only read the 4 uploaded PDFs. | 25 of 40 sections said "no information in context." LAI *has* the legal knowledge — the DD engine just can't reach it. |
| **Three parallel codebases, ~1,500–2,000 duplicated lines.** The same PDF-reading, search, and AI-calling logic is copy-pasted 2–4 times. | Every bug has to be fixed in 3 places — so fixes are slow and inconsistent. The "findings failed" bug lives in code that was never hardened. |
| **The corpus is one 350 GB file loaded entirely into memory.** | Cold restarts take minutes; the system can't scale past one machine; the corpus can't grow without a full reload. |
| **One copy of each AI model serves everything.** | If the main model restarts, *all three products* go down at once — this has caused outages before (the runtime stack briefly went down at audit time; see `RE_VERIFICATION.md` §B1). |
| **No real security layer.** | Every user can see every other user's contracts — the GDPR blocker. |
| **Storage split across two engines by accident** (SQLite for the corpus, Postgres for DD) — not a design decision, just drift. | The two halves can't share data without a bridge that doesn't exist. |

The smoke test's failures are not random bugs — they are the **predictable
output of these structural faults**.

---

## 3. The target architecture (on-prem, unified)

```
   ┌──────────────────────────────────────────────────────────────┐
   │  FRONTEND  (React)  — served ON-PREM from the box            │
   │  with a real authentication layer in front                   │
   └───────────────────────────┬──────────────────────────────────┘
                               │  one backend, one contract
                ┌──────────────▼───────────────┐
                │  Unified LAI backend          │
                │  • chat / Q&A                 │
                │  • DDiQ report engine         │
                │  • shared code (lai.common)   │
                │  • a RETRIEVAL ROUTER in front │
                │    of every analysis step      │
                └──────────────┬────────────────┘
                               │
        ┌──────────────────────┼──────────────────────────┐
        │                      │                          │
   ┌────▼─────────┐   ┌────────▼──────────┐   ┌───────────▼────────┐
   │ ONE database  │   │ External public   │   │ Feedback store     │
   │ Postgres +    │   │ connectors        │   │ — lawyer           │
   │ pgvector:     │   │ • MaStR (free)    │   │   corrections      │
   │ • legal corpus│   │ • ALKIS (free)    │   │   captured & fed   │
   │ • uploaded    │   │ • Handelsregister │   │   back in          │
   │   docs        │   │   (free)          │   │   ("it learns")    │
   │ • all in ONE  │   │ • OSM (free)      │   │                    │
   │   place       │   └───────────────────┘   └────────────────────┘
   └───────────────┘
        AI models: same 2 GPUs, but the corpus no longer sits in
        155 GB of RAM — Postgres does the search efficiently.
```

**The four structural changes:**

1. **Unify storage.** Move the legal corpus out of the 350 GB SQLite file into
   the same Postgres database the DD engine already uses. Now the DD engine can
   ground every section in actual statute and case law — a plain database query
   instead of an impossible cross-system bridge. *(This is the "keystone" — it
   unblocks the most.)*
2. **Unify the code.** Delete the abandoned codebase; extract the duplicated
   logic into one shared library. Every fix lands once.
3. **Add a retrieval router.** Before each analysis step, one component decides
   which sources to pull from — the uploaded documents, the legal corpus, the
   public registries — and assembles a grounded answer *with its sources
   attached*. This is what turns "no information" into "the law requires X under
   §Y; here is what is missing."
4. **Add the missing layers:** real authentication + per-customer data
   isolation, and a feedback store so lawyer corrections make the next report
   better.

---

## 4. Before / After

| | Today | Target |
|--|-------|--------|
| Backends | 2 siloed + 1 dead | 1 unified |
| Storage | SQLite file (corpus) + Postgres (DD), separate | 1 Postgres, everything together |
| DD engine knowledge | Only the uploaded PDFs | Uploaded PDFs **+ 672 GB legal corpus + public registries** |
| Duplicated code | ~1,500–2,000 lines, fix 3× | One shared library, fix once |
| Security | None — data globally visible | Auth + per-customer isolation |
| Learning | None | Lawyer corrections fed back in |
| Hosting | Mixed (Vercel/Cloudflare + on-prem) | Fully on-prem — "data never leaves the building" |
| Scaling limit | Restart reloads 155 GB into RAM | Efficient DB search, no RAM reload |

---

## 5. The honest bottom line for the boss

- **It is not a rewrite.** The models, the corpus, the legal reasoning, the
  extraction pipeline — those are sound. We are **re-wiring**, not rebuilding.
- **The plan fits the constraints.** On-prem is mostly where we already are.
  No-budget is fine because the data sources that matter are free and public.
- **On-prem is a feature, not a limitation** — for German legal clients,
  "nothing leaves your building" is a real competitive edge.
- **The one honest caveat:** on-prem means one box, so we cannot scale by
  "adding cloud." We scale by the efficiency gains above (getting the corpus out
  of RAM). That is enough for the wind-energy vertical; it is a real ceiling if
  LAI later goes broad.

Full detail: `DDIQ_ROADMAP.md` (the phased plan), `DEEP_RESEARCH.md` (the
evidence), `AUDIT.md`, `VERIFICATION.md`.
