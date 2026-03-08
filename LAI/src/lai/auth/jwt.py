"""JWT authentication — token creation and verification."""

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.auth.jwt")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, extra: dict | None = None) -> str:
    settings = get_settings().jwt
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": user_id, "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
    logger.debug("Access token created for user %s", user_id)
    return token


def create_refresh_token(user_id: str) -> str:
    settings = get_settings().jwt
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload or None."""
    settings = get_settings().jwt
    try:
        payload = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[settings.algorithm])
        return payload
    except JWTError as e:
        logger.debug("Token validation failed: %s", e)
        return None
