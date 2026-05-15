from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from core.config import get_settings
from core.deps import CurrentUser, DbSession
from core.limiter import limiter
from core.security import create_access_token, hash_password, verify_password
from db.models import User
from db.schemas import AuthResponse, LoginRequest, SignupRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name_access,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.cookie_name_access, path="/")


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: get_settings().auth_rate_limit)
async def signup(
    request: Request,
    response: Response,
    payload: SignupRequest,
    db: DbSession,
) -> AuthResponse:
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        default_market=payload.default_market,
        risk_profile=payload.risk_profile,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(subject=str(user.id))
    _set_auth_cookie(response, token)
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthResponse)
@limiter.limit(lambda: get_settings().auth_rate_limit)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: DbSession,
) -> AuthResponse:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = create_access_token(subject=str(user.id))
    _set_auth_cookie(response, token)
    return AuthResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    _clear_auth_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
