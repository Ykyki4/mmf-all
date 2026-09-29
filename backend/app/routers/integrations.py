"""Заглушки внешних сервисов верификации: Госуслуги, GitHub, Telegram.

Подключённые сервисы повышают уровень доверия (триггер пересчёта рейтинга),
а верификация через Госуслуги делает подтверждения пользователя «верифицированными».
"""

import random

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..errors import ApiError, NotFoundError
from ..models import User, UserIntegration
from ..models.base import utcnow
from ..models.enums import IntegrationProvider, IntegrationStatus
from ..responses import ApiResponse, ok
from ..schemas.system import IntegrationConnect, IntegrationRead, IntegrationsOverview

router = APIRouter(prefix="/integrations", tags=["integrations"])

PROVIDER_TITLES = {
    IntegrationProvider.gosuslugi: "Госуслуги",
    IntegrationProvider.github: "GitHub",
    IntegrationProvider.telegram: "Telegram",
}
TRUST_BONUS_PER_SERVICE = 5          # совпадает с recalc_user_rating в БД
SIMULATED_SUCCESS_PROBABILITY = 0.8
_NO_SERVICES_MESSAGE = "Подключите хотя бы один сервис для повышения доверия"


def _integration_read(provider: IntegrationProvider, integration: UserIntegration | None) -> IntegrationRead:
    connected = integration is not None and integration.status == IntegrationStatus.connected
    status_value = "connected" if connected else ("failed" if integration else "not_connected")
    return IntegrationRead(
        provider=provider,
        title=PROVIDER_TITLES[provider],
        status=status_value,
        status_title="Подтверждено" if connected else "Не подтверждено",
        external_username=integration.external_username if integration else None,
        repositories_count=integration.repositories_count if integration else None,
        verified_at=integration.verified_at if integration else None,
        trust_bonus=TRUST_BONUS_PER_SERVICE if connected else 0,
    )


async def _overview(db: AsyncSession, user: User) -> IntegrationsOverview:
    integrations = {
        item.provider: item
        for item in (await db.execute(
            select(UserIntegration).where(UserIntegration.user_id == user.id)
        )).scalars()
    }
    items = [_integration_read(provider, integrations.get(provider)) for provider in IntegrationProvider]
    connected = sum(1 for item in items if item.status == "connected")
    return IntegrationsOverview(
        items=items,
        connected_count=connected,
        message=None if connected else _NO_SERVICES_MESSAGE,
    )


@router.get("", response_model=ApiResponse[IntegrationsOverview], summary="Статусы внешних сервисов")
async def list_integrations(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ok(await _overview(db, current_user))


@router.post(
    "/{provider}/connect",
    response_model=ApiResponse[IntegrationRead],
    summary="Подключить сервис (имитация: Госуслуги — случайный результат, GitHub/Telegram — OAuth-заглушка)",
)
async def connect_integration(
    provider: IntegrationProvider,
    body: IntegrationConnect | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    body = body or IntegrationConnect()
    if body.simulate == "random":
        success = random.random() < SIMULATED_SUCCESS_PROBABILITY
    else:
        success = body.simulate == "success"

    integration = (await db.execute(
        select(UserIntegration).where(
            UserIntegration.user_id == current_user.id, UserIntegration.provider == provider,
        )
    )).scalar_one_or_none() or UserIntegration(user_id=current_user.id, provider=provider)

    integration.status = IntegrationStatus.connected if success else IntegrationStatus.failed
    integration.verified_at = utcnow() if success else None
    if provider == IntegrationProvider.github:
        integration.external_username = body.username or current_user.email.split("@")[0]
        integration.repositories_count = random.randint(3, 60) if success else None
    elif provider == IntegrationProvider.telegram:
        username = body.username or current_user.email.split("@")[0]
        integration.external_username = username if username.startswith("@") else f"@{username}"
    db.add(integration)
    await db.commit()  # триггер пересчитывает уровень доверия

    if not success:
        raise ApiError(
            status.HTTP_502_BAD_GATEWAY,
            "INTEGRATION_FAILED",
            f"Сервис «{PROVIDER_TITLES[provider]}» не подтвердил данные. Попробуйте ещё раз",
        )
    return ok(_integration_read(provider, integration), f"{PROVIDER_TITLES[provider]}: подтверждено")


@router.delete("/{provider}", response_model=ApiResponse[IntegrationsOverview], summary="Отключить сервис")
async def disconnect_integration(
    provider: IntegrationProvider,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        delete(UserIntegration).where(
            UserIntegration.user_id == current_user.id, UserIntegration.provider == provider,
        )
    )
    if result.rowcount == 0:
        raise NotFoundError("Сервис не подключён", "INTEGRATION_NOT_FOUND")
    await db.commit()
    return ok(await _overview(db, current_user), "Сервис отключён")
