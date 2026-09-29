import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .errors import ForbiddenError, UnauthorizedError
from .models import RefreshToken, User
from .models.base import utcnow
from .models.enums import EMPLOYER_ROLES, SHORTLIST_ROLES, UserRole

password_hasher = PasswordHash.recommended()  # Argon2id
_bearer = HTTPBearer(auto_error=False, description="JWT access-токен из POST /auth/login")

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_LAST_SEEN_UPDATE_INTERVAL = timedelta(minutes=5)


# ── Пароли ────────────────────────────────────────────────────────────────────
# Клиенты могут отправлять пароль уже захешированным (SHA-256 hex, флаг password_prehashed) —
# так пароль не передаётся в открытом виде даже внутри TLS. Сервер приводит оба варианта
# к SHA-256 и хранит Argon2id-хеш от него.

def prehash_password(password: str, is_prehashed: bool = False) -> str:
    if is_prehashed:
        if not _SHA256_HEX.match(password):
            raise ValueError("Ожидается SHA-256 хеш пароля (64 hex-символа)")
        return password.lower()
    return hashlib.sha256(password.encode()).hexdigest()


def hash_password(password: str, is_prehashed: bool = False) -> str:
    return password_hasher.hash(prehash_password(password, is_prehashed))


def verify_password(password: str, hashed: str, is_prehashed: bool = False) -> bool:
    try:
        return password_hasher.verify(prehash_password(password, is_prehashed), hashed)
    except ValueError:
        return False


# ── Access-токен (JWT) ────────────────────────────────────────────────────────

def create_access_token(user_id: int, role: str, session_id: int) -> str:
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "sid": session_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_two_factor_token(user_id: int) -> str:
    """Короткоживущий токен между вводом пароля и вводом кода 2FA."""
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "type": "2fa",
        "iat": now,
        "exp": now + timedelta(minutes=settings.two_factor_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Срок действия токена истёк", "TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Недействительный токен", "INVALID_TOKEN") from exc
    if payload.get("type") != expected_type:
        raise UnauthorizedError("Неверный тип токена", "INVALID_TOKEN")
    return payload


# ── Refresh-токен (непрозрачный) ──────────────────────────────────────────────

def generate_refresh_token() -> tuple[str, str, datetime]:
    """Вернуть (raw_token, token_hash, expires_at)."""
    raw = secrets.token_urlsafe(48)
    expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
    return raw, hash_token(raw), expires_at


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── CSRF-токен (для браузерных клиентов с другого origin) ─────────────────────
# Формат: <nonce>.<timestamp>.<hmac> — подпись без хранения на сервере.

def create_csrf_token() -> str:
    nonce = secrets.token_urlsafe(16)
    timestamp = str(int(utcnow().timestamp()))
    signature = _sign(f"{nonce}.{timestamp}")
    return f"{nonce}.{timestamp}.{signature}"


def verify_csrf_token(token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    nonce, timestamp, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(f"{nonce}.{timestamp}")):
        return False
    try:
        issued_at = int(timestamp)
    except ValueError:
        return False
    return utcnow().timestamp() - issued_at <= settings.csrf_token_ttl_hours * 3600


def _sign(value: str) -> str:
    return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


# ── Зависимости FastAPI ───────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError()

    payload = decode_token(credentials.credentials, "access")
    user_id = int(payload["sub"])

    # Сессия могла быть завершена через /auth/logout — тогда токен больше не действует
    session = await db.get(RefreshToken, payload.get("sid"))
    if session is None or session.user_id != user_id or session.revoked_at is not None:
        raise UnauthorizedError("Сессия завершена. Войдите снова", "SESSION_REVOKED")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Пользователь не найден или заблокирован", "USER_INACTIVE")

    request.state.user_id = user.id
    request.state.session_id = session.id

    now = utcnow()
    if user.last_seen_at is None or now - user.last_seen_at > _LAST_SEEN_UPDATE_INTERVAL:
        user.last_seen_at = now
        db.add(user)
        await db.commit()
    return user


def require_roles(*roles: str):
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ForbiddenError("Недостаточно прав для выполнения операции")
        return user

    return dependency


get_current_employer = require_roles(*EMPLOYER_ROLES)
get_current_recruiter = require_roles(*SHORTLIST_ROLES)
get_current_admin = require_roles(UserRole.admin)
