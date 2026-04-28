# LAI MVP — Delivery Summary

**Subject:** LAI MVP — delivery summary

The MVP for the LAI contract-review platform is now deployed and working end-to-end. Below is a summary of what's been built and the capabilities it now offers.

## Core delivery — what works today

- **Conversational legal assistant** for German wind-energy contracts, with retrieval-augmented answers grounded in our internal corpus (BImSchG / BauGB / EEG / contract chunks).
- **Document upload and analysis** — users drop a PDF (signed contracts supported) and the system extracts text via OCR, segments the document into clauses, and produces a clause-by-clause review.
- **Deep contract analysis** powered by a 27B-parameter reasoning model with thinking mode. Output includes:
  - Detected contract type (Pachtvertrag, Wartungsvertrag, Nutzungsvertrag, PPA, etc.)
  - Per-clause issues with severity (1–5), affected paragraphs, and concrete redline suggestions
  - Cited legal basis (§307 BGB, §309 BGB, EEG, AGB-Recht)
  - Missing-required-clause detection against per-contract-type playbooks
  - Cross-clause consistency findings (e.g. one clause references another that doesn't exist)
  - Cadastral parcel extraction (Gemarkung / Flur / Flurstück) ready for plotting on a map
  - Financial-table reconciliation with deterministic German number parsing (catches arithmetic errors a model would miss)
  - Extraction-quality guard that flags low-OCR-confidence so reviewers don't act on incomplete data

## User experience

- **Persistent conversations** — uploaded contracts, chat history, and analysis results all survive page refreshes and server restarts. Stored in a local SQLite database with the original PDFs preserved on disk.
- **Live progress feedback** during the multi-minute analysis run (current clause, percent complete, estimated remaining time).
- **Conversations sidebar** with auto-titles (first user message or filename), rename, delete.
- **Message history rehydration** — every visible bubble in the chat corresponds to a database record, so users can come back to old contracts and see the full review they ran weeks earlier.

## Infrastructure

- Containerized stack (analyzer LLM, embedding service, pgvector database, Redis) orchestrated by a single Docker Compose file.
- Host processes (FastAPI backend, Vite UI) for fast iteration.
- One-command bring-up / tear-down scripts (`scripts/start.sh`, `stop.sh`, `status.sh`).
- Loopback-only by default; VPN-trusted mode supported for remote access via FortiClient.

## Quality engineering done along the way

- Switched OCR engine to Tesseract with German training data, raising extraction yield ~60% on signed/scanned PDFs.
- Bigger segmentation token budget so long contracts don't silently drop their last sections.
- Routing fix so contract-specific questions don't pull in irrelevant chunks from other contracts in the corpus.
- Comprehensive smoke tests across 44-clause contract analyses (Enercon Wartungsvertrag, WP Altmark Nutzungsvertrag, etc.) verifying the analyzer correctly identifies AGB-Verstöße, project-finance risks, and operational red flags.

## Validated example output

Running the analyzer on a real signed Wartungsvertrag produces ~44 clauses with findings such as:

- §307 BGB-style invalidity in Haftungsbeschränkung clauses
- One-sided cost-allocation provisions flagged as Kardinalpflicht violations
- SLA gaps (Mo-Fr 8-17 vs 24/7 wind-park reality)
- Project-finance concerns (refinancing-blocking clauses, EPK-Ablösung gaps)
- Each finding accompanied by concrete redline language

The system is the running MVP. Ready to demo whenever you're available.

