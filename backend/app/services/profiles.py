"""Сборка профиля пользователя в режимах full / public / employer."""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..errors import BadRequestError, ForbiddenError, NotFoundError
from ..models import (
    Confirmation,
    EducationRecord,
    User,
    UserIntegration,
    UserSkill,
    WorkExperience,
)
from ..models.base import utcnow
from ..models.enums import (
    EDUCATION_TYPE_TITLES,
    EMPLOYER_ROLES,
    EMPLOYMENT_TYPE_TITLES,
    ConfirmationStatus,
    IntegrationProvider,
    IntegrationStatus,
)
from ..schemas.users import (
    ActivityStatus,
    CompanyBrief,
    EducationRead,
    ExperienceRead,
    InstitutionBrief,
    ProfileMode,
    ProfileRead,
    ProfileSettings,
    ProfileStats,
    SkillRead,
    UserBrief,
    UserShort,
)
from .matching import get_recommendations
from .rating import build_rating_read

_PUBLIC_ID_PATTERN = re.compile(r"^#?(?:ITP-)?0*(\d+)$", re.IGNORECASE)
ONLINE_WINDOW = timedelta(minutes=15)
RECENT_WINDOW = timedelta(days=7)
DAYS_PER_YEAR = 365.25


# ── Идентификация ─────────────────────────────────────────────────────────────

def parse_user_ref(ref: str, current_user: User) -> int:
    """Принимает 'me', числовой id или публичный ID вида ITP-00012 / #ITP-12."""
    if ref == "me":
        return current_user.id
    match = _PUBLIC_ID_PATTERN.match(ref.strip())
    if not match:
        raise BadRequestError("Некорректный идентификатор пользователя", "INVALID_USER_ID")
    return int(match.group(1))


async def get_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise NotFoundError("Пользователь не найден", "USER_NOT_FOUND")
    return user


def ensure_profile_visible(viewer: User, target: User) -> None:
    """Закрытый профиль видят только владелец, администратор и сотрудники компаний."""
    if viewer.id == target.id or target.is_profile_public:
        return
    if viewer.role not in EMPLOYER_ROLES:
        raise ForbiddenError("Профиль пользователя скрыт", "PROFILE_PRIVATE")


def activity_status(last_seen_at: datetime | None) -> ActivityStatus:
    if last_seen_at is None:
        return "offline"
    elapsed = utcnow() - last_seen_at
    if elapsed <= ONLINE_WINDOW:
        return "online"
    if elapsed <= RECENT_WINDOW:
        return "recent"
    return "offline"


async def verified_user_ids(db: AsyncSession, user_ids: list[int]) -> set[int]:
    """Пользователи, подтвердившие личность через Госуслуги."""
    if not user_ids:
        return set()
    rows = await db.execute(
        select(UserIntegration.user_id).where(
            UserIntegration.user_id.in_(user_ids),
            UserIntegration.provider == IntegrationProvider.gosuslugi,
            UserIntegration.status == IntegrationStatus.connected,
        )
    )
    return set(rows.scalars().all())


def user_brief(user: User) -> UserBrief:
    return UserBrief(
        id=user.id,
        public_id=user.public_id,
        email=user.email,
        full_name=user.full_name,
        last_name=user.last_name,
        first_name=user.first_name,
        patronymic=user.patronymic,
        role=user.role,
        role_title=user.role_title,
        avatar_url=user.avatar_url,
    )


def user_short(user: User, verified: set[int]) -> UserShort:
    return UserShort(
        id=user.id,
        public_id=user.public_id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        is_verified=user.id in verified,
    )


# ── Вспомогательные расчёты ───────────────────────────────────────────────────

def experience_years(experiences: list[WorkExperience]) -> float:
    today = date.today()
    days = sum((min(exp.end_date or today, today) - exp.start_date).days for exp in experiences)
    return round(max(days, 0) / DAYS_PER_YEAR, 1)


def profile_completeness(user: User, has_confirmations: bool) -> int:
    """Заполненность профиля в процентах — каждый пункт даёт равный вклад."""
    checks = [
        bool(user.phone),
        bool(user.avatar_url),
        bool(user.city),
        bool(user.about),
        len(user.education_records) > 0,
        len(user.work_experiences) > 0,
        len(user.skills) >= 3,
        has_confirmations,
    ]
    return round(sum(checks) / len(checks) * 100)


async def _accepted_confirmations(db: AsyncSession, user_id: int) -> list[Confirmation]:
    return list((await db.execute(
        select(Confirmation).where(
            Confirmation.requester_id == user_id,
            Confirmation.status == ConfirmationStatus.accepted,
        )
    )).scalars().all())


async def load_profile_user(db: AsyncSession, user_id: int) -> User:
    user = (await db.execute(
        select(User)
        .where(User.id == user_id, User.is_active == True)
        .options(
            selectinload(User.education_records).selectinload(EducationRecord.institution),
            selectinload(User.work_experiences).selectinload(WorkExperience.company),
            selectinload(User.skills).selectinload(UserSkill.technology),
            selectinload(User.employer_company),
        )
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if user is None:
        raise NotFoundError("Пользователь не найден", "USER_NOT_FOUND")
    return user


# ── Сериализация частей профиля ───────────────────────────────────────────────

def education_read(record: EducationRecord, confirmations: int = 0) -> EducationRead:
    institution = record.institution
    return EducationRead(
        id=record.id,
        education_type=record.education_type,
        education_type_title=EDUCATION_TYPE_TITLES.get(record.education_type, record.education_type),
        institution=InstitutionBrief(
            id=institution.id, name=institution.name,
            institution_type=institution.institution_type, city=institution.city,
        ),
        specialty=record.specialty,
        qualification=record.qualification,
        start_date=record.start_date,
        end_date=record.end_date,
        result=record.result,
        document_number=record.document_number,
        document_url=record.document_url,
        sort_order=record.sort_order,
        confirmations_count=confirmations,
    )


def experience_read(record: WorkExperience, confirmations: int = 0) -> ExperienceRead:
    employment_title = EMPLOYMENT_TYPE_TITLES.get(record.employment_type, record.employment_type)
    company = record.company
    return ExperienceRead(
        id=record.id,
        employment_type=record.employment_type,
        employment_type_title=employment_title,
        company=CompanyBrief(id=company.id, name=company.name, city=company.city) if company else None,
        organization=company.name if company else employment_title,
        position=record.position,
        start_date=record.start_date,
        end_date=record.end_date,
        is_current=record.end_date is None,
        description=record.description,
        confirmations_count=confirmations,
    )


def skill_read(skill: UserSkill, confirmation_dates: list[datetime] | None = None) -> SkillRead:
    dates = sorted(confirmation_dates or [])
    return SkillRead(
        id=skill.id,
        technology_id=skill.technology_id,
        technology=skill.technology.name,
        category=skill.technology.category,
        level=skill.level,
        confirmations_count=len(dates),
        confirmation_dates=dates,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


# ── Профиль целиком ───────────────────────────────────────────────────────────

async def build_profile(db: AsyncSession, user_id: int, mode: ProfileMode) -> ProfileRead:
    user = await load_profile_user(db, user_id)
    confirmations = await _accepted_confirmations(db, user.id)

    skill_dates: dict[int, list[datetime]] = defaultdict(list)
    experience_counts: dict[int, int] = defaultdict(int)
    education_counts: dict[int, int] = defaultdict(int)
    for confirmation in confirmations:
        if confirmation.user_skill_id:
            skill_dates[confirmation.user_skill_id].append(confirmation.responded_at or confirmation.created_at)
        elif confirmation.work_experience_id:
            experience_counts[confirmation.work_experience_id] += 1
        elif confirmation.education_record_id:
            education_counts[confirmation.education_record_id] += 1

    skills = sorted(
        (skill_read(skill, skill_dates.get(skill.id)) for skill in user.skills),
        key=lambda item: (-item.level, -item.confirmations_count, item.technology),
    )
    recommendations = await get_recommendations(db, user.id)
    is_verified = user.id in await verified_user_ids(db, [user.id])

    hide_personal = mode == "employer"
    hide_contacts = mode in ("employer", "public")
    return ProfileRead(
        id=user.id,
        public_id=user.public_id,
        mode=mode,
        full_name=user.anonymized_name if hide_personal else user.full_name,
        last_name=None if hide_personal else user.last_name,
        first_name=None if hide_personal else user.first_name,
        patronymic=None if hide_personal else user.patronymic,
        email=None if hide_contacts else user.email,
        phone=None if hide_contacts else user.phone,
        avatar_url=user.avatar_url,
        city=user.city,
        about=user.about,
        role=user.role,
        role_title=user.role_title,
        activity_status=activity_status(user.last_seen_at),
        is_verified=is_verified,
        registered_at=user.created_at,
        experience_years=experience_years(user.work_experiences),
        profile_completeness=profile_completeness(user, bool(confirmations)),
        rating=await build_rating_read(db, user),
        stats=ProfileStats(
            confirmations=len(confirmations),
            skills=len(user.skills),
            recommendations=len(recommendations.skills) + len(recommendations.vacancies),
        ),
        education=[education_read(rec, education_counts.get(rec.id, 0)) for rec in user.education_records],
        experience=[experience_read(rec, experience_counts.get(rec.id, 0)) for rec in user.work_experiences],
        skills=skills,
        settings=None if mode != "full" else ProfileSettings(
            is_profile_public=user.is_profile_public,
            two_factor_enabled=user.two_factor_enabled,
            employer_company=CompanyBrief(
                id=user.employer_company.id, name=user.employer_company.name, city=user.employer_company.city,
            ) if user.employer_company else None,
        ),
    )

