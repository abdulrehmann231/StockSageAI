import uuid
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.security import decode_access_token
from db.models import User
from db.session import get_session

settings = get_settings()

# Bearer token kept as a fallback (e.g. server-to-server, tooling).
# Primary auth path is the httpOnly cookie set on signup/login.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]


def _extract_token(
    cookie_token: str | None,
    bearer_token: str | None,
) -> str:
    token = cookie_token or bearer_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def get_current_user(
    db: DbSession,
    access_token: Annotated[str | None, Cookie(alias=settings.cookie_name_access)] = None,
    bearer_token: Annotated[str | None, Depends(_oauth2_scheme)] = None,
) -> User:
    token = _extract_token(access_token, bearer_token)
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
