"""Category registry: maps gesetze-im-internet.de law slugs → corpus domains.

The statute feed (Phase 4.3) ingests *every* federal law, but tags each with
a legal-domain category so the corpus stays partitioned and filterable — using
the **same taxonomy** :mod:`lai.pipeline.classify` assigns to the rest of the
corpus (the ``parent_chunks.domain`` field). Wind-energy-relevant laws are
mapped explicitly; everything else falls back to ``allgemein`` (classify's own
fallback) so coverage is total — no law is dropped, it's just grouped under the
catch-all until someone assigns it a domain.

This module is the **single source of truth** for the mapping. Adding a law is
a one-line edit to ``_DOMAIN_BY_SLUG``; the slug is the gesetze-im-internet.de
URL slug (e.g. ``bimschg`` from ``/bimschg/xml.zip``). New domains must also be
added to :mod:`lai.pipeline.classify`'s ``DOMAINS`` to stay in sync.
"""

from __future__ import annotations

__all__ = ["DEFAULT_DOMAIN", "KNOWN_DOMAINS", "categorize", "mapped_slugs"]

# Mirrors lai.pipeline.classify.DOMAINS — keep in sync. The feed writes one of
# these into parent_chunks.domain so statute rows filter identically to the
# classified corpus.
KNOWN_DOMAINS: frozenset[str] = frozenset(
    {
        "immissionsschutzrecht",
        "energierecht",
        "baurecht",
        "umweltrecht",
        "vertragsrecht",
        "gesellschaftsrecht",
        "grundstuecksrecht",
        "arbeitsrecht",
        "steuerrecht",
        "verwaltungsrecht",
        "prozessrecht",
    }
)

# classify.py's fallback bucket for text it can't place. Reused here so every
# unmapped statute lands in the same catch-all as unclassified corpus chunks.
DEFAULT_DOMAIN = "allgemein"

# Explicit slug → domain mappings, seeded with the wind-energy-relevant federal
# laws (and the common civil/commercial/procedural codes a DD touches). Grouped
# by domain for readability. Extend freely — anything not here is `allgemein`.
_DOMAIN_BY_SLUG: dict[str, str] = {
    # Immissionsschutz
    "bimschg": "immissionsschutzrecht",
    # Energie
    "eeg_2014": "energierecht",  # EEG — GII keeps the 2014 consolidation slug
    "enwg_2005": "energierecht",
    "windseeg": "energierecht",
    "windbg": "energierecht",
    "kwkg_2016": "energierecht",
    # Bau / Raumordnung
    "bbaug": "baurecht",  # Baugesetzbuch (BauGB)
    "baunvo": "baurecht",
    "rog_2008": "baurecht",  # Raumordnungsgesetz
    # Umwelt / Natur / Wasser
    "bnatschg_2009": "umweltrecht",
    "uvpg": "umweltrecht",
    "whg_2009": "umweltrecht",
    "uschadg": "umweltrecht",
    # Vertrag / Zivil
    "bgb": "vertragsrecht",
    "hgb": "vertragsrecht",
    # Gesellschaft
    "gmbhg": "gesellschaftsrecht",
    "aktg": "gesellschaftsrecht",
    # Grundstück
    "gbo": "grundstuecksrecht",
    "erbbauv": "grundstuecksrecht",
    # Arbeit
    "kschg": "arbeitsrecht",
    "betrvg": "arbeitsrecht",
    "arbzg": "arbeitsrecht",
    # Steuer
    "estg": "steuerrecht",
    "kstg_1977": "steuerrecht",
    "ustg_1980": "steuerrecht",
    "gewstg": "steuerrecht",
    # Verwaltung / Prozess
    "vwvfg": "verwaltungsrecht",
    "vwgo": "prozessrecht",
    "zpo": "prozessrecht",
}


def categorize(slug: str) -> str:
    """Return the corpus domain for a gesetze-im-internet.de law ``slug``.

    Case-insensitive. Falls back to :data:`DEFAULT_DOMAIN` for any law not
    explicitly mapped, so every federal statute is covered (just grouped under
    ``allgemein`` until a domain is assigned in :data:`_DOMAIN_BY_SLUG`).
    """
    return _DOMAIN_BY_SLUG.get(slug.lower(), DEFAULT_DOMAIN)


def mapped_slugs() -> frozenset[str]:
    """The slugs with an explicit domain mapping (excludes the default bucket)."""
    return frozenset(_DOMAIN_BY_SLUG)
