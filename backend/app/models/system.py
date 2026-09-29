from datetime import datetime

from sqlalchemy import CheckConstraint, Index, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel

from .base import created_at_field, enum_check, timestamp_field
from .enums import ExportFormat, IntegrationProvider, IntegrationStatus, NotificationType, PrivacyMode

# ── Уведомления ───────────────────────────────────────────────────────────────

class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(enum_check("notification_type", NotificationType), name="ck_notifications_type"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE")
    notification_type: str = Field(max_length=30)
    title: str = Field(max_length=255)
    content: str = Field(max_length=1000)
    is_read: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})
    # Ссылки для tap-to-action: запрос подтверждения или вакансия
    confirmation_id: int | None = Field(default=None, foreign_key="confirmations.id", ondelete="SET NULL")
    vacancy_id: int | None = Field(default=None, foreign_key="vacancies.id", ondelete="SET NULL")
    created_at: datetime = created_at_field()


# ── Внешние интеграции (заглушки Госуслуг, GitHub, Telegram) ─────────────────

class UserIntegration(SQLModel, table=True):
    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_integrations_user_provider"),
        CheckConstraint(enum_check("provider", IntegrationProvider), name="ck_user_integrations_provider"),
        CheckConstraint(enum_check("status", IntegrationStatus), name="ck_user_integrations_status"),
        CheckConstraint(
            "repositories_count IS NULL OR repositories_count >= 0",
            name="ck_user_integrations_repositories_count",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    provider: str = Field(max_length=20)
    status: str = Field(max_length=20)
    external_username: str | None = Field(default=None, max_length=100)
    repositories_count: int | None = None
    verified_at: datetime | None = timestamp_field()
    created_at: datetime = created_at_field()


# ── Экспорт профиля (файлы по временной ссылке) ──────────────────────────────

class ProfileExport(SQLModel, table=True):
    __tablename__ = "profile_exports"
    __table_args__ = (
        UniqueConstraint("token", name="uq_profile_exports_token"),
        CheckConstraint(enum_check("export_format", ExportFormat), name="ck_profile_exports_format"),
        CheckConstraint(enum_check("privacy_mode", PrivacyMode), name="ck_profile_exports_privacy_mode"),
        CheckConstraint("expires_at > created_at", name="ck_profile_exports_expiration"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    token: str = Field(max_length=64)
    export_format: str = Field(max_length=10)
    privacy_mode: str = Field(max_length=20)
    file_name: str = Field(max_length=100)
    content: bytes = Field(sa_type=LargeBinary)
    created_at: datetime = created_at_field()
    expires_at: datetime = timestamp_field(nullable=False)


# ── Журнал запросов к API ─────────────────────────────────────────────────────

class ApiRequestLog(SQLModel, table=True):
    """Логирование всех операций API (без тел запросов и токенов)."""

    __tablename__ = "api_request_logs"

    id: int | None = Field(default=None, primary_key=True)
    method: str = Field(max_length=10)
    path: str = Field(max_length=500)
    status_code: int
    duration_ms: int
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL", index=True)
    client_ip: str | None = Field(default=None, max_length=45)
    created_at: datetime = created_at_field()
