# Track A — DDiQ reliability: file-by-file modification map

**Document type:** Implementation map for Phase 1b Track A items 2–6
**Date:** 2026-05-16
**Status:** Planning — no code changes yet. Item 1 already shipped (`501a315`).

This document answers "which folder under `LAI/` are we touching, and which
file does what?" for every Track A item we're about to take on. Each item
gets:

- **Targets** — exact files and line ranges that change
- **New files** — anything created from scratch
- **DB schema** — any `ddiq_*` table change
- **Out of scope** — what we explicitly *don't* touch
- **Test surface** — how we'll know it works

Two short references throughout:

- All Track A changes land in `LAI/micro-services/` (the DDiQ FastAPI
  microservice). The shared `lai.common` package is *consumed*, not
  modified, in Track A. Phase 1a (`lai.common.embedding`, `pdf`, `chunk`)
  is the teammate's lane and is not touched here.
- File paths in this document are relative to the repo root
  `/data/projects/lai/`. So `LAI/micro-services/ddiq_report.py` is the
  full path the editor opens.

---

## Map at a glance

| # | Item | Primary file(s) | New file(s) | DB schema | Status |
|---|------|-----------------|-------------|-----------|--------|
| 1 | LLM-call migration | `LAI/micro-services/ddiq_report.py` | (none) | (none) | ✅ Shipped `501a315` |
| 2 | Per-finding iteration | `LAI/micro-services/ddiq_report.py:1616-1728` | (none) | (none) | Pending |
| 3 | Geocoding plausibility gate | `LAI/micro-services/ddiq_report.py:644-680`, `:153-155` | `LAI/micro-services/bundesland_bbox.py` | `ddiq_geocode_cache` + `expires_at` column | Pending |
| 4 | Deterministic reconciler | `LAI/micro-services/ddiq_report.py` (multi-site) + new helper | `LAI/micro-services/_reconcile.py` | (none) | Pending |
| 5 | Validation / guardrail layer | `LAI/micro-services/ddiq_report.py` (caller integration) | `LAI/micro-services/_guardrail.py` | (none) | Pending |
| 6 | Adjacent reliability fixes | `LAI/micro-services/ddiq_report.py` (multi-site) | (none) | UNIQUE index + TTL columns on `ddiq_geocode_cache`, `ddiq_parcel_cache` | Pending |

Track A leaves the rest of the repository alone:

- `LAI/src/lai/common/*` — used as a library (`from lai.common.llm import …`); not modified
- `LAI/src/lai/analyzer/reconciler.py` — read *for reference only* when porting Item 4's pattern; not edited
- `LAI/processed/pipeline_local.db` — owned by Step 6; we don't touch
- `LAI/src/lai/{api,auth,documents,extraction,generation,infra,search}/*` — the dead-stack the teammate will delete in Phase 1a remainder
- `LAI/scripts/`, `LAI/Docker/`, `LAI/tests/` — only changed if a reliability item creates a new test

---

## Item 2 — Per-finding iteration in `generate_findings`

### What's wrong today

`LAI/micro-services/ddiq_report.py:1616-1728` builds a single batched
prompt asking the LLM for an array of findings at once. When one row in
that array is malformed, the whole `llm_json` parse fails, the function
returns `[]` or the "Findings extraction returned an unexpected shape —
manual review required" placeholder, and the entire chapter is lost.

### Targets

| File | Lines | What changes |
|------|------:|--------------|
| `LAI/micro-services/ddiq_report.py` | `1622-1648` | Keep the loop that fills `flagged`; keep the `issues_json` builder. |
| `LAI/micro-services/ddiq_report.py` | `1649-1728` | Replace the single batched LLM call with a loop calling `llm_json` once per item in `flagged`. Per failed row: append a structured `Finding(domain="General", severity="yellow", text=f"(extraction failed for issue #{i}: {label})", kind="row")` and continue. |

### New files

None.

### DB schema

None.

### Out of scope

- The `Finding` Pydantic model itself (`LAI/micro-services/ddiq_report.py`
  near the top) — signature unchanged.
- Evidence pointers — already attached upstream.
- The downstream JSON-B writer (`_persist_report_jsonb`) — unchanged.

### Test surface

- Manual probe via the DDiQ container exec, similar to today's smoke
  test: corrupt one row's input deliberately, verify the rest still come
  through with one structured "missing" row.
- Optional: a parametrised pytest under `LAI/tests/unit/micro_services/`
  (new folder) mocking `llm_json` with a side-effect list to exercise
  the partial-failure path.

---

## Item 3 — Geocoding plausibility gate

### What's wrong today

`geocode_address` (`LAI/micro-services/ddiq_report.py:644-660`) trusts
the first Nominatim result regardless of plausibility. A Cuxhaven
address can resolve to Bremen (the city of Bremen, ~70 km south-west)
and the resulting WEA marker lands in the wrong Bundesland — one of the
two failures the wind-lawyer screenshot-flagged. The
`ddiq_geocode_cache` row then poisons every subsequent run.

### Targets

| File | Lines | What changes |
|------|------:|--------------|
| `LAI/micro-services/ddiq_report.py` | `153-155` | Schema: add `expires_at TIMESTAMPTZ` to `ddiq_geocode_cache`. Forward-compat: `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. Default to `NOW() + INTERVAL '90 days'`. |
| `LAI/micro-services/ddiq_report.py` | `644-680` | `geocode_address` accepts an optional `expected_bundesland: Optional[str] = None`. After Nominatim returns, look up the Bundesland's bbox; if `(lat, lng)` falls outside, reject the result, log a warning, return `None` (caller already handles `None`). Cache lookup filters `WHERE expires_at IS NULL OR expires_at > NOW()`. Cache insert sets `expires_at = NOW() + INTERVAL '90 days'`. |
| `LAI/micro-services/ddiq_report.py` | `684-728` (`detect_bundesland`) | Caller propagates the detected Bundesland into `geocode_address(expected_bundesland=…)`. |

### New files

`LAI/micro-services/bundesland_bbox.py` — pure-Python module exporting
a constant dict of 16 entries:

```python
BUNDESLAND_BBOX: dict[str, tuple[float, float, float, float]] = {
    # (min_lat, max_lat, min_lng, max_lng)
    "Niedersachsen": (51.30, 53.90, 6.65, 11.60),
    "Bremen":        (53.01, 53.61, 8.48, 8.99),
    # … 14 more, sourced from the official Bundesland boundary boxes …
}
```

Plus one helper:

```python
def is_in_bundesland(lat: float, lng: float, bundesland: str) -> bool: ...
```

### DB schema

`ddiq_geocode_cache` gets one new column. SQL embedded in the schema
init block (`LAI/micro-services/ddiq_report.py:117-200`).

### Out of scope

- The Nominatim transport (HTTP via `requests` is fine for now; will
  migrate to `lai.common` in a later track).
- The `detect_bundesland` function itself (text-based; unchanged).
- ALKIS / `lai_postgres_main` queries — separate cache.

### Test surface

- A Niedersachsen→Bremen mis-resolution case in a fixture; assert
  `geocode_address` returns `None` and that no row is written to
  `ddiq_geocode_cache`.

---

## Item 4 — Deterministic reconciler

### What's wrong today

When the LLM extracts the same fact (turbine count, project size,
Bundesland) in multiple sections, the report can show four different
values. There's no single canonical write-down step.

### Reference

`LAI/src/lai/analyzer/reconciler.py` — already implements this pattern
for the analyzer's table outputs. We port the same approach into the
DDiQ engine. **The reference file is read-only for Track A; we do not
edit it.**

Look at:

- `reconcile_table` (`LAI/src/lai/analyzer/reconciler.py:184`) — the
  table-row reducer
- `reconcile_all` (`LAI/src/lai/analyzer/reconciler.py:318`) — the
  full-report driver

### Targets

| File | Lines | What changes |
|------|------:|--------------|
| `LAI/micro-services/ddiq_report.py` | (multi-site, post-extraction, pre-write) | At each spot in the report-build flow where a numeric / categorical field is set from an LLM-extracted value, route through the new reconciler module instead of writing directly. Affected fields (initial pass): `total_capacity_mw`, `turbine_count`, `bundesland`, `project_size_ha`. |

### New files

`LAI/micro-services/_reconcile.py` — DDiQ-local helper. Underscored
because it's not part of the public DDiQ API surface. Functions:

```python
class Candidate(BaseModel):
    value: Any
    provenance: Literal["cadastral", "llm", "regex", "fallback"]
    confidence: float
    source: str  # human-readable: "extract_metadata", "WEA hull", ...

def reconcile_numeric(name: str, candidates: list[Candidate]) -> tuple[Any, list[Candidate]]:
    """Return (winner, rejected). Precedence: cadastral > llm > regex > fallback,
    with a numeric-close-enough tolerance for floats."""

def reconcile_categorical(name: str, candidates: list[Candidate]) -> tuple[Any, list[Candidate]]:
    """Same precedence; ties broken by mode (most common value)."""
```

### DB schema

None.

### Out of scope

- Promoting `_reconcile.py` to `lai.common.reconcile` — keep it
  DDiQ-local for now. If `serve_rag` or the analyzer needs the same
  thing later, we promote in a follow-up commit.
- Storing the rejected-candidate audit trail in the report JSONB —
  log-only for v1.

### Test surface

- Unit tests in `LAI/tests/unit/micro_services/test_reconcile.py` (new
  folder). Pure-function logic, ~10 cases covering precedence, ties,
  float tolerance, and empty-candidates.

---

## Item 5 — Validation / guardrail layer

### What's wrong today

Section outputs in the report can include:

- Hedge phrases: "it might be that…", "möglicherweise…", "potentially…"
- Mixed languages within a paragraph (DE input → DE+EN output salad)
- Defensive AI fallbacks: "As an AI assistant, I cannot…", "I do not
  have the ability to…"

The lawyer specifically called these out as immediate trust-killers.

### Targets

| File | Lines | What changes |
|------|------:|--------------|
| `LAI/micro-services/ddiq_report.py` | (multi-site, just before `_persist_report_jsonb`) | Wrap every section's text fields in a single call to `_guardrail.scrub_section(section, target_language=…)` before persisting. |

### New files

`LAI/micro-services/_guardrail.py` — DDiQ-local. Functions:

```python
def strip_hedges(text: str, language: Literal["de", "en"]) -> str: ...
def detect_mixed_language(text: str, target: Literal["de", "en"]) -> bool: ...
def rewrite_paragraph(text: str, target: Literal["de", "en"]) -> str:
    """Single-shot LLM rewrite to enforce target language."""
def strip_defensive_ai(text: str) -> tuple[str, bool]:
    """Returns (cleaned, was_defensive). If was_defensive, caller emits
    {status: 'missing'} downstream."""
def scrub_section(section: ReportSection, target_language: str) -> ReportSection: ...
```

### DB schema

None.

### Out of scope

- Multi-language detection beyond DE/EN (will need a third language
  later but not for v1).
- Storing pre-/post-scrub diffs (log-only for v1).

### Test surface

- Unit tests in `LAI/tests/unit/micro_services/test_guardrail.py` — pure
  text functions; ~15 cases. Hedge-strip is mostly regex; mixed-language
  detection uses character n-gram heuristic; LLM-rewrite path mocks the
  client.

### Open design question (raised in the planning message)

`rewrite_paragraph` calls the LLM once per offending paragraph; that
costs +1 LLM call per scrub. Alternative is dropping the paragraph and
emitting `"(content removed: mixed-language)"`. **Default: LLM-rewrite
keeps content, accepts the latency**. Will surface in commit message and
ADR if we ever switch.

---

## Item 6 — Adjacent reliability fixes

A grab-bag. Each is a small, independent fix. Bundled as one commit (or
two, if we split DB-schema-only from logic).

### Targets

| File | Lines | What changes |
|------|------:|--------------|
| `LAI/micro-services/ddiq_report.py` | `752-…` (`_parse_alkis_feature`) | Invert control flow — make the success path the trunk, errors early-return. Currently nested `if/else` makes the happy path hard to read; bugs hide in the false branches. |
| `LAI/micro-services/ddiq_report.py` | `142, 150-152` | `request_fingerprint`: the existing index is **partial** (`WHERE … IS NOT NULL`) but **not UNIQUE**. Two concurrent identical requests can both write reports. Add `CREATE UNIQUE INDEX IF NOT EXISTS …`. |
| `LAI/micro-services/ddiq_report.py` | `153-180` (cache tables) | Add `expires_at TIMESTAMPTZ` to `ddiq_geocode_cache` (already covered by Item 3) and `ddiq_parcel_cache`. Default 90 days for both. Read queries filter on `expires_at`. |
| `LAI/micro-services/ddiq_report.py` | `~656` (cache write) | Switch `INSERT … ON CONFLICT DO NOTHING` to `INSERT … ON CONFLICT (key) DO UPDATE SET …` for cache refresh on hit (currently a stale cache row sticks forever). Same treatment for `ddiq_parcel_cache`. |
| `LAI/micro-services/ddiq_report.py` | (DDiQ sync entry, near line `~1900-2000`) | Wrap the synchronous report builder in an outer try/except that, on uncaught exception, marks `ddiq_reports.status='failed'` and `error=str(exc)`. Currently a crash mid-pipeline leaves the row stuck in `status='running'`. |

### New files

None.

### DB schema

- `ddiq_geocode_cache`: `+ expires_at TIMESTAMPTZ`
- `ddiq_parcel_cache`: `+ expires_at TIMESTAMPTZ`
- `ddiq_reports.request_fingerprint`: existing index promoted to `UNIQUE`

All via `ALTER … ADD COLUMN IF NOT EXISTS` + `DROP INDEX … CREATE UNIQUE
INDEX …` in the schema-init block; forward-compat across existing data.

### Out of scope

- Migrating the legacy `requests` HTTP transport — Track A keeps it as
  is. A future track migrates embed/rerank/Nominatim to `httpx`.
- Anything in the cadastral pipeline beyond `_parse_alkis_feature` —
  that lives in `LAI/micro-services/cadastral_pipeline.py` and is
  out of scope.

### Test surface

- A regression test that two concurrent requests with the same
  `request_fingerprint` produce one report row, not two.
- The cache TTL change is verifiable with a short fixture: insert a
  row with `expires_at` in the past, query, get a miss.

---

## Cross-item file summary

Files touched across Track A (so reviewers know what to look for):

| File | Items |
|------|-------|
| `LAI/micro-services/ddiq_report.py` | 2, 3, 4 (call sites only), 5 (call sites only), 6 |
| `LAI/micro-services/bundesland_bbox.py` (new) | 3 |
| `LAI/micro-services/_reconcile.py` (new) | 4 |
| `LAI/micro-services/_guardrail.py` (new) | 5 |
| `LAI/tests/unit/micro_services/test_reconcile.py` (new) | 4 |
| `LAI/tests/unit/micro_services/test_guardrail.py` (new) | 5 |

Files **read only** (for reference / never edited in Track A):

- `LAI/src/lai/common/llm/*` — consumed; teammate's adjacent work in
  `lai.common.embedding` doesn't conflict.
- `LAI/src/lai/analyzer/reconciler.py` — referenced for Item 4's port.

Files **explicitly not touched** in Track A:

- The whole `LAI/src/lai/` tree (Phase 1a remainder is the teammate's).
- `LAI/processed/pipeline_local.db` and `LAI/scripts/ops/resume_step6.sh`
  (Step 6 is running; only touch if rj asks).
- `LAI/docker-compose.yml`, `LAI/micro-services/docker-compose.yml`,
  `LAI/micro-services/Dockerfile` — already settled in Stage 2a of the
  DDiQ migration. Reused by every Track A redeploy without further
  edits.

---

## Per-item deploy checklist

Every item ships the same way:

1. Code change committed on `v2-restructure`.
2. `cd /data/projects/lai/LAI/micro-services && docker compose up -d --build`
   — image cache hits the lai-install layer; only the `COPY
   micro-services/ /app/` layer rebuilds, so the swap is ~10 s.
3. `curl http://127.0.0.1:18001/health` returns `{"status":"ok",…}`.
4. For items 2, 3, 5: one live probe (a real `/ddiq/report/generate`
   with a small fixture) before moving to the next item.
5. Items 4 and 6 ride on the next deploy (pure logic / schema, low
   regression risk).

---

## Pointers

- `harsh/LAI_V1_STRATEGY.md` — the strategy doc (Track A is in
  "Phase 1b Track A — DDiQ reliability (continuation)").
- `harsh/PROGRESS.md` — line-item tracker; will be updated as each
  Track A item ships.
- `LAI/CONTRIBUTING.md` — workflow + quality gate.
- The first DDiQ migration commits (for the same code-style baseline):
  `0946b65`, `aba3279`, `501a315`, `cdfc9c1`.
