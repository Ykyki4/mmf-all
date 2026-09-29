from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from .base import created_at_field, enum_check, timestamp_field
from .enums import AuthCodePurpose, UserRole

if TYPE_CHECKING:
    from .profile import Company, EducationRecord, UserSkill, WorkExperience


# ── Роли ──────────────────────────────────────────────────────────────────────

class Role(SQLModel, table=True):
    """Справочник ролей. users.role ссылается на roles.code."""

    __tablename__ = "roles"

    code: str = Field(primary_key=True, max_length=20)
    title: str = Field(max_length=100)
    is_employer: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})


# ── Пользователь ──────────────────────────────────────────────────────────────

class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("phone", name="uq_users_phone"),
        CheckConstraint(r"email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'", name="ck_users_email_format"),
        CheckConstraint(r"phone ~ '^\+?[0-9]{10,15}$'", name="ck_users_phone_format"),
    )

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=255)
    phone: str | None = Field(default=None, max_length=16)
    last_name: str = Field(max_length=100)
    first_name: str = Field(max_length=100)
    patronymic: str | None = Field(default=None, max_length=100)
    role: str = Field(
        default=UserRole.user, max_length=20, foreign_key="roles.code", index=True,
        sa_column_kwargs={"server_default": UserRole.user.value},
    )
    hashed_password: str = Field(max_length=255)
    avatar_url: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=100)
    about: str | None = Field(default=None, max_length=1000)
    # Компания-работодатель для сотрудников (HR/руководитель/аналитик)
    employer_company_id: int | None = Field(default=None, foreign_key="companies.id", ondelete="SET NULL")
    is_active: bool = Field(default=True, sa_column_kwargs={"server_default": "true"})
    is_profile_public: bool = Field(default=True, sa_column_kwargs={"server_default": "true"})
    two_factor_enabled: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})
    created_at: datetime = created_at_field()
    updated_at: datetime | None = timestamp_field()
    last_seen_at: datetime | None = timestamp_field()

    role_info: Role | None = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    employer_company: Optional["Company"] = Relationship()
    education_records: list["EducationRecord"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"order_by": "EducationRecord.sort_order", "cascade": "all, delete-orphan"},
    )
    work_experiences: list["WorkExperience"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"order_by": "WorkExperience.start_date.desc()", "cascade": "all, delete-orphan"},
    )
    skills: list["UserSkill"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.last_name, self.first_name, self.patronymic) if part)

    @property
    def role_title(self) -> str:
        return self.role_info.title if self.role_info else self.role

    @property
    def public_id(self) -> str:
        """Публичный идентификатор профиля, например ITP-00012 (вычисляется из id)."""
        return f"ITP-{self.id:05d}"

    @property
    def anonymized_name(self) -> str:
        """«Фамилия И.О. (анонимный профиль)» — для режима работодателя."""
        initials = "".join(f"{part[0]}." for part in (self.first_name, self.patronymic) if part)
        return f"{self.last_name} {initials} (анонимный профиль)"


# ── Журнал смены ролей (заполняется триггером) ───────────────────────────────

class RoleHistory(SQLModel, table=True):
    __tablename__ = "role_history"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    old_role: str | None = Field(default=None, max_length=20, foreign_key="roles.code")
    new_role: str = Field(max_length=20, foreign_key="roles.code")
    changed_by_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    changed_at: datetime = created_at_field()


# ── Сессии (refresh-токены) ───────────────────────────────────────────────────

class RefreshToken(SQLModel, table=True):
    """Сессия пользователя. id сессии (sid) зашит в access-токен,
    поэтому отзыв сессии сразу инвалидирует и access-токен."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    # SHA-256 от сырого токена, который получает клиент
    token_hash: str = Field(max_length=64)
    user_agent: str | None = Field(default=None, max_length=255)
    created_at: datetime = created_at_field()
    expires_at: datetime = timestamp_field(nullable=False)
    revoked_at: datetime | None = timestamp_field()


# ── Одноразовые коды (2FA, восстановление пароля) ─────────────────────────────

class AuthCode(SQLModel, table=True):
    __tablename__ = "auth_codes"
    __table_args__ = (
        CheckConstraint(enum_check("purpose", AuthCodePurpose), name="ck_auth_codes_purpose"),
        CheckConstraint("attempts >= 0", name="ck_auth_codes_attempts"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    purpose: str = Field(max_length=20)
    code_hash: str = Field(max_length=64)
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    created_at: datetime = created_at_field()
    expires_at: datetime = timestamp_field(nullable=False)
    used_at: datetime | None = timestamp_field()
