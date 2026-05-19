"""Configuration for :mod:`lai.common.connectors`.

Two frozen :class:`~pydantic_settings.BaseSettings` subclasses — one
per upstream. The defaults are pinned to the real production
endpoints (Nominatim's hosted instance + the 12 German state ALKIS
INSPIRE WFS endpoints).

Environment-variable prefix is split:

* ``LAI_NOMINATIM_*`` for :class:`NominatimConfig`
* ``LAI_ALKIS_*`` for :class:`AlkisConfig`

This keeps the two upstreams' knobs independent — operators can tune
retry policy on one without touching the other.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "ALKIS_WFS_ENDPOINTS",
    "AlkisConfig",
    "NominatimConfig",
]


# ── ALKIS INSPIRE WFS endpoints (12 of 16 Bundeslaender; the 4 city /
# small states have no separate WFS, they fall back to their surrounding
# state's data). Each entry: ``url`` is the GetFeature endpoint;
# ``typename`` is the INSPIRE CP feature type to request; ``label`` is
# the human-readable operator-of-record (for log lines).
# Verified against the federal INSPIRE registry as of 2026-04. ────────
ALKIS_WFS_ENDPOINTS: Final[dict[str, dict[str, str]]] = {
    "niedersachsen": {
        "url": "https://www.opengeodata.lgln.niedersachsen.de/doorman/noauth/wfs_ni_inspire-flurstuecke_alkis",
        "typename": "cp:CadastralParcel",
        "label": "Niedersachsen LGLN",
    },
    "nordrhein-westfalen": {
        "url": "https://www.wfs.nrw.de/geobasis/wfs_nw_inspire-flurstuecke_alkis",
        "typename": "cp:CadastralParcel",
        "label": "NRW Geobasis",
    },
    "schleswig-holstein": {
        "url": "https://service.gdi-sh.de/WFS_SH_INSPIRE_CP",
        "typename": "cp:CadastralParcel",
        "label": "SH GDI",
    },
    "brandenburg": {
        "url": "https://inspire.brandenburg.de/services/cp_wfs",
        "typename": "cp:CadastralParcel",
        "label": "Brandenburg LGB",
    },
    "mecklenburg-vorpommern": {
        "url": "https://www.geodaten-mv.de/dienste/inspire_cp_alkis_download",
        "typename": "cp:CadastralParcel",
        "label": "MV LAIV",
    },
    "sachsen-anhalt": {
        "url": "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_INSPIRE_CP_WFS/guest",
        "typename": "cp:CadastralParcel",
        "label": "SA LVG",
    },
    "hessen": {
        "url": "https://www.gds.hessen.de/wfs2/aaa-bkg/inspire_cp_alkis",
        "typename": "cp:CadastralParcel",
        "label": "Hessen HVBG",
    },
    "thueringen": {
        "url": "https://www.geoproxy.geoportal-th.de/geoproxy/services/inspire_cp_alkis_wfs",
        "typename": "cp:CadastralParcel",
        "label": "Thueringen TLVermGeo",
    },
    "sachsen": {
        "url": "https://geodienste.sachsen.de/wfs_geobasis_inspire_cp/guest",
        "typename": "cp:CadastralParcel",
        "label": "Sachsen GeoSN",
    },
    "rheinland-pfalz": {
        "url": "https://www.geoportal.rlp.de/spatial-objects/314/services/inspire_cp_alkis_wfs",
        "typename": "cp:CadastralParcel",
        "label": "RLP LVermGeo",
    },
    "bayern": {
        "url": "https://geoservices.bayern.de/wfs/ogc_inspire_cp.cgi",
        "typename": "cp:CadastralParcel",
        "label": "Bayern LDBV",
    },
    "baden-wuerttemberg": {
        "url": "https://owsproxy.lgl-bw.de/owsproxy/wfs/WFS_ALKIS_INSPIRE_CP",
        "typename": "cp:CadastralParcel",
        "label": "BW LGL",
    },
}


# ─────────────────────────────────────────────────────────────────────
# Nominatim
# ─────────────────────────────────────────────────────────────────────


class NominatimConfig(BaseSettings):
    """Settings for :class:`~lai.common.connectors.nominatim.NominatimClient`.

    Defaults target the hosted OSM Nominatim instance. Self-hosted
    instances (which avoid the 1-req/sec usage policy) override
    ``base_url`` via ``LAI_NOMINATIM_BASE_URL``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_NOMINATIM_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    base_url: str = Field(
        default="https://nominatim.openstreetmap.org",
        description=(
            "Base URL of the Nominatim instance. Hosted OSM enforces a "
            "1-request-per-second usage policy (we throttle to that "
            "regardless via :attr:`request_interval_seconds`)."
        ),
    )
    user_agent: str = Field(
        default="LAI-DDiQ/1.0 (legal-ai-report-generator)",
        min_length=4,
        description=(
            "User-Agent header. OSM's usage policy requires identifying "
            "the application and a contact channel; bare 'Python-Requests' "
            "or empty strings are blocked at the server level."
        ),
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Per-request HTTP timeout in seconds.",
    )
    request_interval_seconds: float = Field(
        default=1.1,
        ge=0.0,
        description=(
            "Throttle delay AFTER a successful Nominatim call. Hosted OSM "
            "policy: max 1 request/second; we sleep 1.1 s to give a 10 % "
            "safety margin. Set to 0 against a self-hosted instance with "
            "no rate limit."
        ),
    )

    # ── Retry policy ────────────────────────────────────────────────
    max_retries: int = Field(
        default=2,
        ge=0,
        le=8,
        description=(
            "Total retry attempts on transport / 5xx failure. Lower than "
            "the LLM client's default 3 because Nominatim transients are "
            "rare and we don't want to amplify rate-limit-induced 429s."
        ),
    )
    retry_initial_wait_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial backoff before the first retry.",
    )
    retry_max_wait_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Cap on exponential backoff between retries.",
    )

    @field_validator("base_url")
    @classmethod
    def _check_base_url_scheme(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")


# ─────────────────────────────────────────────────────────────────────
# ALKIS
# ─────────────────────────────────────────────────────────────────────


class AlkisConfig(BaseSettings):
    """Settings for :class:`~lai.common.connectors.alkis.AlkisClient`.

    Per-Bundesland endpoint URLs live in :data:`ALKIS_WFS_ENDPOINTS`,
    not here — the 12 entries are fixed addresses of real government
    services, not operator-tunable.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_ALKIS_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    user_agent: str = Field(
        default="LAI-DDiQ/1.0 (legal-ai-report-generator)",
        min_length=4,
        description=(
            "User-Agent header. The state WFS endpoints don't enforce a "
            "policy as strict as Nominatim's, but identifying the client "
            "is good citizenship and helps operators trace traffic."
        ),
    )
    timeout_seconds: float = Field(
        default=20.0,
        gt=0.0,
        description=(
            "Per-request HTTP timeout in seconds. Slower than Nominatim "
            "because some state WFS (notably NRW's INSPIRE-CP and BW LGL) "
            "take 5-15 s on cold caches."
        ),
    )
    feature_count: int = Field(
        default=10,
        gt=0,
        le=1000,
        description=(
            "Maximum features per GetFeature request. ALKIS parcels "
            "near a typical wind-energy site number 1-5; 10 is generous "
            "headroom while keeping responses small enough that GML "
            "parsing stays fast."
        ),
    )

    # ── Retry policy ────────────────────────────────────────────────
    max_retries: int = Field(
        default=3,
        ge=0,
        le=8,
        description=(
            "Total retry attempts on transport / 5xx failure. Some state "
            "WFS (NRW, Bayern) occasionally return HTTP 530 (Cloudflare "
            "origin-unreachable) — retried."
        ),
    )
    retry_initial_wait_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial backoff before the first retry.",
    )
    retry_max_wait_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description="Cap on exponential backoff between retries.",
    )
