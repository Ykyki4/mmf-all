from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from .base import created_at_field, enum_check, timestamp_field
from .enums import EducationType, EmploymentType, InstitutionType

if TYPE_CHECKING:
    from .users import User


# ── Справочники ───────────────────────────────────────────────────────────────

class EducationalInstitution(SQLModel, table=True):
    __tablename__ = "educational_institutions"
    __table_args__ = (
        UniqueConstraint("name", name="uq_educational_institutions_name"),
        CheckConstraint(
            enum_check("institution_type", InstitutionType), name="ck_educational_institutions_type"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    institution_type: str = Field(max_length=30)
    city: str | None = Field(default=None, max_length=100)


class Company(SQLModel, table=True):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("name", name="uq_companies_name"),
        UniqueConstraint("inn", name="uq_companies_inn"),
        CheckConstraint(r"inn ~ '^[0-9]{10}([0-9]{2})?$'", name="ck_companies_inn_format"),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    inn: str | None = Field(default=None, max_length=12)
    city: str | None = Field(default=None, max_length=100)
    # Контакты компании — никогда не показываются в режиме работодателя
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)


class Technology(SQLModel, table=True):
    __tablename__ = "technologies"
    __table_args__ = (UniqueConstraint("name", name="uq_technologies_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    category: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=500)


# ── Данные профиля ────────────────────────────────────────────────────────────

class EducationRecord(SQLModel, table=True):
    __tablename__ = "education_records"
    __table_args__ = (
        CheckConstraint(enum_check("education_type", EducationType), name="ck_education_records_type"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_education_records_dates"),
        CheckConstraint("sort_order >= 0", name="ck_education_records_sort_order"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    institution_id: int = Field(foreign_key="educational_institutions.id", ondelete="RESTRICT")
    education_type: str = Field(max_length=20)
    specialty: str | None = Field(default=None, max_length=255)
    qualification: str | None = Field(default=None, max_length=100)
    start_date: date
    end_date: date | None = None
    # Результаты обучения: средний балл, оценка, итог курса
    result: str | None = Field(default=None, max_length=100)
    document_number: str | None = Field(default=None, max_length=100)
    document_url: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    created_at: datetime = created_at_field()

    user: Optional["User"] = Relationship(back_populates="education_records")
    institution: EducationalInstitution | None = Relationship()


class WorkExperience(SQLModel, table=True):
    """Периоды работы одного пользователя не пересекаются (триггер + EXCLUDE в миграции)."""

    __tablename__ = "work_experiences"
    __table_args__ = (
        CheckConstraint(
            enum_check("employment_type", EmploymentType), name="ck_work_experiences_employment_type"
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_work_experiences_dates"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    # Для фриланса/самозанятости компания может отсутствовать
    company_id: int | None = Field(default=None, foreign_key="companies.id", ondelete="RESTRICT")
    employment_type: str = Field(max_length=30)
    position: str = Field(max_length=255)
    start_date: date
    end_date: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    created_at: datetime = created_at_field()

    user: Optional["User"] = Relationship(back_populates="work_experiences")
    company: Company | None = Relationship()


class UserSkill(SQLModel, table=True):
    __tablename__ = "user_skills"
    __table_args__ = (
        UniqueConstraint("user_id", "technology_id", name="uq_user_skills_user_technology"),
        CheckConstraint("level BETWEEN 0 AND 10", name="ck_user_skills_level_range"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    technology_id: int = Field(foreign_key="technologies.id", ondelete="RESTRICT", index=True)
    # 0 — знаком с терминологией, 10 — контрибутор технологии
    level: int
    created_at: datetime = created_at_field()
    updated_at: datetime | None = timestamp_field()

    user: Optional["User"] = Relationship(back_populates="skills")
    technology: Technology | None = Relationship()
