"""Authentication routes.

POST /auth/register — create account
POST /auth/login    — get JWT tokens
GET  /auth/me       — current user info
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

from lai.auth.jwt import create_access_token, create_refresh_token, decode_token, verify_password
from lai.auth.repository import create_user, get_user_by_email, get_user_by_id
from lai.core.logging import get_logger

logger = get_logger("lai.auth.routes")
router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Dependency — extract and validate current user from JWT."""
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


@router.post("/register", response_model=TokenResponse)
async def register(request: RegisterRequest):
    existing = await get_user_by_email(request.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await create_user(request.email, request.password)
    logger.info("User registered: %s", user["email"])
    return TokenResponse(
        access_token=create_access_token(user["id"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    user = await get_user_by_email(request.email)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    logger.info("User logged in: %s", user["email"])
    return TokenResponse(
        access_token=create_access_token(user["id"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"]}
