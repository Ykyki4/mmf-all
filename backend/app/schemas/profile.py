from datetime import date

from pydantic import AliasChoices, BaseModel, Field, model_validator

from ..models.enums import EducationType, EmploymentType
from .common import safe_str


def _check_period(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ValueError("Дата окончания не может быть раньше даты начала")
    if start and start > date.today():
        raise ValueError("Дата начала не может быть в будущем")


# ── Образование ───────────────────────────────────────────────────────────────

class EducationCreate(BaseModel):
    education_type: EducationType
    # Учебное заведение: id из справочника или название (будет добавлено в справочник)
    institution_id: int | None = Field(default=None, gt=0)
    institution_name: safe_str(255) | None = None
    specialty: safe_str(255) | None = None
    qualification: safe_str(100) | None = None
    start_date: date
    end_date: date | None = None
    result: safe_str(100) | None = None
    document_number: safe_str(100) | None = None
    document_url: safe_str(500) | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> EducationCreate:
        if self.institution_id is None and not self.institution_name:
            raise ValueError("Укажите institution_id или institution_name")
        _check_period(self.start_date, self.end_date)
        return self


class EducationUpdate(BaseModel):
    education_type: EducationType | None = None
    institution_id: int | None = Field(default=None, gt=0)
    institution_name: safe_str(255) | None = None
    specialty: safe_str(255) | None = None
    qualification: safe_str(100) | None = None
    start_date: date | None = None
    end_date: date | None = None
    result: safe_str(100) | None = None
    document_number: safe_str(100) | None = None
    document_url: safe_str(500) | None = None

    @model_validator(mode="after")
    def validate_period(self) -> EducationUpdate:
        _check_period(self.start_date, self.end_date)
        return self


class EducationReorder(BaseModel):
    """Новый порядок записей (drag-to-reorder): id в нужной последовательности."""

    ids: list[int] = Field(min_length=1, max_length=100)


# ── Опыт работы ───────────────────────────────────────────────────────────────

class ExperienceCreate(BaseModel):
    employment_type: EmploymentType
    company_id: int | None = Field(default=None, gt=0)
    company_name: safe_str(255) | None = None
    position: safe_str(255)
    start_date: date
    end_date: date | None = None
    description: safe_str(2000) | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> ExperienceCreate:
        needs_company = self.employment_type in (
            EmploymentType.labor_contract, EmploymentType.llc_participant, EmploymentType.civil_contract,
        )
        if needs_company and self.company_id is None and not self.company_name:
            raise ValueError("Для этого типа занятости укажите компанию")
        _check_period(self.start_date, self.end_date)
        return self


class ExperienceUpdate(BaseModel):
    employment_type: EmploymentType | None = None
    company_id: int | None = Field(default=None, gt=0)
    company_name: safe_str(255) | None = None
    position: safe_str(255) | None = None
    start_date: date | None = None
    end_date: date | None = None
    description: safe_str(2000) | None = None

    @model_validator(mode="after")
    def validate_period(self) -> ExperienceUpdate:
        _check_period(self.start_date, self.end_date)
        return self


# ── Навыки ────────────────────────────────────────────────────────────────────

class SkillCreate(BaseModel):
    # Технология: id из справочника (skill_id) или название
    technology_id: int | None = Field(
        default=None, gt=0, validation_alias=AliasChoices("technology_id", "skill_id")
    )
    technology_name: safe_str(100) | None = None
    level: int = Field(ge=0, le=10, description="0 — знаком с терминологией, 10 — контрибутор")

    @model_validator(mode="after")
    def validate_technology(self) -> SkillCreate:
        if self.technology_id is None and not self.technology_name:
            raise ValueError("Укажите technology_id (skill_id) или technology_name")
        return self


class SkillUpdate(BaseModel):
    level: int = Field(ge=0, le=10)


class TechnologyRead(BaseModel):
    id: int
    name: str
    category: str | None
    description: str | None
    specialists_count: int = 0


class TopSkill(BaseModel):
    name: str
    specialists_count: int
    percent: float
