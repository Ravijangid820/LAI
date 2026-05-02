# LAI — TODO

Single source of truth for things we've committed to doing but haven't shipped.
Not a wishlist. Items move from here to closed pull requests; if we don't intend
to actually do an item, it doesn't live here.

> **Living doc.** Update when an item ships (cross out + commit hash) or when a
> deferred item gets new info. Keep entries to one title line + one "Why" line
> so the file stays scannable.

---

## P0 — blockers before sharing the URL

### Real auth + user scoping
- **Status:** scoped, deferred (~4-6h)
- **Why:** the frontend `AuthContext` is a demo that accepts any credentials and self-signs a JWT; both backends never validate it; `sessions` / `ddiq_documents` / `ddiq_reports` are globally visible. The `sessions.user_id` column already exists but is unused. Anyone hitting `/sessions` or `/ddiq/reports` sees every other user's data.
- **What it takes:**
  1. New `users` table on lai-backend Postgres (`email PK, password_hash, full_name, created_at`).
  2. `POST /auth/signup`, `POST /auth/login` with bcrypt + HMAC-signed JWT; shared `AUTH_SECRET` so `serve_rag` can verify the same token.
  3. Frontend `AuthContext.login/signup` replaced with real fetch calls; `Authorization: Bearer <jwt>` threaded through every fetch in `ddiqApi.ts` and `ragApi.ts`.
  4. Add `user_id` column to `ddiq_documents` and `ddiq_reports`; populate `sessions.user_id` on insert; filter all listings by `WHERE user_id = current_user.id`.
- **Discussed:** 2026-04-30 chat. Three logical chunks (auth foundation → DDiQ scoping → chat scoping).

---

## Performance

### Cap `max_tokens=4096 → 1024` for structured-JSON extraction
- **Status:** 1-line change, deferred per "we'll look at runtime later"
- **Why:** Qwen3.6-27B in thinking-mode emits long invisible reasoning traces that hit the 4096 ceiling. Each LLM call takes up to 2.6 min instead of ~30s. The DDiQ JSON outputs are typically <500 tokens — 4096 just buys nothing. Section pass goes from ~44 min to ~20 min.
- **Where:** [`llm_call` line 504](micro-services/ddiq_report.py#L504), [`llm_json` lines 517 + 521](micro-services/ddiq_report.py#L517) in `LAI/micro-services/ddiq_report.py`.
- **Risk:** None for structured extractions; long-form chat answers in `serve_rag` are a separate path and unaffected.

### Per-finding (instead of batch) generation in `generate_findings`
- **Status:** ~30 line change
- **Why:** the LLM occasionally returns empty content on the batch findings prompt and the retry-with-stricter-system-prompt also returns empty, falling through to a "Manual review required (findings extraction failed)" placeholder. Iterating per flagged row makes each call smaller, retries cheap, and partial success becomes useful (6/8 succeed → 6 findings instead of 0).
- **Where:** `generate_findings()` in `LAI/micro-services/ddiq_report.py`.

---

## Polish

### WEA technical-attrs extraction reliability
- **Why:** the lawyer-grade `WEAStatus.hub_height_m / rotor_diameter_m / rated_power_kw` came back null in both smoke tests despite the Enercon E70 Datenblatt being indexed. Manufacturer + model land sometimes ("Enercon", "E70") but the numerical specs miss. Likely PyMuPDF flattens the spec table oddly. Either better PDF→table extraction (try Docling) or a dedicated specs-only prompt that's more aggressive about numbers.

### ALKIS Niedersachsen WFS retry
- **Why:** the LGLN endpoint regularly returns HTTP 530 (Cloudflare-unreachable). External, but our pipeline currently logs and falls through to estimated polygons, never retries. Add bounded retry-with-backoff (3 attempts × 5s) before falling through.
- **Where:** `alkis_query_parcels()` in `LAI/micro-services/ddiq_report.py`.

### Disable thinking-mode for structured-extraction prompts
- **Status:** larger lever, requires per-call config
- **Why:** ~2-3× faster generation per call when not thinking. But the V2 contract analyzer explicitly relies on thinking for quality, so this can't be a blanket switch. Need either a separate model endpoint without thinking, or per-call `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` if the served Qwen3.6-27B build accepts it.

---

## Future capacity (P2 from the lawyer-grade audit)

External integrations that were called out as "needed for true lawyer-grade DD" but require third-party data sources:

- **EEG award database lookup** — auction round, strike price, Marktwertkorrektur via Bundesnetzagentur API. Confirms the EEG award status the section prompt asks about today.
- **Counterparty creditworthiness flag** — lessor / O&M operator / EPC contractor / grid operator. Hits a credit bureau (Bürgel, Creditreform, etc.).
- **Tax structure validation** — Gewerbesteuerzerlegung §29 GewStG (90% Standortgemeinde / 10% Sitzgemeinde) + §15a UStG correction risk. Needs accountant review interface.
- **Insurance certificate verification** — cover amounts, deductibles, beneficiary status, insolvency-remoteness. Direct insurer confirmation.
- **Repowering eligibility** — Flächennutzungsplan / Bebauungsplan post-update + EEG "bestehende Anlage" status for subsidy extension.
- **Neighbor-litigation / Widerspruch monitoring** — public dispute databases (OVG / VG case lookups, Bekanntmachungsregister).
- **Server-side PDF rendering** — Puppeteer or wkhtmltopdf in a sidecar container so the user gets a real PDF without going through the browser print dialog. ~200 MB image size; nice-to-have.

---

## Recently shipped (rolling, last 5)

- 2026-04-30 [`885612c`](https://github.com/Ravijangid820/LAI/commit/885612c) — docs round 2: INFRASTRUCTURE service map, pipeline status, dev pointer.
- 2026-04-30 [`99cc7f0`](https://github.com/Ravijangid820/LAI/commit/99cc7f0) — docs round 1: post-MVP DDiQ overhaul, async flow, Past Reports, chat memory.
- 2026-04-30 [`62f5964`](https://github.com/Ravijangid820/LAI-UI/commit/62f5964) — chat: clicking a sidebar conversation now actually loads its messages.
- 2026-04-30 [`2fdce04`](https://github.com/Ravijangid820/LAI/commit/2fdce04) — DELETE /report/{id} with cascade cleanup.
- 2026-04-30 [`ca3a579`](https://github.com/Ravijangid820/LAI/commit/ca3a579) — GET /reports endpoint for the Past Reports browser.
