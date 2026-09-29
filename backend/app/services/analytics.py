"""Метрики главной страницы HR и аналитика рынка навыков."""

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import Date, bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Confirmation, Rating, RatingHistory, Technology, User, UserSkill
from ..models.base import utcnow
from ..models.enums import ConfirmationStatus, UserRole
from ..schemas.hr import (
    DashboardSummary,
    DistributionItem,
    RatingTrendResponse,
    SkillsAnalytics,
    SummaryMetric,
    TechnologyStat,
)
from ..schemas.profile import TopSkill
from ..schemas.rating import TrendPoint
from .matching import best_match, load_vacancies
from .rating import trend_of

TOP_TECHNOLOGIES_LIMIT = 10
# Рейтинг на дашборде и в аналитике показывается по шкале 0–10 (индекс компетенций / 10)
RATING_SCALE_DIVISOR = 10

_SPECIALIST = (User.role == UserRole.user, User.is_active == True)


def _percent(part: int | float, total: int | float) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _day_start(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


# ── Топ технологий ────────────────────────────────────────────────────────────

async def count_specialists(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(User.id)).where(*_SPECIALIST))).scalar_one()


async def top_skills(db: AsyncSession, limit: int) -> list[TopSkill]:
    """Доля специалистов, владеющих технологией (от всех активных специалистов)."""
    total = await count_specialists(db)
    specialists = func.count(func.distinct(UserSkill.user_id))
    rows = (await db.execute(
        select(Technology.name, specialists)
        .join(UserSkill, UserSkill.technology_id == Technology.id)
        .join(User, User.id == UserSkill.user_id)
        .where(*_SPECIALIST)
        .group_by(Technology.name)
        .order_by(specialists.desc(), Technology.name)
        .limit(limit)
    )).all()
    return [TopSkill(name=name, specialists_count=count, percent=_percent(count, total)) for name, count in rows]


# ── Главная страница HR ───────────────────────────────────────────────────────

async def dashboard_summary(db: AsyncSession) -> DashboardSummary:
    now = utcnow()
    day_ago = now - timedelta(days=1)
    today = _day_start(now)

    active_profiles = await count_specialists(db)
    new_profiles = (await db.execute(
        select(func.count(User.id)).where(*_SPECIALIST, User.created_at >= day_ago)
    )).scalar_one()

    job_match_now, job_match_before = await _average_job_match(db, day_ago)

    avg_now = (await db.execute(
        select(func.avg(Rating.competency_index)).join(User, User.id == Rating.user_id).where(*_SPECIALIST)
    )).scalar_one() or 0.0
    avg_before = await _average_rating_at(db, day_ago)

    accepted_today, accepted_yesterday = (await db.execute(
        select(
            func.count(Confirmation.id).filter(Confirmation.responded_at >= today),
            func.count(Confirmation.id).filter(
                Confirmation.responded_at >= today - timedelta(days=1), Confirmation.responded_at < today,
            ),
        ).where(Confirmation.status == ConfirmationStatus.accepted)
    )).one()

    avg_now_scaled = round(float(avg_now) / RATING_SCALE_DIVISOR, 1)
    avg_before_scaled = round(float(avg_before) / RATING_SCALE_DIVISOR, 1) if avg_before is not None else avg_now_scaled
    return DashboardSummary(
        active_profiles=SummaryMetric(value=active_profiles, delta=new_profiles),
        job_match=SummaryMetric(value=job_match_now, delta=round(job_match_now - job_match_before, 1)),
        avg_rating=SummaryMetric(value=avg_now_scaled, delta=round(avg_now_scaled - avg_before_scaled, 1)),
        confirmations_today=SummaryMetric(value=accepted_today, delta=accepted_today - accepted_yesterday),
        updated_at=now,
    )


async def _average_job_match(db: AsyncSession, before: datetime) -> tuple[float, float]:
    """Среднее по специалистам лучшее совпадение с вакансией — сейчас и по навыкам на момент `before`."""
    vacancies = await load_vacancies(db)
    rows = (await db.execute(
        select(UserSkill.user_id, UserSkill.technology_id, UserSkill.level, UserSkill.created_at)
        .join(User, User.id == UserSkill.user_id)
        .where(*_SPECIALIST)
    )).all()
    levels_now: dict[int, dict[int, int]] = defaultdict(dict)
    levels_before: dict[int, dict[int, int]] = defaultdict(dict)
    for user_id, technology_id, level, created_at in rows:
        levels_now[user_id][technology_id] = level
        if created_at < before:
            levels_before[user_id][technology_id] = level

    total = await count_specialists(db)
    if not total or not vacancies:
        return 0.0, 0.0
    now_sum = sum(best_match(levels, vacancies) for levels in levels_now.values())
    before_sum = sum(best_match(levels, vacancies) for levels in levels_before.values())
    return round(now_sum / total, 1), round(before_sum / total, 1)


async def _average_rating_at(db: AsyncSession, moment: datetime) -> float | None:
    """Средний индекс компетенций по последним значениям истории на момент `moment`."""
    latest = (
        select(RatingHistory.user_id, RatingHistory.competency_index)
        .join(User, User.id == RatingHistory.user_id)
        .where(*_SPECIALIST, RatingHistory.recorded_at <= moment)
        .order_by(RatingHistory.user_id, RatingHistory.recorded_at.desc())
        .distinct(RatingHistory.user_id)
        .subquery()
    )
    return (await db.execute(select(func.avg(latest.c.competency_index)))).scalar_one()


# ── Аналитика рынка ───────────────────────────────────────────────────────────

async def skills_analytics(db: AsyncSession, period_days: int, distribution_top: int) -> SkillsAnalytics:
    """Навыки, добавленные или обновлённые специалистами за период."""
    since = utcnow() - timedelta(days=period_days)
    activity = func.coalesce(UserSkill.updated_at, UserSkill.created_at)
    specialists = func.count(func.distinct(UserSkill.user_id))
    rows = (await db.execute(
        select(Technology.name, specialists, func.avg(UserSkill.level))
        .join(UserSkill, UserSkill.technology_id == Technology.id)
        .join(User, User.id == UserSkill.user_id)
        .where(*_SPECIALIST, activity >= since)
        .group_by(Technology.name)
        .order_by(specialists.desc(), Technology.name)
    )).all()
    total_specialists = (await db.execute(
        select(func.count(func.distinct(UserSkill.user_id)))
        .join(User, User.id == UserSkill.user_id)
        .where(*_SPECIALIST, activity >= since)
    )).scalar_one()

    # Круговая диаграмма: доли технологий среди всех навыков периода, хвост — «Другое»
    total_entries = sum(count for _, count, _ in rows)
    distribution = [
        DistributionItem(name=name, specialists_count=count, percent=_percent(count, total_entries))
        for name, count, _ in rows[:distribution_top]
    ]
    other = sum(count for _, count, _ in rows[distribution_top:])
    if other:
        distribution.append(DistributionItem(
            name="Другое", specialists_count=other,
            percent=round(100 - sum(item.percent for item in distribution), 1),
        ))

    top = [
        TechnologyStat(name=name, avg_level=round(float(avg_level), 1), specialists_count=count)
        for name, count, avg_level in rows[:TOP_TECHNOLOGIES_LIMIT]
    ]
    return SkillsAnalytics(
        period_days=period_days,
        total_specialists=total_specialists,
        distribution=distribution,
        top_technologies=top,
    )


_RATING_TREND_SQL = text("""
    SELECT d::date AS day, AVG(h.competency_index) AS value
    FROM generate_series(CAST(:start AS date), CAST(:finish AS date), interval '1 day') AS d
    JOIN LATERAL (
        SELECT DISTINCT ON (rh.user_id) rh.competency_index
        FROM rating_history rh
        JOIN users u ON u.id = rh.user_id AND u.role = 'user' AND u.is_active
        WHERE rh.recorded_at < d + interval '1 day'
        ORDER BY rh.user_id, rh.recorded_at DESC
    ) h ON true
    GROUP BY d
    ORDER BY d
""").bindparams(bindparam("start", type_=Date), bindparam("finish", type_=Date))


async def rating_trend(db: AsyncSession, period_days: int) -> RatingTrendResponse:
    """Средний рейтинг специалистов (шкала 0–10) по дням: на каждый день берётся
    последнее известное значение индекса каждого специалиста."""
    finish = utcnow().date()
    start = finish - timedelta(days=period_days - 1)
    rows = (await db.execute(_RATING_TREND_SQL, {"start": start, "finish": finish})).all()
    points = [
        TrendPoint(date=day, value=round(float(value) / RATING_SCALE_DIVISOR, 2))
        for day, value in rows
    ]
    change = round(points[-1].value - points[0].value, 2) if len(points) > 1 else 0.0
    return RatingTrendResponse(
        period_days=period_days,
        points=points,
        current=points[-1].value if points else None,
        change=change,
        # Шкала 0–10: порог тренда в 10 раз меньше, чем для процентов
        trend=trend_of(change * RATING_SCALE_DIVISOR),
    )
