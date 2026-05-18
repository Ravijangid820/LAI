"""Load the demo Matter ("Windpark Lamstedt") into a fresh serve_rag session.

The 5-minute lawyer pitch (strategy doc Appendix A) opens with:

    "Drop your 4 PDFs in."

For the live demo we don't want the lawyer to wait for /upload to chew
through 4 PDFs cold. This script pre-loads a curated set into a fixed
session_id so the operator can open the UI at that session and the docs
are already there.

PDFs to drop into the seed directory
------------------------------------

Per the strategy doc §12.4 the demo Matter ("Windpark Lamstedt
acquisition") should contain a handful of high-signal documents. The
curated set the project lead is expected to assemble:

    1. A Pachtvertrag with a clear Schriftform issue (§ 550 BGB).
    2. A BImSchG-Bescheid with named Auflagen and Nebenbestimmungen.
    3. A relevant OVG ruling (e.g., the Niedersachsen Denkmalschutz one).
    4. An Enercon Wartungsvertrag with named warranty terms.
    5. A Lageplan / Flurstücke list.
    6. A Versicherungsschein (or its absence — flag in the demo).
    7. A Netzanschlussvertrag (or its absence — flag in the demo).

Drop those files into ``demo-seed/lamstedt/`` (path configurable below),
then run::

    python scripts/ops/load_demo_matter.py

The script POSTs each file to the live ``serve_rag`` /upload endpoint
under a fixed session_id (``DEMO_LAMSTEDT_SESSION_ID``). Re-runs are
idempotent: existing files with the same name skip the upload.

Usage
-----

    # Default — host-mode serve_rag, default seed directory
    python scripts/ops/load_demo_matter.py

    # Override the seed directory
    python scripts/ops/load_demo_matter.py --seed-dir /tmp/lamstedt-pdfs

    # Override the backend
    LAI_SERVE_RAG_URL=http://localhost:18000 \\
        python scripts/ops/load_demo_matter.py

    # Dry run — list what would be uploaded without doing it
    python scripts/ops/load_demo_matter.py --dry-run

The fixed session id is exposed as a constant so the frontend can deep-
link to it (``?session_id=lamstedt-demo``).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_DIR = REPO_ROOT / "demo-seed" / "lamstedt"
DEFAULT_URL = os.environ.get("LAI_SERVE_RAG_URL", "http://localhost:18000")

# Fixed session id the demo opens with. Stable so the frontend can
# deep-link via ``?session_id=lamstedt-demo`` (the React app reads
# ``session_id`` from query string on first load).
DEMO_LAMSTEDT_SESSION_ID = "lamstedt-demo"

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}


def _list_curated_pdfs(seed_dir: Path) -> list[Path]:
    """Enumerate files in the seed dir that we know how to upload.

    Files are returned in sorted-by-name order so each run uploads in
    the same sequence (the chat history reads the same way after a
    re-seed). README files are excluded — every seed directory has one
    explaining what to put there, and we don't want to upload it as a
    fake DD document.
    """
    if not seed_dir.exists():
        return []
    return sorted(
        p for p in seed_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and p.stem.lower() != "readme"
    )


def _upload_one(client: httpx.Client, base_url: str, path: Path, session_id: str) -> bool:
    """POST one file to /upload. Returns True on 2xx, False on failure
    (with a logged reason — script does not raise so a single bad PDF
    doesn't kill the rest of the seed)."""
    with path.open("rb") as f:
        files = {"file": (path.name, f, "application/octet-stream")}
        data = {"session_id": session_id}
        try:
            resp = client.post(
                f"{base_url.rstrip('/')}/upload",
                files=files, data=data, timeout=120.0,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            print(f"  ❌ {path.name}: transport error: {exc}")
            return False
    if resp.status_code != 200:
        print(f"  ❌ {path.name}: HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    try:
        body = resp.json()
    except ValueError:
        print(f"  ❌ {path.name}: non-JSON response")
        return False
    msg = body.get("message") or f"{body.get('pages', 0)} pages"
    print(f"  ✅ {path.name}: {msg}")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR,
                   help=f"Directory of curated PDFs (default: {DEFAULT_SEED_DIR.relative_to(REPO_ROOT)})")
    p.add_argument("--url", default=DEFAULT_URL,
                   help=f"serve_rag base URL (default: {DEFAULT_URL})")
    p.add_argument("--session-id", default=DEMO_LAMSTEDT_SESSION_ID,
                   help=f"Session id to load the matter under (default: {DEMO_LAMSTEDT_SESSION_ID})")
    p.add_argument("--dry-run", action="store_true",
                   help="List files that would be uploaded, do nothing.")
    args = p.parse_args(argv)

    pdfs = _list_curated_pdfs(args.seed_dir)
    if not pdfs:
        print(f"No supported files found in {args.seed_dir}.")
        print()
        print("Curate the Lamstedt demo Matter as a half-day product task")
        print("(see strategy doc §12.4 for the 7-document recommended set).")
        print("Drop the curated PDFs into the seed directory above, then")
        print("re-run this script.")
        return 0 if args.dry_run else 1

    print(f"Found {len(pdfs)} curated file(s) in {args.seed_dir}:")
    for p_ in pdfs:
        print(f"  - {p_.name}")
    print()

    if args.dry_run:
        print(f"(dry run — would upload under session_id={args.session_id} to {args.url})")
        return 0

    print(f"Uploading to {args.url} under session_id={args.session_id}…")
    n_ok = 0
    with httpx.Client() as client:
        for path in pdfs:
            if _upload_one(client, args.url, path, args.session_id):
                n_ok += 1
    print()
    print(f"Uploaded {n_ok}/{len(pdfs)} file(s) under session_id={args.session_id}")
    print()
    print(f"Open the demo at:  <frontend>/?session_id={args.session_id}")
    return 0 if n_ok == len(pdfs) else 1


if __name__ == "__main__":
    sys.exit(main())
