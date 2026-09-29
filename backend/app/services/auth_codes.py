"""Одноразовые коды 2FA и восстановления пароля. Отправка email — имитация (запись в лог)."""

import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import hash_token
from ..config import settings
from ..logging_config import mask_email
from ..models import AuthCode, User
from ..models.base import utcnow
from ..models.enums import AuthCodePurpose

logger = logging.getLogger("itprof.email")

_PURPOSE_TITLES = {
    AuthCodePurpose.two_factor: "код входа",
    AuthCodePurpose.password_reset: "код восстановления пароля",
}


async def issue_code(db: AsyncSession, user: User, purpose: AuthCodePurpose) -> str:
    """Создаёт новый код (предыдущие неиспользованные аннулируются) и «отправляет» его на email."""
    now = utcnow()
    await db.execute(
        update(AuthCode)
        .where(AuthCode.user_id == user.id, AuthCode.purpose == purpose, AuthCode.used_at.is_(None))
        .values(used_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(AuthCode(
        user_id=user.id,
        purpose=purpose,
        code_hash=hash_token(code),
        expires_at=now + timedelta(minutes=settings.auth_code_expire_minutes),
    ))
    # Сам код в лог не пишется — только факт отправки
    logger.info("Email to %s: %s sent", mask_email(user.email), _PURPOSE_TITLES[purpose])
    return code


async def verify_code(db: AsyncSession, user: User, purpose: AuthCodePurpose, code: str) -> bool:
    now = utcnow()
    auth_code = (await db.execute(
        select(AuthCode)
        .where(
            AuthCode.user_id == user.id,
            AuthCode.purpose == purpose,
            AuthCode.used_at.is_(None),
            AuthCode.expires_at > now,
        )
        .order_by(AuthCode.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if auth_code is None or auth_code.attempts >= settings.auth_code_max_attempts:
        return False

    if not hmac.compare_digest(auth_code.code_hash, hash_token(code)):
        auth_code.attempts += 1
        db.add(auth_code)
        await db.commit()
        return False

    auth_code.used_at = now
    db.add(auth_code)
    return True


def debug_code(code: str) -> str | None:
    return code if settings.expose_debug_codes else None
