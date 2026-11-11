"""
Authentication and security module for NiftyDebateAI.

Implements:
1. Cryptographic password hashing (bcrypt with standard library PBKDF2 fallback).
2. JWT token issuance, verification, and decoding (PyJWT HS256).
3. FastAPI security dependencies:
   - `get_current_user`: Strict dependency requiring valid Bearer token.
   - `get_optional_user`: Soft dependency allowing guest access while scoping logged-in users.
4. Pydantic request & response schemas for registration, login, profile, voting, and watchlists.
5. FastAPI auth APIRouter exposing /api/auth/register, /login, /me, /logout.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import UserModel, get_session
from time_utils import ist_now, to_ist_iso

logger = logging.getLogger("niftydebate.auth")

# ── JWT Configuration ─────────────────────────────────────────────────────────

JWT_SECRET_KEY = getattr(
    settings,
    "JWT_SECRET_KEY",
    os.getenv("JWT_SECRET_KEY", "niftydebate_secret_jwt_key_super_secure_99182"),
)
JWT_ALGORITHM = getattr(settings, "JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24 * 7)

bearer_scheme = HTTPBearer(auto_error=False)


# ── Password Hashing Engine ───────────────────────────────────────────────────

try:
    import bcrypt as _bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False


def hash_password(plain_password: str) -> str:
    """Hash a password using bcrypt if installed, else secure PBKDF2-HMAC-SHA256."""
    if HAS_BCRYPT:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        salt = _bcrypt.gensalt(rounds=12)
        return _bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")
    else:
        salt = secrets.token_hex(16)
        key = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        )
        return f"pbkdf2:sha256:100000${salt}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against stored bcrypt or PBKDF2 hash."""
    if not hashed_password or not plain_password:
        return False

    if hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$"):
        if HAS_BCRYPT:
            try:
                return _bcrypt.checkpw(
                    plain_password.encode("utf-8")[:72],
                    hashed_password.encode("utf-8"),
                )
            except Exception as e:
                logger.error("Bcrypt check error: %s", e)
                return False
        return False

    if hashed_password.startswith("pbkdf2:"):
        parts = hashed_password.split("$")
        if len(parts) == 3:
            salt = parts[1]
            expected = parts[2]
            key = hashlib.pbkdf2_hmac(
                "sha256",
                plain_password.encode("utf-8"),
                salt.encode("utf-8"),
                100_000,
            )
            return secrets.compare_digest(key.hex(), expected)

    return False


# ── Token Generation & Decoding ───────────────────────────────────────────────

def create_access_token(data: dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Issue a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token, raising HTTPException if invalid."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Pydantic Request & Response Models ─────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Valid email address")
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    full_name: Optional[str] = Field(None, max_length=100)


class UserLoginRequest(BaseModel):
    username_or_email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    full_name: Optional[str] = None
    is_active: bool
    is_superuser: bool = False
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserVoteRequest(BaseModel):
    vote: str = Field(..., description="BULL, BEAR, AGREE_JUDGE, or DISAGREE_JUDGE")
    confidence: Optional[int] = Field(None, ge=1, le=10)
    comment: Optional[str] = Field(None, max_length=500)


class UserWatchlistRequest(BaseModel):
    symbol: str = Field(default="NIFTY", description="Index symbol: NIFTY, BANKNIFTY")
    strike: int
    option_type: str = Field(..., description="CE or PE")
    notes: Optional[str] = Field(None, max_length=255)


# ── FastAPI Dependencies ──────────────────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> UserModel:
    """Strict dependency: requires valid Bearer token and returns active UserModel."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    stmt = select(UserModel).where(UserModel.id == user_id)
    user = (await session.execute(stmt)).scalars().first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Optional[UserModel]:
    """Soft dependency: returns UserModel if valid Bearer token provided, else None."""
    if not credentials or not credentials.credentials:
        return None

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
        stmt = select(UserModel).where(UserModel.id == user_id)
        user = (await session.execute(stmt)).scalars().first()
        return user if (user and user.is_active) else None
    except Exception:
        return None


# ── Auth APIRouter ────────────────────────────────────────────────────────────

auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@auth_router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: UserRegisterRequest, session: AsyncSession = Depends(get_session)):
    """Register a new user account and return JWT access token."""
    clean_email = req.email.strip().lower()
    clean_username = req.username.strip()

    # Check for existing user
    stmt = select(UserModel).where(
        (UserModel.email == clean_email) | (UserModel.username == clean_username)
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        if existing.email == clean_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username is already taken.",
            )

    new_user = UserModel(
        email=clean_email,
        username=clean_username,
        hashed_password=hash_password(req.password),
        full_name=req.full_name.strip() if req.full_name else None,
        is_active=True,
    )
    session.add(new_user)
    try:
        await session.commit()
        await session.refresh(new_user)
    except (IntegrityError, Exception):
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email is already registered.",
        )

    token = create_access_token(
        data={"sub": str(new_user.id), "username": new_user.username, "email": new_user.email}
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            id=new_user.id,
            email=new_user.email,
            username=new_user.username,
            full_name=new_user.full_name,
            is_active=new_user.is_active,
            is_superuser=new_user.is_superuser,
            created_at=to_ist_iso(new_user.created_at),
        ),
    )


@auth_router.post("/login", response_model=TokenResponse)
async def login(req: UserLoginRequest, session: AsyncSession = Depends(get_session)):
    """Authenticate with username or email and password."""
    ident = req.username_or_email.strip()
    stmt = select(UserModel).where(
        (UserModel.email == ident.lower()) | (UserModel.username == ident)
    )
    user = (await session.execute(stmt)).scalars().first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is deactivated.",
        )

    token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "email": user.email}
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            created_at=to_ist_iso(user.created_at),
        ),
    )


@auth_router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserModel = Depends(get_current_user)):
    """Fetch current authenticated user profile."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_superuser=current_user.is_superuser,
        created_at=to_ist_iso(current_user.created_at),
    )


@auth_router.post("/logout")
async def logout(current_user: UserModel = Depends(get_current_user)):
    """Logout endpoint acknowledging client token disposal."""
    return {"status": "success", "message": "Logged out successfully."}
