"""FastAPI router for the 7 auth endpoints.

See :doc:`/harsh/AUTH_PLAN` §4.2 for the contract. Endpoints:

    POST  /auth/signup           — create account, log in immediately
    POST  /auth/login            — bcrypt-verify, issue tokens
    POST  /auth/refresh          — exchange refresh cookie for new access token
    POST  /auth/logout           — revoke current refresh row, clear cookie
    GET   /auth/me               — hydrate the SPA from the access token
    POST  /auth/forgot-password  — issue a single-use reset token, mail it
    POST  /auth/reset-password   — consume token, set new password, revoke sessions

Design notes
------------

* Cookies. The refresh cookie is set on /auth/login, /auth/signup, and
  /auth/refresh. Path-scoped to ``/auth`` so it is never sent on chat
  or DDiQ data routes. ``HttpOnly`` blocks JS access; ``Secure`` is on
  by default; ``SameSite`` is configurable for cross-origin deploys.
* No email enumeration. ``/auth/forgot-password`` always returns 204
  whether or not the address resolved to a real account. ``/auth/login``
  returns the same 401 for "user not found" and "password mismatch".
* Password resets revoke every active refresh token for the user, so
  every device must re-login — the v1 substitute for a rotation chain.
* Opportunistic rehash on login. If the stored bcrypt rounds is below
  the current ``AuthConfig.bcrypt_rounds``, we re-hash with the new
  cost. Lets us raise the OWASP floor without a mass-rotation event.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final
from uuid import UUID

import asyncpg
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr, Field

from lai.api.email import EmailConfig, send_reset_email
from lai.common.auth import (
    AuthConfig,
    CurrentUser,
    InvalidCredentialsError,
    PasswordHasher,
    PasswordPolicyError,
    TokenIssuer,
    UserDisabledError,
    hash_refresh_token,
)
from lai.common.auth.repository import (
    RefreshTokenRepository,
    ResetTokenRepository,
    UserRepository,
    canonical_email,
)

__all__ = ["AuthDeps", "build_auth_router"]

_logger = logging.getLogger("lai.auth")

# Generic 401 body — identical for "user not found" and "wrong
# password" so timing + status + body are indistinguishable.
_INVALID_CREDENTIALS_DETAIL: Final[str] = "invalid email or password"


# ─── Request / response shapes ──────────────────────────────────────────────

class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
    full_name: str = Field(min_length=1, max_length=200)
    company: str | None = Field(default=None, max_length=200)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
    remember_me: bool = False


class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1)


class AccessTokenResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str = "Bearer"


class MeResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    company: str | None
    role: str


# ─── Dependency bag ─────────────────────────────────────────────────────────

class AuthDeps:
    """Bundle of resolved auth dependencies shared with route handlers.

    Built once at app startup and parked on ``app.state``; the router
    factory closes over it so handlers receive everything they need
    without reaching for module globals.
    """

    __slots__ = (
        "auth_config",
        "email_config",
        "hasher",
        "issuer",
        "pool",
        "refresh_repo",
        "reset_repo",
        "user_repo",
    )

    def __init__(
        self,
        *,
        auth_config: AuthConfig,
        email_config: EmailConfig | None,
        hasher: PasswordHasher,
        issuer: TokenIssuer,
        pool: asyncpg.Pool,
    ) -> None:
        self.auth_config: AuthConfig = auth_config
        self.email_config: EmailConfig | None = email_config
        self.hasher: PasswordHasher = hasher
        self.issuer: TokenIssuer = issuer
        self.pool: asyncpg.Pool = pool
        self.user_repo: UserRepository = UserRepository()
        self.refresh_repo: RefreshTokenRepository = RefreshTokenRepository()
        self.reset_repo: ResetTokenRepository = ResetTokenRepository()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _check_password_policy(deps: AuthDeps, password: str) -> None:
    """Apply the configured length policy. Length is the only v1 check.

    More structural rules (character classes, common-password
    rejection, breach checks) are intentionally deferred — NIST 800-63B
    has been arguing against them for years; length is what matters.
    """
    cfg = deps.auth_config
    if len(password) < cfg.password_min_length:
        raise PasswordPolicyError(
            f"password must be at least {cfg.password_min_length} characters",
        )
    if len(password) > cfg.password_max_length:
        raise PasswordPolicyError(
            f"password must be at most {cfg.password_max_length} characters",
        )


def _set_refresh_cookie(
    response: Response,
    deps: AuthDeps,
    raw_token: str,
    expires_at_epoch: int,
) -> None:
    import time

    cfg = deps.auth_config
    # Cookie max-age in seconds. Anchored to the row's expires_at so a
    # browser restart respects the issued lifetime.
    max_age = max(0, expires_at_epoch - int(time.time()))
    response.set_cookie(
        key=cfg.refresh_cookie_name,
        value=raw_token,
        max_age=max_age,
        path=cfg.refresh_cookie_path,
        secure=cfg.refresh_cookie_secure,
        httponly=True,
        samesite=cfg.refresh_cookie_samesite,
    )


def _clear_refresh_cookie(response: Response, deps: AuthDeps) -> None:
    cfg = deps.auth_config
    response.delete_cookie(
        key=cfg.refresh_cookie_name,
        path=cfg.refresh_cookie_path,
        secure=cfg.refresh_cookie_secure,
        httponly=True,
        samesite=cfg.refresh_cookie_samesite,
    )


def _build_me(user: dict[str, Any] | Any) -> MeResponse:
    """Project a UserRecord (or dict) into the wire ``/me`` shape."""
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        company=user.company,
        role=user.role,
    )


# ─── Router factory ─────────────────────────────────────────────────────────

def build_auth_router(
    deps: AuthDeps,
    *,
    get_current_user,  # noqa: ANN001 — dependency callable, FastAPI signature
) -> APIRouter:
    """Construct the auth router bound to ``deps``.

    Args:
        deps: Resolved auth deps (config + hasher + issuer + pool +
            repositories + optional email config).
        get_current_user: The FastAPI dependency built via
            :func:`build_get_current_user`. Used by ``/auth/me`` and
            (implicitly, via routes mounted elsewhere) for tenant
            isolation throughout the app.

    Returns:
        A :class:`fastapi.APIRouter` ready to ``app.include_router()``.
    """
    router = APIRouter(prefix="/auth", tags=["auth"])

    # ── POST /auth/signup ───────────────────────────────────────────
    @router.post(
        "/signup",
        response_model=AccessTokenResponse,
        status_code=status.HTTP_200_OK,
    )
    async def signup(
        body: SignupBody,
        response: Response,
    ) -> AccessTokenResponse:
        try:
            _check_password_policy(deps, body.password)
        except PasswordPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        password_hash = deps.hasher.hash(body.password)
        async with deps.pool.acquire() as conn:
            async with conn.transaction():
                try:
                    user = await deps.user_repo.create(
                        conn,
                        email=body.email,
                        password_hash=password_hash,
                        full_name=body.full_name,
                        company=body.company,
                    )
                except asyncpg.UniqueViolationError as exc:
                    # Map to 409 without leaking which constraint hit.
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="an account with this email already exists",
                    ) from exc

                access_token, expires_in = deps.issuer.issue_access_token(
                    user_id=user.id, email=user.email_canonical, role=user.role,
                )
                refresh = deps.issuer.issue_refresh_token(remember_me=False)
                await deps.refresh_repo.create(
                    conn,
                    user_id=user.id,
                    token_hash=refresh.token_hash,
                    expires_at=refresh.expires_at,
                )

        _set_refresh_cookie(
            response, deps, refresh.raw, int(refresh.expires_at.timestamp()),
        )
        return AccessTokenResponse(access_token=access_token, expires_in=expires_in)

    # ── POST /auth/login ────────────────────────────────────────────
    @router.post(
        "/login",
        response_model=AccessTokenResponse,
        status_code=status.HTTP_200_OK,
    )
    async def login(
        body: LoginBody,
        response: Response,
    ) -> AccessTokenResponse:
        async with deps.pool.acquire() as conn:
            user = await deps.user_repo.get_by_email(conn, body.email)
            # Always run a verify, even on a missing user, so timing
            # does not leak the membership signal. Compare against a
            # known invalid hash — passlib will return False in
            # constant-ish time.
            stored_hash = (
                user.password_hash
                if user is not None
                else "$2b$12$invalidinvalidinvalidinvalidinvalidinvalidinvalidinval"
            )
            if not deps.hasher.verify(body.password, stored_hash) or user is None:
                raise InvalidCredentialsError(_INVALID_CREDENTIALS_DETAIL)
            if user.status != "active":
                # Distinct exception so logs can attribute, but the
                # network response is still a generic 401 — we do not
                # want to confirm "this email exists but is disabled".
                raise UserDisabledError(_INVALID_CREDENTIALS_DETAIL)

            # Opportunistic rehash when the cost floor has been raised.
            if deps.hasher.needs_rehash(user.password_hash):
                await deps.user_repo.update_password_hash(
                    conn, user.id, deps.hasher.hash(body.password),
                )

            access_token, expires_in = deps.issuer.issue_access_token(
                user_id=user.id, email=user.email_canonical, role=user.role,
            )
            refresh = deps.issuer.issue_refresh_token(remember_me=body.remember_me)
            await deps.refresh_repo.create(
                conn,
                user_id=user.id,
                token_hash=refresh.token_hash,
                expires_at=refresh.expires_at,
            )
            await deps.user_repo.touch_last_login(conn, user.id)

        _set_refresh_cookie(
            response, deps, refresh.raw, int(refresh.expires_at.timestamp()),
        )
        return AccessTokenResponse(access_token=access_token, expires_in=expires_in)

    # ── POST /auth/refresh ──────────────────────────────────────────
    @router.post(
        "/refresh",
        response_model=AccessTokenResponse,
        status_code=status.HTTP_200_OK,
    )
    async def refresh(
        request: Request,
        response: Response,
    ) -> AccessTokenResponse:
        cookie_value = request.cookies.get(deps.auth_config.refresh_cookie_name)
        if not cookie_value:
            raise HTTPException(status_code=401, detail="missing refresh cookie")

        token_hash = hash_refresh_token(cookie_value)
        async with deps.pool.acquire() as conn:
            row = await deps.refresh_repo.get_active_by_hash(conn, token_hash)
            if row is None:
                _clear_refresh_cookie(response, deps)
                raise HTTPException(status_code=401, detail="refresh token rejected")
            user = await deps.user_repo.get_by_id(conn, row.user_id)
            if user is None or user.status != "active":
                _clear_refresh_cookie(response, deps)
                raise HTTPException(status_code=401, detail="refresh token rejected")

            access_token, expires_in = deps.issuer.issue_access_token(
                user_id=user.id, email=user.email_canonical, role=user.role,
            )

        # Refresh the cookie's max-age but keep the same opaque value
        # and DB row. (v1 has no rotation chain — see AUTH_PLAN §12.)
        _set_refresh_cookie(response, deps, cookie_value, int(row.expires_at.timestamp()))
        return AccessTokenResponse(access_token=access_token, expires_in=expires_in)

    # ── POST /auth/logout ───────────────────────────────────────────
    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(
        request: Request,
        response: Response,
    ) -> Response:
        cookie_value = request.cookies.get(deps.auth_config.refresh_cookie_name)
        if cookie_value:
            async with deps.pool.acquire() as conn:
                await deps.refresh_repo.revoke_by_hash(
                    conn, hash_refresh_token(cookie_value),
                )
        _clear_refresh_cookie(response, deps)
        # Returning Response directly lets us avoid the 200 wrapper
        # the framework would add for an empty body.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ── GET /auth/me ────────────────────────────────────────────────
    @router.get("/me", response_model=MeResponse)
    async def me(
        user: CurrentUser = Depends(get_current_user),
    ) -> MeResponse:
        async with deps.pool.acquire() as conn:
            record = await deps.user_repo.get_by_id(conn, user.id)
        if record is None:
            # Access token decoded successfully but the user vanished
            # (admin disabled / deleted between token issuance and
            # this call). Surface as 401 so the SPA logs them out.
            raise HTTPException(status_code=401, detail="account not found")
        return _build_me(record)

    # ── POST /auth/forgot-password ──────────────────────────────────
    @router.post(
        "/forgot-password",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def forgot_password(
        body: ForgotPasswordBody,
        background_tasks: BackgroundTasks,
    ) -> Response:
        # No-enumeration: always 204. We do the lookup + token mint +
        # email schedule only when the address resolves; the absence
        # of a response-shape difference protects user privacy.
        async with deps.pool.acquire() as conn:
            user = await deps.user_repo.get_by_email(conn, body.email)
            if user is not None and user.status == "active":
                reset = deps.issuer.issue_reset_token()
                await deps.reset_repo.create(
                    conn,
                    user_id=user.id,
                    token_hash=reset.token_hash,
                    expires_at=reset.expires_at,
                )
                if deps.email_config is not None:
                    background_tasks.add_task(
                        send_reset_email,
                        deps.email_config,
                        recipient_email=user.email,
                        recipient_name=user.full_name,
                        raw_reset_token=reset.raw,
                        ttl_minutes=deps.auth_config.reset_token_ttl_minutes,
                    )
                else:
                    # Misconfigured environment: the reset row is
                    # written but no email goes out. Log loud so ops
                    # sees deliverability is silently broken.
                    _logger.error(
                        "auth.forgot_password.no_email_config",
                        extra={"user_id": str(user.id)},
                    )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ── POST /auth/reset-password ───────────────────────────────────
    @router.post(
        "/reset-password",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def reset_password(
        body: ResetPasswordBody,
    ) -> Response:
        try:
            _check_password_policy(deps, body.new_password)
        except PasswordPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        token_hash = hash_refresh_token(body.token)
        new_hash = deps.hasher.hash(body.new_password)

        async with deps.pool.acquire() as conn:
            async with conn.transaction():
                row = await deps.reset_repo.consume(conn, token_hash)
                if row is None:
                    raise HTTPException(
                        status_code=400,
                        detail="reset token invalid or expired",
                    )
                await deps.user_repo.update_password_hash(conn, row.user_id, new_hash)
                # Force a re-login on every device. v1's stand-in for
                # a refresh-rotation theft-detection chain.
                await deps.refresh_repo.revoke_all_for_user(conn, row.user_id)

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # FastAPI ``APIRouter`` has no ``add_exception_handler``; the
    # translation of :class:`InvalidCredentialsError` /
    # :class:`UserDisabledError` into 401 happens at the app level via
    # :func:`register_auth_exception_handlers`.

    return router


def register_auth_exception_handlers(app) -> None:  # noqa: ANN001 — FastAPI app type avoided to keep this importable from microservices
    """Register the 401/403 translation for auth-module exceptions.

    Mount once on the application alongside ``include_router``.
    """
    from fastapi import FastAPI  # local import to avoid forcing this module to depend on fastapi at import-of-our-module time

    assert isinstance(app, FastAPI)  # noqa: S101 — type guard for the optional callers

    @app.exception_handler(InvalidCredentialsError)
    async def _invalid_credentials(_request: Request, exc: InvalidCredentialsError) -> Response:
        from fastapi.responses import JSONResponse
        _logger.info("auth.login.invalid_credentials")
        return JSONResponse(
            status_code=401,
            content={"detail": _INVALID_CREDENTIALS_DETAIL},
            headers={"WWW-Authenticate": 'Bearer realm="lai"'},
        )

    @app.exception_handler(UserDisabledError)
    async def _user_disabled(_request: Request, exc: UserDisabledError) -> Response:
        from fastapi.responses import JSONResponse
        _logger.info("auth.login.user_disabled")
        return JSONResponse(
            status_code=401,
            content={"detail": _INVALID_CREDENTIALS_DETAIL},
            headers={"WWW-Authenticate": 'Bearer realm="lai"'},
        )
