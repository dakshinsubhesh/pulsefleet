"""
PulseFleet — Auth dependency (Day 7: Authentication)

`get_current_user` is what actually protects a route: add it as a
Depends() parameter and FastAPI will reject the request with 401 before
the route body ever runs, unless a valid, non-expired token for an
active user is presented.
"""
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import UnauthorizedError
from app.security import decode_access_token
from app import models

# tokenUrl is where Swagger's "Authorize" button will POST credentials
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> models.User:
    if token is None:
        raise UnauthorizedError("Not authenticated. Provide a Bearer token.", error_code="not_authenticated")

    try:
        username = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Access token has expired.", error_code="token_expired")
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Invalid access token.", error_code="invalid_token")

    result = await db.execute(select(models.User).where(models.User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive.", error_code="invalid_token")

    return user
