from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from .base import created_at_field, timestamp_field
from .profile import Company, Technology

# ── Подборки кандидатов (short-list) ──────────────────────────────────────────

class Shortlist(SQLModel, table=True):
    __tablename__ = "shortlists"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_shortlists_owner_name"),)

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    vacancy_id: int | None = Field(default=None, foreign_key="vacancies.id", ondelete="SET NULL")
    name: str = Field(max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    created_at: datetime = created_at_field()
    updated_at: datetime | None = timestamp_field()

    candidates: list["ShortlistCandidate"] = Relationship(
        back_populates="shortlist",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "passive_deletes": True},
    )


class ShortlistCandidate(SQLModel, table=True):
    __tablename__ = "shortlist_candidates"
    __table_args__ = (
        UniqueConstraint("shortlist_id", "candidate_id", name="uq_shortlist_candidates_shortlist_candidate"),
    )

    id: int | None = Field(default=None, primary_key=True)
    shortlist_id: int = Field(foreign_key="shortlists.id", ondelete="CASCADE", index=True)
    candidate_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    note: str | None = Field(default=None, max_length=500)
    added_at: datetime = created_at_field()

    shortlist: Shortlist | None = Relationship(back_populates="candidates")


# ── Вакансии (mock-данные для рекомендаций и совпадений) ─────────────────────

class Vacancy(SQLModel, table=True):
    __tablename__ = "vacancies"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", ondelete="CASCADE", index=True)
    title: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = Field(default=True, sa_column_kwargs={"server_default": "true"})
    created_at: datetime = created_at_field()

    company: Company | None = Relationship()
    required_skills: list["VacancySkill"] = Relationship(
        back_populates="vacancy", sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class VacancySkill(SQLModel, table=True):
    __tablename__ = "vacancy_skills"
    __table_args__ = (
        CheckConstraint("required_level BETWEEN 0 AND 10", name="ck_vacancy_skills_level_range"),
    )

    vacancy_id: int = Field(primary_key=True, foreign_key="vacancies.id", ondelete="CASCADE")
    technology_id: int = Field(primary_key=True, foreign_key="technologies.id", ondelete="RESTRICT")
    required_level: int

    vacancy: Vacancy | None = Relationship(back_populates="required_skills")
    technology: Technology | None = Relationship()
