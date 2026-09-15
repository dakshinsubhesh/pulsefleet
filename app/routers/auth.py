"""
PulseFleet — Auth endpoints (Day 7: Authentication)
"""
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.exceptions import ConflictError, UnauthorizedError
from app.security import create_access_token, hash_password, verify_password
from app import models, schemas

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: schemas.UserCreate, db: AsyncSession = Depends(get_db)) -> models.User:
    """Create a new user account. Fails with 409 if the username is taken."""
    existing = await db.execute(select(models.User).where(models.User.username == payload.username))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"Username '{payload.username}' is already taken.", error_code="duplicate_username")

    user = models.User(username=payload.username, hashed_password=hash_password(payload.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(f"Username '{payload.username}' is already taken.", error_code="duplicate_username")
    await db.refresh(user)
    return user


@router.post("/login", response_model=schemas.Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> schemas.Token:
    """
    Exchange username/password for a JWT access token.
    Uses the standard OAuth2 password flow (form-encoded body: username, password)
    so it works directly with Swagger's "Authorize" button.
    """
    result = await db.execute(select(models.User).where(models.User.username == form_data.username))
    user = result.scalar_one_or_none()

    # Deliberately identical error for "no such user" and "wrong password" —
    # distinguishing them would let an attacker enumerate valid usernames.
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise UnauthorizedError("Incorrect username or password.", error_code="invalid_credentials")
    if not user.is_active:
        raise UnauthorizedError("This account is inactive.", error_code="inactive_user")

    token, expires_in = create_access_token(subject=user.username)
    return schemas.Token(access_token=token, expires_in_minutes=expires_in)


@router.get("/me", response_model=schemas.UserResponse)
async def read_current_user(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Returns the caller's own profile. Requires a valid Bearer token."""
    return current_user
