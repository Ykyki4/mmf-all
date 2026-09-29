"""Поиск кандидатов для HR (desktop, экран «Поиск профилей»)."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Integer, and_, exists, func, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Confirmation, Rating, Technology, User, UserSkill, WorkExperience
from ..models.enums import ConfirmationStatus, UserRole
from ..responses import Page, make_page
from ..schemas.hr import CandidateSearchItem, CandidateSkill
from .profiles import DAYS_PER_YEAR, activity_status
from .rating import rating_changes, trend_of

SortField = Literal["id", "rating", "trust", "experience", "confirmations"]
MAX_SKILLS_IN_ROW = 6


@dataclass
class SearchFilters:
    skills: list[str] = field(default_factory=list)
    min_level: int | None = None
    max_level: int | None = None
    min_experience: float | None = None
    max_experience: float | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    min_trust: float | None = None
    employment_type: str | None = None
    candidate_id: int | None = None


def experience_years_subquery():
    """Суммарный стаж пользователя в годах (периоды не пересекаются — достаточно суммы)."""
    worked_days = type_coerce(
        func.least(func.coalesce(WorkExperience.end_date, func.current_date()), func.current_date())
        - WorkExperience.start_date,
        Integer,
    )
    return (
        select(
            WorkExperience.user_id.label("user_id"),
            (func.sum(worked_days) / DAYS_PER_YEAR).label("years"),
        )
        .group_by(WorkExperience.user_id)
        .subquery()
    )


def _skill_condition(filters: SearchFilters, technology_name: str | None):
    conditions = [UserSkill.user_id == User.id]
    if technology_name is not None:
        conditions.append(func.lower(Technology.name) == technology_name.lower())
    if filters.min_level is not None:
        conditions.append(UserSkill.level >= filters.min_level)
    if filters.max_level is not None:
        conditions.append(UserSkill.level <= filters.max_level)
    return exists(
        select(UserSkill.id).join(Technology, Technology.id == UserSkill.technology_id).where(and_(*conditions))
    )


async def search_candidates(
    db: AsyncSession,
    filters: SearchFilters,
    sort_by: SortField,
    descending: bool,
    page: int,
    page_size: int,
) -> Page[CandidateSearchItem]:
    exp = experience_years_subquery()
    years = func.coalesce(exp.c.years, 0)

    conditions = [User.role == UserRole.user, User.is_active == True, User.is_profile_public == True]
    # Каждая выбранная технология должна быть у кандидата с уровнем в заданном диапазоне
    for name in filters.skills:
        conditions.append(_skill_condition(filters, name))
    if not filters.skills and (filters.min_level is not None or filters.max_level is not None):
        conditions.append(_skill_condition(filters, None))
    if filters.min_experience is not None:
        conditions.append(years >= filters.min_experience)
    if filters.max_experience is not None:
        conditions.append(years <= filters.max_experience)
    if filters.min_rating is not None:
        conditions.append(Rating.competency_index >= filters.min_rating)
    if filters.max_rating is not None:
        conditions.append(Rating.competency_index <= filters.max_rating)
    if filters.min_trust is not None:
        conditions.append(Rating.trust_level >= filters.min_trust)
    if filters.employment_type:
        conditions.append(exists(select(WorkExperience.id).where(
            WorkExperience.user_id == User.id, WorkExperience.employment_type == filters.employment_type,
        )))
    if filters.candidate_id is not None:
        conditions.append(User.id == filters.candidate_id)

    base = (
        select(
            User.id,
            User.last_seen_at,
            Rating.competency_index,
            Rating.trust_level,
            Rating.confirmations_count,
            years.label("experience_years"),
        )
        .join(Rating, Rating.user_id == User.id)
        .outerjoin(exp, exp.c.user_id == User.id)
        .where(*conditions)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    sort_columns = {
        "id": User.id,
        "rating": Rating.competency_index,
        "trust": Rating.trust_level,
        "experience": years,
        "confirmations": Rating.confirmations_count,
    }
    sort_column = sort_columns[sort_by]
    order = sort_column.desc() if descending else sort_column.asc()
    rows = (await db.execute(
        base.order_by(order, User.id).offset((page - 1) * page_size).limit(page_size)
    )).all()

    user_ids = [row.id for row in rows]
    skills = await _skills_for_users(db, user_ids, filters.skills)
    changes = await rating_changes(db, user_ids)

    items = [
        CandidateSearchItem(
            id=row.id,
            public_id=f"ITP-{row.id:05d}",
            skills=skills.get(row.id, []),
            competency_index=row.competency_index,
            trust_level=row.trust_level,
            confirmations_count=row.confirmations_count,
            experience_years=round(float(row.experience_years), 1),
            trend=trend_of(changes.get(row.id, 0.0)),
            activity_status=activity_status(row.last_seen_at),
        )
        for row in rows
    ]
    return make_page(items, total, page, page_size)


async def _skills_for_users(
    db: AsyncSession, user_ids: list[int], preferred: list[str]
) -> dict[int, list[CandidateSkill]]:
    """Навыки кандидатов с числом принятых подтверждений; искомые технологии — первыми."""
    if not user_ids:
        return {}
    confirmations = (
        select(Confirmation.user_skill_id, func.count(Confirmation.id).label("cnt"))
        .where(Confirmation.status == ConfirmationStatus.accepted, Confirmation.user_skill_id.is_not(None))
        .group_by(Confirmation.user_skill_id)
        .subquery()
    )
    rows = (await db.execute(
        select(UserSkill.user_id, Technology.name, UserSkill.level, func.coalesce(confirmations.c.cnt, 0))
        .join(Technology, Technology.id == UserSkill.technology_id)
        .outerjoin(confirmations, confirmations.c.user_skill_id == UserSkill.id)
        .where(UserSkill.user_id.in_(user_ids))
        .order_by(UserSkill.user_id, UserSkill.level.desc(), Technology.name)
    )).all()

    preferred_lower = {name.lower() for name in preferred}
    result: dict[int, list[CandidateSkill]] = defaultdict(list)
    for user_id, name, level, count in rows:
        result[user_id].append(CandidateSkill(name=name, level=level, confirmations=count))
    for user_id, user_skills in result.items():
        user_skills.sort(key=lambda skill: (skill.name.lower() not in preferred_lower, -skill.level))
        result[user_id] = user_skills[:MAX_SKILLS_IN_ROW]
    return result
