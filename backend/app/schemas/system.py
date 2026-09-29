from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from ..models.enums import ExportFormat, PrivacyMode
from .common import safe_str

# ── Уведомления ───────────────────────────────────────────────────────────────


class NotificationRead(BaseModel):
    id: int
    notification_type: str
    title: str
    content: str
    is_read: bool
    confirmation_id: int | None
    vacancy_id: int | None
    created_at: datetime


class UnreadCount(BaseModel):
    unread: int
    pending_confirmations: int


# ── Интеграции ────────────────────────────────────────────────────────────────

class IntegrationRead(BaseModel):
    provider: str
    title: str
    status: Literal["connected", "not_connected", "failed"]
    status_title: str
    external_username: str | None
    repositories_count: int | None
    verified_at: datetime | None
    trust_bonus: int


class IntegrationsOverview(BaseModel):
    items: list[IntegrationRead]
    connected_count: int
    message: str | None


class IntegrationConnect(BaseModel):
    username: safe_str(100) | None = None
    # Заглушка внешнего сервиса: random — успех с вероятностью 80%
    simulate: Literal["random", "success", "failure"] = "random"


# ── Экспорт профиля ───────────────────────────────────────────────────────────

class ExportCreate(BaseModel):
    format: ExportFormat = ExportFormat.pdf
    # 7 или 30 дней; null — «бессрочно» (максимальный срок хранения 730 дней)
    ttl_days: Literal[7, 30] | None = 7
    privacy_mode: PrivacyMode = PrivacyMode.full


class ExportRead(BaseModel):
    id: int
    format: str
    privacy_mode: str
    file_name: str
    download_url: str
    created_at: datetime
    expires_at: datetime
    expires_in_seconds: int
    is_expired: bool
