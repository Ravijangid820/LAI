"""Typed exceptions for :mod:`lai.common.connectors`.

Mirrors the hierarchy shape used by :mod:`lai.common.llm`,
:mod:`lai.common.embedding`, and :mod:`lai.common.reranker`: one root
class per upstream (``NominatimError``, ``AlkisError``) plus
``ConnectorError`` as the common parent so callers that want to catch
"anything from a public registry" can do so with a single except.

Construction always preserves the ``__cause__`` chain when used with
``raise … from`` — keep the underlying httpx / parser error visible
in tracebacks so the operator can grep for the real cause.
"""

from __future__ import annotations

from lai.common.exceptions import LaiCommonError

__all__ = [
    "AlkisCallError",
    "AlkisError",
    "AlkisInvalidResponseError",
    "AlkisRetryExhaustedError",
    "ConnectorError",
    "GesetzeCallError",
    "GesetzeError",
    "GesetzeInvalidResponseError",
    "GesetzeRetryExhaustedError",
    "NominatimCallError",
    "NominatimError",
    "NominatimInvalidResponseError",
    "NominatimRetryExhaustedError",
]


class ConnectorError(LaiCommonError):
    """Base for any failure interacting with an external connector."""


# ── Nominatim ────────────────────────────────────────────────────────


class NominatimError(ConnectorError):
    """Base for any Nominatim (OpenStreetMap geocoder) failure."""


class NominatimCallError(NominatimError):
    """Transport-level failure when calling Nominatim.

    HTTP non-2xx, connection refusal, timeout. ``status_code`` is the
    HTTP code when applicable; ``url`` is the endpoint we hit.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.url: str | None = url


class NominatimInvalidResponseError(NominatimError):
    """Nominatim returned 2xx but the body wasn't the expected shape.

    Covers: non-JSON body, missing ``lat`` / ``lon`` fields, non-numeric
    coordinates. ``raw_response`` carries up to ~500 chars of the body
    so the operator can paste it into a debugger.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response: str | None = raw_response[:500] if raw_response else None


class NominatimRetryExhaustedError(NominatimError):
    """Every retry attempt against Nominatim failed.

    ``attempts`` is the number of attempts made (≥1).
    """

    def __init__(self, message: str, *, attempts: int) -> None:
        super().__init__(message)
        self.attempts: int = attempts


# ── ALKIS (German cadastral INSPIRE WFS) ─────────────────────────────


class AlkisError(ConnectorError):
    """Base for any ALKIS INSPIRE WFS failure."""


class AlkisCallError(AlkisError):
    """Transport-level failure when calling an ALKIS WFS endpoint.

    Same shape as :class:`NominatimCallError`. ``bundesland`` identifies
    which state's endpoint was being hit so operators can correlate
    failures against the per-state services (Bayern's LDBV, NRW's
    Geobasis, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        bundesland: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.url: str | None = url
        self.bundesland: str | None = bundesland


class AlkisInvalidResponseError(AlkisError):
    """ALKIS WFS returned 2xx but the body couldn't be parsed.

    Neither the JSON-feature parser nor the GML XML parser produced a
    usable result. Distinct from :class:`AlkisCallError` because the
    HTTP layer succeeded — the failure is content-shape.
    """

    def __init__(
        self,
        message: str,
        *,
        bundesland: str | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.bundesland: str | None = bundesland
        self.raw_response: str | None = raw_response[:500] if raw_response else None


class AlkisRetryExhaustedError(AlkisError):
    """Every retry attempt against the ALKIS WFS failed."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        bundesland: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts: int = attempts
        self.bundesland: str | None = bundesland


# ── gesetze-im-internet.de (German federal statute feed) ─────────────


class GesetzeError(ConnectorError):
    """Base for any gesetze-im-internet.de failure."""


class GesetzeCallError(GesetzeError):
    """Transport-level failure when fetching from gesetze-im-internet.de.

    HTTP 5xx, connection refusal, timeout. Retry-eligible (the retry
    filter in the client matches this class). ``status_code`` is the
    HTTP code when applicable; ``url`` is the resource we hit.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.url: str | None = url


class GesetzeInvalidResponseError(GesetzeError):
    """A 2xx response whose body wasn't the expected shape.

    Covers: a 4xx hard error (NOT retried — distinct class so the retry
    filter skips it), the table-of-contents body not being parseable XML,
    or a law download that isn't a valid zip / has no ``.xml`` entry.
    ``url`` identifies the resource; ``raw_response`` carries up to ~500
    chars of the body when textual.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.url: str | None = url
        self.raw_response: str | None = raw_response[:500] if raw_response else None


class GesetzeRetryExhaustedError(GesetzeError):
    """Every retry attempt against gesetze-im-internet.de failed.

    ``attempts`` is the number of attempts made (≥1).
    """

    def __init__(self, message: str, *, attempts: int) -> None:
        super().__init__(message)
        self.attempts: int = attempts
