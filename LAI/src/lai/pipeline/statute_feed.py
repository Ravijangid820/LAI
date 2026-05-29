"""Phase 4.3 — German federal statute ingestion feed.

Currently **read-only**: the only mode is the dry-run (the default), which
fetches gesetze-im-internet.de's table of contents, categorises every law by
legal domain (:mod:`lai.common.connectors.statute_categories`), and reports
what *would* be ingested — writing nothing to the corpus. With
``--fetch-sections`` it also downloads + parses a small sample to prove the
full TOC → fetch → unzip → parse chain against live data.

The write path (chunk via :mod:`lai.pipeline.chunk` → embed via
:mod:`lai.pipeline.embed` → transactional upsert into the ``corpus_*`` tables)
lands in a later phase.

    python -m lai.pipeline.statute_feed                       # dry-run summary
    python -m lai.pipeline.statute_feed --fetch-sections      # + validate parse
"""

from __future__ import annotations

import argparse
from collections import Counter

from lai.common.connectors._gii_parser import parse_law_xml
from lai.common.connectors.gesetze import GesetzeImInternetClient
from lai.common.connectors.statute_categories import (
    DEFAULT_DOMAIN,
    categorize,
    mapped_slugs,
)


def _run_dry(*, fetch_sections: bool, limit: int) -> int:
    with GesetzeImInternetClient() as client:
        laws = client.list_laws()

        by_domain: Counter[str] = Counter(categorize(law.slug) for law in laws)
        total = len(laws)
        catch_all = by_domain.get(DEFAULT_DOMAIN, 0)

        print(f"gesetze-im-internet.de TOC: {total} laws")
        print(f"  explicitly categorised : {total - catch_all}")
        print(f"  {DEFAULT_DOMAIN} (catch-all)    : {catch_all}")
        print("\nby domain:")
        for domain, n in sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {domain:24s} {n:>6}")

        # Validate the seeded registry slugs against the live TOC so typos /
        # renamed laws surface instead of silently falling to the catch-all.
        toc_slugs = {law.slug for law in laws}
        missing = sorted(mapped_slugs() - toc_slugs)
        if missing:
            print(f"\n[warn] {len(missing)} registry slug(s) NOT in the TOC: {', '.join(missing)}")
        else:
            print("\n[ok] every registry slug exists in the TOC")

        if fetch_sections:
            sample = [law for law in laws if categorize(law.slug) != DEFAULT_DOMAIN][:limit]
            print(f"\nfetch-sections sample ({len(sample)} laws):")
            for law in sample:
                try:
                    parsed = parse_law_xml(client.fetch_law_xml(law))
                except Exception as exc:  # dry-run continues past a bad/transient law
                    print(f"  {law.slug:16s} FAILED: {exc}")
                    continue
                paragraphs = sum(1 for s in parsed.sections if s.enbez and s.enbez.startswith("§"))
                print(
                    f"  {law.slug:16s} {categorize(law.slug):22s} "
                    f"jurabk={parsed.jurabk} sections={len(parsed.sections)} §={paragraphs}"
                )

    print("\nDRY RUN — nothing written to the corpus.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="statute-feed",
        description="German federal statute ingestion feed (Phase 4.3, read-only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(default) report what would be ingested; write nothing.",
    )
    parser.add_argument(
        "--fetch-sections",
        action="store_true",
        help="also download + parse a sample of mapped laws to validate the parse.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="number of laws to fetch with --fetch-sections (default 5).",
    )
    args = parser.parse_args(argv)
    # Only the dry-run exists today; --dry-run is accepted for forward-compat.
    return _run_dry(fetch_sections=args.fetch_sections, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
