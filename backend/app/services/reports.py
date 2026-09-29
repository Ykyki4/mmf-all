"""Данные отчётов: по кандидату, по подборке и выгрузка собственного профиля."""

from collections import Counter
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Confirmation, Rating, RatingHistory, Shortlist, ShortlistCandidate, Technology, UserSkill
from ..models.base import utcnow
from ..models.enums import ConfirmationStatus
from ..schemas.hr import ChartSeries, ReportChart, ReportData, ReportSummaryItem, ReportTable
from ..schemas.users import ProfileRead
from .matching import load_vacancies, match_percent, user_skill_levels
from .profiles import build_profile
from .rating import rating_changes

ReportPeriod = Literal["month", "quarter", "year"]
PERIOD_DAYS = {"month": 30, "quarter": 90, "year": 365}
PERIOD_TITLES = {"month": "месяц", "quarter": "квартал", "year": "год"}
PrivacyMode = Literal["full", "anonymous"]


def _period_bounds(period: ReportPeriod) -> tuple[date, date]:
    end = utcnow().date()
    return end - timedelta(days=PERIOD_DAYS[period]), end


def _signed(value: float) -> str:
    return f"{value:+.2f}"


def _years_range(start: date, end: date | None) -> str:
    return f"{start:%m.%Y} — {end:%m.%Y}" if end else f"{start:%m.%Y} — наст. время"


def _skills_table(profile: ProfileRead) -> ReportTable:
    return ReportTable(
        title="Подтверждённые навыки",
        columns=["Технология", "Уровень", "Подтверждений"],
        rows=[[skill.technology, f"{skill.level}/10", skill.confirmations_count] for skill in profile.skills],
    )


def _experience_table(profile: ProfileRead) -> ReportTable:
    return ReportTable(
        title="Опыт работы",
        columns=["Период", "Организация", "Тип занятости", "Должность"],
        rows=[
            [_years_range(exp.start_date, exp.end_date), exp.organization, exp.employment_type_title, exp.position]
            for exp in profile.experience
        ],
    )


async def _rating_chart(db: AsyncSession, user_id: int, since: date, title: str) -> ReportChart:
    records = (await db.execute(
        select(RatingHistory)
        .where(RatingHistory.user_id == user_id, RatingHistory.recorded_at >= since)
        .order_by(RatingHistory.recorded_at)
    )).scalars().all()
    if not records:
        rating = await db.get(Rating, user_id)
        labels = [utcnow().strftime("%d.%m.%Y")]
        competency = [rating.competency_index if rating else 0.0]
        trust = [rating.trust_level if rating else 0.0]
    else:
        labels = [record.recorded_at.strftime("%d.%m.%Y") for record in records]
        competency = [record.competency_index for record in records]
        trust = [record.trust_level for record in records]
    return ReportChart(
        chart_type="line",
        title=title,
        labels=labels,
        series=[ChartSeries(name="Индекс компетенций", values=competency),
                ChartSeries(name="Доверие сообщества", values=trust)],
    )


# ── Отчёт по кандидату ────────────────────────────────────────────────────────

async def candidate_report(db: AsyncSession, candidate_id: int, period: ReportPeriod) -> ReportData:
    start, end = _period_bounds(period)
    profile = await build_profile(db, candidate_id, "employer")
    change = (await rating_changes(db, [candidate_id], PERIOD_DAYS[period])).get(candidate_id, 0.0)
    confirmations_in_period = len((await db.execute(
        select(Confirmation.id).where(
            Confirmation.requester_id == candidate_id,
            Confirmation.status == ConfirmationStatus.accepted,
            Confirmation.responded_at >= start,
        )
    )).all())

    vacancies = await load_vacancies(db)
    levels = await user_skill_levels(db, candidate_id)
    matches = sorted(
        ((vacancy, match_percent(levels, vacancy)) for vacancy in vacancies), key=lambda item: -item[1],
    )[:5]

    summary = [
        ReportSummaryItem(label="Кандидат", value=f"#{profile.public_id} {profile.full_name}"),
        ReportSummaryItem(label="Индекс компетенций", value=f"{profile.rating.competency_index:.2f}%"),
        ReportSummaryItem(label="Доверие сообщества", value=f"{profile.rating.trust_level:.2f}%"),
        ReportSummaryItem(label="Изменение индекса за период", value=_signed(change)),
        ReportSummaryItem(label="Подтверждений всего / за период",
                          value=f"{profile.stats.confirmations} / {confirmations_in_period}"),
        ReportSummaryItem(label="Навыков", value=str(profile.stats.skills)),
        ReportSummaryItem(label="Стаж, лет", value=f"{profile.experience_years:.1f}"),
        ReportSummaryItem(label="Лучшее совпадение с вакансией",
                          value=f"{matches[0][1]}% ({matches[0][0].title})" if matches else "—"),
    ]
    return ReportData(
        report_type="candidate",
        title=f"Отчёт по кандидату #{profile.public_id}",
        period=PERIOD_TITLES[period],
        period_start=start,
        period_end=end,
        generated_at=utcnow(),
        summary=summary,
        tables=[
            _skills_table(profile),
            _experience_table(profile),
            ReportTable(
                title="Соответствие вакансиям",
                columns=["Вакансия", "Компания", "Совпадение, %"],
                rows=[[vacancy.title, vacancy.company, percent] for vacancy, percent in matches],
            ),
        ],
        chart=await _rating_chart(db, candidate_id, start, "Динамика рейтинга за период"),
    )


# ── Отчёт по подборке ─────────────────────────────────────────────────────────

async def shortlist_report(db: AsyncSession, shortlist: Shortlist, period: ReportPeriod) -> ReportData:
    start, end = _period_bounds(period)
    candidates = (await db.execute(
        select(ShortlistCandidate, Rating)
        .join(Rating, Rating.user_id == ShortlistCandidate.candidate_id)
        .where(ShortlistCandidate.shortlist_id == shortlist.id)
        .order_by(Rating.competency_index.desc())
    )).all()
    candidate_ids = [item.candidate_id for item, _ in candidates]
    changes = await rating_changes(db, candidate_ids, PERIOD_DAYS[period])

    skills_rows = (await db.execute(
        select(UserSkill.user_id, Technology.name, UserSkill.level)
        .join(Technology, Technology.id == UserSkill.technology_id)
        .where(UserSkill.user_id.in_(candidate_ids))
        .order_by(UserSkill.level.desc())
    )).all() if candidate_ids else []
    top_skills: dict[int, list[str]] = {}
    technology_counter: Counter[str] = Counter()
    for user_id, name, level in skills_rows:
        top_skills.setdefault(user_id, [])
        if len(top_skills[user_id]) < 3:
            top_skills[user_id].append(f"{name} {level}/10")
        technology_counter[name] += 1

    vacancy = None
    if shortlist.vacancy_id:
        vacancy = next((v for v in await load_vacancies(db) if v.vacancy_id == shortlist.vacancy_id), None)

    rows = []
    for index, (item, rating) in enumerate(candidates, start=1):
        row = [
            index, f"ITP-{item.candidate_id:05d}", f"{rating.competency_index:.2f}", f"{rating.trust_level:.2f}",
            _signed(changes.get(item.candidate_id, 0.0)), ", ".join(top_skills.get(item.candidate_id, [])),
        ]
        if vacancy:
            row.append(match_percent(await user_skill_levels(db, item.candidate_id), vacancy))
        rows.append(row)

    count = len(candidates)
    avg_competency = sum(rating.competency_index for _, rating in candidates) / count if count else 0.0
    avg_trust = sum(rating.trust_level for _, rating in candidates) / count if count else 0.0
    summary = [
        ReportSummaryItem(label="Подборка", value=shortlist.name),
        ReportSummaryItem(label="Кандидатов", value=str(count)),
        ReportSummaryItem(label="Создана", value=f"{shortlist.created_at:%d.%m.%Y}"),
        ReportSummaryItem(label="Средний индекс компетенций", value=f"{avg_competency:.2f}%"),
        ReportSummaryItem(label="Среднее доверие сообщества", value=f"{avg_trust:.2f}%"),
    ]
    columns = ["№", "Кандидат", "Индекс, %", "Доверие, %", "Изменение за период", "Топ навыки"]
    if vacancy:
        summary.append(ReportSummaryItem(label="Вакансия", value=f"{vacancy.title} ({vacancy.company})"))
        columns.append("Совпадение, %")

    most_common = technology_counter.most_common(8)
    return ReportData(
        report_type="shortlist",
        title=f"Отчёт по подборке «{shortlist.name}»",
        period=PERIOD_TITLES[period],
        period_start=start,
        period_end=end,
        generated_at=utcnow(),
        summary=summary,
        tables=[ReportTable(title="Кандидаты", columns=columns, rows=rows)],
        chart=ReportChart(
            chart_type="bar",
            title="Технологии кандидатов подборки",
            labels=[name for name, _ in most_common],
            series=[ChartSeries(name="Кандидатов", values=[float(value) for _, value in most_common])],
        ) if most_common else None,
    )


async def load_shortlist(db: AsyncSession, shortlist_id: int) -> Shortlist | None:
    return (await db.execute(
        select(Shortlist).where(Shortlist.id == shortlist_id).options(selectinload(Shortlist.candidates))
    )).scalar_one_or_none()


# ── Выгрузка собственного профиля (PDF) ───────────────────────────────────────

async def profile_report(db: AsyncSession, user_id: int, privacy_mode: PrivacyMode) -> ReportData:
    profile = await build_profile(db, user_id, "employer" if privacy_mode == "anonymous" else "full")
    today = utcnow().date()
    summary = [
        ReportSummaryItem(label="Профиль", value=f"#{profile.public_id} {profile.full_name}"),
        ReportSummaryItem(label="Индекс компетенций", value=f"{profile.rating.competency_index:.2f}%"),
        ReportSummaryItem(label="Доверие сообщества", value=f"{profile.rating.trust_level:.2f}%"),
        ReportSummaryItem(label="Подтверждений", value=str(profile.stats.confirmations)),
        ReportSummaryItem(label="Стаж, лет", value=f"{profile.experience_years:.1f}"),
    ]
    if privacy_mode == "full":
        summary.insert(1, ReportSummaryItem(label="Email", value=profile.email or "—"))
        summary.insert(2, ReportSummaryItem(label="Телефон", value=profile.phone or "—"))
        if profile.city:
            summary.insert(3, ReportSummaryItem(label="Город", value=profile.city))

    education = ReportTable(
        title="Образование",
        columns=["Период", "Учебное заведение", "Тип", "Специальность"],
        rows=[
            [_years_range(edu.start_date, edu.end_date), edu.institution.name,
             edu.education_type_title, edu.specialty or "—"]
            for edu in profile.education
        ],
    )
    return ReportData(
        report_type="profile",
        title="Цифровой профиль ИТ-специалиста" + (" (анонимный)" if privacy_mode == "anonymous" else ""),
        period="на дату выгрузки",
        period_start=profile.registered_at.date(),
        period_end=today,
        generated_at=utcnow(),
        summary=summary,
        tables=[_skills_table(profile), _experience_table(profile), education],
        chart=await _rating_chart(db, user_id, today - timedelta(days=90), "Динамика рейтинга за 90 дней"),
    )
