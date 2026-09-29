from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from ..models.enums import UserRole
from .common import PersonName, Phone, safe_str
from .rating import RatingRead

ActivityStatus = Literal["online", "recent", "offline"]
ProfileMode = Literal["full", "public", "employer"]


class UserBrief(BaseModel):
    """Текущий пользователь в ответе login и шапке клиентов."""

    id: int
    public_id: str
    email: str
    full_name: str
    last_name: str
    first_name: str
    patronymic: str | None
    role: str
    role_title: str
    avatar_url: str | None


class UserShort(BaseModel):
    """Участник подтверждения / кандидат в списках."""

    id: int
    public_id: str
    full_name: str
    avatar_url: str | None
    is_verified: bool = False


# ── Составные части профиля ───────────────────────────────────────────────────

class InstitutionBrief(BaseModel):
    id: int
    name: str
    institution_type: str
    city: str | None


class CompanyBrief(BaseModel):
    """Только название и город — контакты компании не раскрываются."""

    id: int
    name: str
    city: str | None


class EducationRead(BaseModel):
    id: int
    education_type: str
    education_type_title: str
    institution: InstitutionBrief
    specialty: str | None
    qualification: str | None
    start_date: date
    end_date: date | None
    result: str | None
    document_number: str | None
    document_url: str | None
    sort_order: int
    confirmations_count: int = 0


class ExperienceRead(BaseModel):
    id: int
    employment_type: str
    employment_type_title: str
    company: CompanyBrief | None
    # Для отображения: название компании или тип занятости (для фриланса)
    organization: str
    position: str
    start_date: date
    end_date: date | None
    is_current: bool
    description: str | None
    confirmations_count: int = 0


class SkillRead(BaseModel):
    id: int
    technology_id: int
    technology: str
    category: str | None
    level: int
    confirmations_count: int = 0
    confirmation_dates: list[datetime] = []
    created_at: datetime
    updated_at: datetime | None


class ProfileStats(BaseModel):
    confirmations: int
    skills: int
    recommendations: int


class ProfileSettings(BaseModel):
    is_profile_public: bool
    two_factor_enabled: bool
    employer_company: CompanyBrief | None


class ProfileRead(BaseModel):
    """Профиль пользователя. В режиме employer контакты и ФИО скрыты."""

    id: int
    public_id: str
    mode: ProfileMode
    full_name: str
    last_name: str | None
    first_name: str | None
    patronymic: str | None
    email: str | None
    phone: str | None
    avatar_url: str | None
    city: str | None
    about: str | None
    role: str
    role_title: str
    activity_status: ActivityStatus
    is_verified: bool
    registered_at: datetime
    experience_years: float
    profile_completeness: int
    rating: RatingRead
    stats: ProfileStats
    education: list[EducationRead]
    experience: list[ExperienceRead]
    skills: list[SkillRead]
    settings: ProfileSettings | None = None


# ── Изменение профиля ─────────────────────────────────────────────────────────

class SkillUpsert(BaseModel):
    technology_id: int | None = Field(default=None, gt=0)
    technology_name: safe_str(100) | None = None
    level: int = Field(ge=0, le=10)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    last_name: PersonName | None = None
    first_name: PersonName | None = None
    patronymic: PersonName | None = None
    phone: Phone = None
    city: safe_str(100) | None = None
    about: safe_str(1000, min_length=0) | None = None
    avatar_url: safe_str(500) | None = None
    is_profile_public: bool | None = None
    two_factor_enabled: bool | None = None
    # Если передан — полностью заменяет набор навыков (быстрое заполнение профиля)
    skills: list[SkillUpsert] | None = Field(default=None, max_length=100)


class UserLookupItem(UserShort):
    city: str | None


class RoleUpdate(BaseModel):
    role: UserRole


class ActiveUpdate(BaseModel):
    is_active: bool


class AdminUserRead(BaseModel):
    id: int
    public_id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_seen_at: datetime | None


class RoleHistoryRead(BaseModel):
    id: int
    user_id: int
    old_role: str | None
    new_role: str
    changed_by_id: int | None
    changed_at: datetime
