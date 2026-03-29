#!/usr/bin/env python3
"""Quick test script for location extraction module.

Usage:
    python3 scripts/test_extraction.py <path-to-pdf-or-docx>

Extracts text from the file, sends it to the LLM for location extraction,
and prints the results. Does NOT write to the database.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/test_extraction.py <path-to-pdf-or-docx>")
        print("\nGood test files from DD Reports:")
        print('  "DD Reports/DD-Report_ZODEL -2020_02_18 update AS 20022020.docx"')
        print('  "DD Reports/Red Flag Report WP Altmark_FINAL VERSION 2021-09-06.docx"')
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    # -- Step 1: Extract text from file --
    print(f"\n{'='*60}")
    print(f"FILE: {filepath.name}")
    print(f"{'='*60}")

    suffix = filepath.suffix.lower()
    text = ""

    if suffix == ".pdf":
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(str(filepath))
            text = result.document.export_to_markdown()
            print(f"Extracted {len(text)} chars via Docling")
        except ImportError:
            # Fallback: try PyPDF2 or pdfplumber
            try:
                import pdfplumber
                with pdfplumber.open(filepath) as pdf:
                    text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
                print(f"Extracted {len(text)} chars via pdfplumber")
            except ImportError:
                print("ERROR: Install docling or pdfplumber to read PDFs")
                sys.exit(1)

    elif suffix in (".docx", ".doc"):
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(str(filepath))
            text = result.document.export_to_markdown()
            print(f"Extracted {len(text)} chars via Docling")
        except ImportError:
            try:
                import docx
                doc = docx.Document(str(filepath))
                text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
                print(f"Extracted {len(text)} chars via python-docx")
            except ImportError:
                print("ERROR: Install docling or python-docx to read DOCX files")
                sys.exit(1)

    elif suffix in (".txt", ".md"):
        text = filepath.read_text(encoding="utf-8", errors="replace")
        print(f"Read {len(text)} chars")

    else:
        print(f"Unsupported file type: {suffix}")
        sys.exit(1)

    if not text or len(text) < 50:
        print("ERROR: Could not extract meaningful text from file")
        sys.exit(1)

    # Show a preview
    print(f"\n--- Text Preview (first 500 chars) ---")
    print(text[:500])
    print("...")

    # -- Step 2: Run location extraction --
    print(f"\n{'='*60}")
    print("EXTRACTING LOCATIONS via LLM...")
    print(f"{'='*60}")

    from lai.extraction.location import extract_locations

    result = await extract_locations(
        text=text,
        segment_id=0,  # dummy ID for testing
    )

    if result.error:
        print(f"\nERROR: {result.error}")
        sys.exit(1)

    if not result.locations:
        print("\nNo locations found in this document.")
        sys.exit(0)

    # -- Step 3: Print results --
    print(f"\nFound {len(result.locations)} locations:\n")

    for i, loc in enumerate(result.locations, 1):
        print(f"  [{i}] {loc.location_name}")
        print(f"      Type:       {loc.location_type.value}")
        if loc.geocode_address:
            print(f"      Geocode:    {loc.geocode_address}")
        if loc.address:
            print(f"      Address:    {loc.address}")
        if loc.coordinates:
            print(f"      Coords:     {loc.coordinates.latitude}, {loc.coordinates.longitude}")
        if loc.flurstuck:
            print(f"      Flurstück:  {loc.flurstuck}")
        if loc.flur:
            print(f"      Flur:       {loc.flur}")
        if loc.gemarkung:
            print(f"      Gemarkung:  {loc.gemarkung}")
        if loc.gemeinde:
            print(f"      Gemeinde:   {loc.gemeinde}")
        if loc.landkreis:
            print(f"      Landkreis:  {loc.landkreis}")
        if loc.bundesland:
            print(f"      Bundesland: {loc.bundesland}")
        print(f"      Confidence: {loc.confidence}")
        if loc.raw_excerpt:
            print(f"      Excerpt:    \"{loc.raw_excerpt[:120]}...\"")
        print()

    # Also dump raw JSON for inspection
    print(f"\n{'='*60}")
    print("RAW JSON OUTPUT")
    print(f"{'='*60}")
    print(json.dumps(
        [loc.model_dump(mode="json", exclude_none=True) for loc in result.locations],
        indent=2, ensure_ascii=False,
    ))


if __name__ == "__main__":
    asyncio.run(main())
