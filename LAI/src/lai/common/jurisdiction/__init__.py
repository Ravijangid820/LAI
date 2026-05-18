"""German Bundesland-aware jurisdiction validation.

Centralises three closely-coupled facts about the 16 German Bundesländer
that the rest of the codebase keeps re-implementing:

1. The keyword set that maps free-text location strings to one of the
   16 lowercase Bundesland keys (e.g. ``"cuxhaven" -> "niedersachsen"``).
2. The geographic bounding box of each Bundesland (used by the DDiQ
   geocoder to reject Nominatim hits that fell in the wrong state).
3. The set of Bundesland-specific legal rules whose mention in an LLM
   answer requires the matter to actually be in that Bundesland — most
   importantly, **Bayern's 10H setback rule (BayBO Art. 82 / 82a)**.
   Citing 10H for a Niedersachsen project was one of the four
   credibility-breaking errors the lawyer flagged at the v0 demo (see
   strategy doc §2.2 / §7.2).

The chat-side validator (:func:`check_jurisdiction`) runs over a
generated answer and a known/expected Bundesland and returns a list of
:class:`JurisdictionWarning` objects the renderer should display as
warning chips above the bubble.

The geocoder constants (:data:`BUNDESLAND_BBOX`, :data:`GERMANY_BBOX`,
:func:`point_in_bbox`) are exposed for ``ddiq_report.py`` to consume —
that file currently inlines the same tables; once it migrates to import
from here, the duplication is gone.

This module is pure-Python with no I/O — safe to import from anywhere
in ``lai.common`` consumers.
"""
from __future__ import annotations

from lai.common.jurisdiction.validator import (
    BUNDESLAND_BBOX,
    BUNDESLAND_KEYWORDS,
    GERMANY_BBOX,
    BUNDESLAND_SPECIFIC_RULES,
    JurisdictionWarning,
    check_jurisdiction,
    detect_bundesland,
    point_in_bbox,
)

__all__ = [
    "BUNDESLAND_BBOX",
    "BUNDESLAND_KEYWORDS",
    "BUNDESLAND_SPECIFIC_RULES",
    "GERMANY_BBOX",
    "JurisdictionWarning",
    "check_jurisdiction",
    "detect_bundesland",
    "point_in_bbox",
]
