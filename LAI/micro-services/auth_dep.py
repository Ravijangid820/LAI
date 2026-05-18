"""Shared auth dependency for the microservice processes.

Both ``api.py`` (port 18001) and ``ddiq_report.py`` (mounted under
``/ddiq``) verify access tokens **issued by ``serve_rag``**. The
verification is stateless — only the shared HS256 secret + the
configured algorithm/issuer are needed, no database round-trip.

This module is the single place where ``micro-services/`` builds the
:class:`TokenIssuer` and the FastAPI ``get_current_user`` dependency
so both files import an identical instance. If the env is missing the
import fails fast — running a microservice without auth would
silently expose every endpoint.

Required env (must match what ``serve_rag`` reads):
    LAI_AUTH_JWT_ACCESS_SECRET   (>= 32 chars)

Optional knobs (defaults match :class:`AuthConfig`):
    LAI_AUTH_JWT_ALGORITHM, LAI_AUTH_JWT_ISSUER, LAI_AUTH_JWT_ACCESS_TTL_MINUTES, ...
"""
from __future__ import annotations

from lai.common.auth import AuthConfig, TokenIssuer, build_get_current_user

# Eager construction at import time. A missing or weak
# ``LAI_AUTH_JWT_ACCESS_SECRET`` raises here, which surfaces at uvicorn
# start as a clear traceback rather than at first request.
_auth_config: AuthConfig = AuthConfig()
_token_issuer: TokenIssuer = TokenIssuer(_auth_config)

# Public surface. Both api.py and ddiq_report.py import this name and
# use it via ``Depends(get_current_user)`` on every protected route.
get_current_user = build_get_current_user(_token_issuer)

__all__ = ["get_current_user"]
