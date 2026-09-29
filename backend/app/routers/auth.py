import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    create_access_token,
    create_csrf_token,
    create_two_factor_token,
    decode_token,
    generate_refresh_token,
    get_current_user,
    hash_password,
    hash_token,
    verify_password,
)
from ..config import settings
from ..database import get_db
from ..errors import ApiError, BadRequestError, ForbiddenError, UnauthorizedError
from ..middleware import auth_rate_limit
from ..models import RefreshToken, User
from ..models.base import utcnow
from ..models.enums import AuthCodePurpose, UserRole
from ..responses import ApiResponse, ok
from ..schemas.auth import (
    CodeSentResponse,
    CsrfTokenResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenPair,
    TwoFactorChallenge,
    TwoFactorRequest,
)
from ..services.auth_codes import debug_code, issue_code, verify_code
from ..services.profiles import user_brief

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("itprof.auth")

_INVALID_CREDENTIALS = "Неверный email или пароль"


async def _open_session(db: AsyncSession, user: User, request: Request) -> TokenPair:
    """Создаёт сессию (refresh-токен) и выдаёт пару токенов."""
    raw, token_hash, expires_at = generate_refresh_token()
    session = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=(request.headers.get("user-agent") or "")[:255] or None,
    )
    db.add(session)
    await db.flush()
    access_token = create_access_token(user.id, user.role, session.id)
    await db.commit()
    request.state.user_id = user.id
    return TokenPair(
        access_token=access_token,
        refresh_token=raw,
        expires_in=settings.access_token_expire_minutes * 60,
        user=user_brief(user),
    )


async def _find_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()


@router.post(
    "/register",
    response_model=ApiResponse[TokenPair],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
    summary="Регистрация ИТ-специалиста",
)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = User(
        email=body.email.lower(),
        last_name=body.last_name,
        first_name=body.first_name,
        patronymic=body.patronymic,
        phone=body.phone,
        hashed_password=hash_password(body.password),
        role=UserRole.user,
    )
    db.add(user)
    await db.flush()  # нарушение уникальности email/телефона → 400 DUPLICATE
    await db.refresh(user, ["role_info"])
    return ok(await _open_session(db, user, request), "Регистрация выполнена")


@router.post(
    "/login",
    response_model=ApiResponse[TokenPair | TwoFactorChallenge],
    dependencies=[Depends(auth_rate_limit)],
    summary="Вход по email и паролю (JWT)",
)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = await _find_user_by_email(db, body.email)
    if not user or not verify_password(body.password, user.hashed_password, body.password_prehashed):
        raise UnauthorizedError(_INVALID_CREDENTIALS, "INVALID_CREDENTIALS")
    if not user.is_active:
        raise ForbiddenError("Учётная запись заблокирована", "ACCOUNT_INACTIVE")

    if user.two_factor_enabled:
        code = await issue_code(db, user, AuthCodePurpose.two_factor)
        await db.commit()
        challenge = TwoFactorChallenge(
            two_factor_token=create_two_factor_token(user.id), debug_code=debug_code(code),
        )
        return ok(challenge, "Код подтверждения отправлен на email")

    return ok(await _open_session(db, user, request), "Вход выполнен")


@router.post(
    "/2fa",
    response_model=ApiResponse[TokenPair],
    dependencies=[Depends(auth_rate_limit)],
    summary="Второй шаг входа: код из email",
)
async def two_factor(body: TwoFactorRequest, request: Request, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.two_factor_token, "2fa")
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("Пользователь не найден", "USER_INACTIVE")
    if not await verify_code(db, user, AuthCodePurpose.two_factor, body.code):
        raise UnauthorizedError("Неверный или просроченный код", "INVALID_2FA_CODE")
    return ok(await _open_session(db, user, request), "Вход выполнен")


@router.post("/refresh", response_model=ApiResponse[TokenPair], summary="Обновление пары токенов")
async def refresh(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)):
    stored = (await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token))
    )).scalar_one_or_none()
    if stored is None or stored.revoked_at is not None or stored.expires_at < utcnow():
        raise UnauthorizedError("Refresh-токен недействителен или истёк", "INVALID_REFRESH_TOKEN")

    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Пользователь не найден или заблокирован", "USER_INACTIVE")

    # Ротация: старая сессия закрывается, выдаётся новая пара
    stored.revoked_at = utcnow()
    db.add(stored)
    return ok(await _open_session(db, user, request), "Токен обновлён")


@router.post("/logout", response_model=ApiResponse[None], summary="Выход (инвалидация токенов)")
async def logout(
    request: Request,
    body: LogoutRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = update(RefreshToken).where(
        RefreshToken.user_id == current_user.id, RefreshToken.revoked_at.is_(None)
    )
    if not (body and body.all_devices):
        query = query.where(RefreshToken.id == request.state.session_id)
    await db.execute(query.values(revoked_at=utcnow()))
    await db.commit()
    message = "Выполнен выход на всех устройствах" if body and body.all_devices else "Выход выполнен"
    return ok(None, message)


@router.get("/csrf-token", response_model=ApiResponse[CsrfTokenResponse], summary="CSRF-токен для веб-клиента")
async def csrf_token():
    return ok(CsrfTokenResponse(csrf_token=create_csrf_token()))


@router.post(
    "/password/forgot",
    response_model=ApiResponse[CodeSentResponse],
    dependencies=[Depends(auth_rate_limit)],
    summary="Восстановление пароля: отправка кода на email",
)
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    user = await _find_user_by_email(db, body.email)
    code = None
    if user and user.is_active:
        code = await issue_code(db, user, AuthCodePurpose.password_reset)
        await db.commit()
    # Ответ одинаковый для существующих и несуществующих email — нельзя перебрать базу
    return ok(
        CodeSentResponse(debug_code=debug_code(code) if code else None),
        "Если email зарегистрирован, на него отправлен код восстановления",
    )


@router.post(
    "/password/reset",
    response_model=ApiResponse[None],
    dependencies=[Depends(auth_rate_limit)],
    summary="Установка нового пароля по коду",
)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    user = await _find_user_by_email(db, body.email)
    if user is None or not await verify_code(db, user, AuthCodePurpose.password_reset, body.code):
        raise BadRequestError("Неверный или просроченный код", "INVALID_RESET_CODE")
    user.hashed_password = hash_password(body.new_password)
    db.add(user)
    # После смены пароля завершаем все сессии
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()
    logger.info("Password reset for user_id=%s", user.id)
    return ok(None, "Пароль изменён. Войдите с новым паролем")


@router.post("/oauth/{provider}", response_model=ApiResponse[None], summary="Вход через Google/GitHub (заглушка)")
async def oauth_login(provider: str):
    if provider not in ("google", "github"):
        raise BadRequestError("Неизвестный провайдер", "UNKNOWN_PROVIDER")
    raise ApiError(status.HTTP_501_NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "Вход через соцсети в разработке")
