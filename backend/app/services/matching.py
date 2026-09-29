"""Совпадение профилей с вакансиями (mock-вакансии) и рекомендации по развитию."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import UserSkill, Vacancy, VacancySkill
from ..schemas.rating import Recommendations, SkillRecommendation, VacancyMatch

MAX_VACANCY_MATCHES = 3
MAX_SKILL_RECOMMENDATIONS = 5


@dataclass
class VacancyRequirements:
    vacancy_id: int
    title: str
    company: str
    # technology_id → (название, требуемый уровень)
    skills: dict[int, tuple[str, int]]


async def load_vacancies(db: AsyncSession) -> list[VacancyRequirements]:
    vacancies = (await db.execute(
        select(Vacancy)
        .where(Vacancy.is_active == True)
        .options(
            selectinload(Vacancy.company),
            selectinload(Vacancy.required_skills).selectinload(VacancySkill.technology),
        )
    )).scalars().all()
    return [
        VacancyRequirements(
            vacancy_id=vacancy.id,
            title=vacancy.title,
            company=vacancy.company.name if vacancy.company else "",
            skills={
                req.technology_id: (req.technology.name, req.required_level)
                for req in vacancy.required_skills
            },
        )
        for vacancy in vacancies
        if vacancy.required_skills
    ]


def match_percent(user_levels: dict[int, int], vacancy: VacancyRequirements) -> int:
    """Среднее по требованиям вакансии: min(уровень / требуемый, 1). Нет навыка — 0."""
    if not vacancy.skills:
        return 0
    total = 0.0
    for technology_id, (_, required) in vacancy.skills.items():
        level = user_levels.get(technology_id, 0)
        total += 1.0 if required == 0 else min(level / required, 1.0)
    return round(total / len(vacancy.skills) * 100)


def best_match(user_levels: dict[int, int], vacancies: list[VacancyRequirements]) -> int:
    return max((match_percent(user_levels, vacancy) for vacancy in vacancies), default=0)


async def user_skill_levels(db: AsyncSession, user_id: int) -> dict[int, int]:
    rows = await db.execute(
        select(UserSkill.technology_id, UserSkill.level).where(UserSkill.user_id == user_id)
    )
    return dict(rows.all())


def build_recommendations(
    user_levels: dict[int, int], vacancies: list[VacancyRequirements]
) -> Recommendations:
    ranked = sorted(
        ((match_percent(user_levels, vacancy), vacancy) for vacancy in vacancies),
        key=lambda item: item[0],
        reverse=True,
    )
    top = ranked[:MAX_VACANCY_MATCHES]

    vacancy_matches = []
    skill_recommendations: dict[int, SkillRecommendation] = {}
    for percent, vacancy in top:
        missing = []
        for technology_id, (name, required) in vacancy.skills.items():
            current = user_levels.get(technology_id, 0)
            if current >= required:
                continue
            missing.append(name)
            known = skill_recommendations.get(technology_id)
            # Для навыка оставляем самое высокое требование среди подходящих вакансий
            if known is None or known.required_level < required:
                skill_recommendations[technology_id] = SkillRecommendation(
                    technology=name,
                    required_level=required,
                    current_level=current,
                    vacancy_title=vacancy.title,
                    text=f"Подтянуть {name} (требуется {required}/10, сейчас {current}/10)",
                )
        vacancy_matches.append(VacancyMatch(
            vacancy_id=vacancy.vacancy_id,
            title=vacancy.title,
            company=vacancy.company,
            match_percent=percent,
            missing_skills=missing,
            text=f"{vacancy.title} — {percent}% соответствие",
        ))

    skills = sorted(
        skill_recommendations.values(),
        key=lambda rec: rec.required_level - rec.current_level,
        reverse=True,
    )[:MAX_SKILL_RECOMMENDATIONS]
    return Recommendations(skills=skills, vacancies=vacancy_matches)


async def get_recommendations(db: AsyncSession, user_id: int) -> Recommendations:
    vacancies = await load_vacancies(db)
    return build_recommendations(await user_skill_levels(db, user_id), vacancies)
