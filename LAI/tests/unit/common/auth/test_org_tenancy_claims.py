"""Phase A (firm tenancy) identity plumbing.

The ``org_id`` must flow through the access token and into
:class:`CurrentUser`, and — crucially — be **optional on the wire** so that
(a) an org-less user round-trips to ``None`` and (b) a token minted before
migration 002 (no ``org_id`` claim at all) still decodes instead of locking
every existing session out at the cutover. Pure token/dataclass tests; no DB.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from jose import jwt

from lai.common.auth.config import AuthConfig
from lai.common.auth.dependencies import build_get_current_user
from lai.common.auth.exceptions import InvalidTokenError
from lai.common.auth.models import CurrentUser
from lai.common.auth.tokens import TokenIssuer

_SECRET = "test-secret-org-tenancy-0123456789abcdef"


def _cfg() -> AuthConfig:
    return AuthConfig(jwt_access_secret=_SECRET)


def _issuer() -> TokenIssuer:
    return TokenIssuer(_cfg())


def test_access_token_round_trips_org_id() -> None:
    iss = _issuer()
    uid, org = uuid4(), uuid4()
    token, _ = iss.issue_access_token(
        user_id=uid, email="a@b.de", role="user", org_id=org,
    )
    claims = iss.decode_access_token(token)
    assert claims.user_id == uid
    assert claims.org_id == org


def test_org_less_user_round_trips_none() -> None:
    iss = _issuer()
    token, _ = iss.issue_access_token(
        user_id=uuid4(), email="a@b.de", role="user", org_id=None,
    )
    assert iss.decode_access_token(token).org_id is None


def test_org_id_defaults_to_none_when_not_passed() -> None:
    # issue_access_token's org_id is optional (defaults None) so existing
    # call sites that don't yet pass it keep working.
    iss = _issuer()
    token, _ = iss.issue_access_token(user_id=uuid4(), email="a@b.de", role="user")
    assert iss.decode_access_token(token).org_id is None


def test_pre_tenancy_token_without_org_claim_decodes_to_none() -> None:
    # A token minted before migration 002 has no org_id claim at all; it must
    # still decode (to org_id=None), not be rejected.
    cfg = _cfg()
    iss = TokenIssuer(cfg)
    now = datetime.now(timezone.utc)
    legacy = jwt.encode(
        {
            "iss": cfg.jwt_issuer,
            "sub": str(uuid4()),
            "email": "old@b.de",
            "role": "user",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        _SECRET,
        algorithm=cfg.jwt_algorithm,
    )
    assert iss.decode_access_token(legacy).org_id is None


def test_malformed_org_id_claim_is_rejected() -> None:
    # A *present but unparseable* org_id is a tampered token → reject.
    cfg = _cfg()
    iss = TokenIssuer(cfg)
    now = datetime.now(timezone.utc)
    bad = jwt.encode(
        {
            "iss": cfg.jwt_issuer,
            "sub": str(uuid4()),
            "email": "x@b.de",
            "role": "user",
            "org_id": "not-a-uuid",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        _SECRET,
        algorithm=cfg.jwt_algorithm,
    )
    with pytest.raises(InvalidTokenError, match="org_id"):
        iss.decode_access_token(bad)


def test_get_current_user_dependency_carries_org_id() -> None:
    from fastapi.security import HTTPAuthorizationCredentials

    iss = _issuer()
    org = uuid4()
    token, _ = iss.issue_access_token(
        user_id=uuid4(), email="a@b.de", role="admin", org_id=org,
    )
    dep = build_get_current_user(iss)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = asyncio.run(dep(creds))
    assert isinstance(user, CurrentUser)
    assert user.org_id == org
    assert user.is_admin


def test_current_user_org_id_defaults_none() -> None:
    assert CurrentUser(id=uuid4(), email="a@b.de", role="user").org_id is None
