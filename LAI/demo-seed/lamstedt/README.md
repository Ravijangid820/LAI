# Demo Matter — Windpark Lamstedt

Drop the curated DD documents into **this directory**, then run:

```bash
python scripts/ops/load_demo_matter.py
```

The script POSTs each file to the running `serve_rag` `/upload` endpoint
under the fixed session id **`lamstedt-demo`**, so the frontend can
deep-link to the demo Matter via `<frontend>/?session_id=lamstedt-demo`.

## What documents to put here

Per strategy doc §12.4 — half-day product/legal curation work, **not
engineering**. Recommended 6-8 document set:

1. **A Pachtvertrag** with a clear Schriftform issue (§ 550 BGB).
2. **A BImSchG-Bescheid** with named Auflagen and Nebenbestimmungen.
3. **A relevant OVG ruling** (e.g., the Niedersachsen Denkmalschutz
   one).
4. **An Enercon Wartungsvertrag** with named warranty terms.
5. **A Lageplan / Flurstücke list**.
6. **A Versicherungsschein** — or its absence (flag in the demo).
7. **A Netzanschlussvertrag** — or its absence (flag in the demo).

The demo is only as good as these PDFs. The 5-minute pitch (Appendix A
of the strategy doc) is built around the lawyer asking a specific
question about the Rückbau / Schriftform / BImSchG-Auflagen and getting
a citation chip back. Without the documents in place, the citation
chips have nothing to anchor to.

## Supported file types

`.pdf`, `.docx`, `.doc`, `.txt`, `.md`. PDFs render in the side panel
via the browser's native viewer (no react-pdf dependency); other types
fall back to a download link.

## Re-running

The script POSTs to `/upload` which currently allows multiple uploads
per session. Running twice loads the documents twice — the chat will
work but the document list will show duplicates. Clean up first via
`DELETE /sessions/lamstedt-demo` (or just don't re-run).

## Dry run

```bash
python scripts/ops/load_demo_matter.py --dry-run
```

Lists what would be uploaded without doing it. Useful to verify the
seed directory has the right files before committing to the upload.
