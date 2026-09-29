from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Trend = Literal["up", "flat", "down"]


class RatingComponent(BaseModel):
    component: Literal["education", "experience", "confirmations"]
    title: str
    weight: int          # вклад в индекс, %
    score: float         # балл компонента 0–100
    contribution: float  # score × weight / 100


class RatingRead(BaseModel):
    user_id: int
    public_id: str
    competency_index: float
    trust_level: float
    confirmations_count: int
    breakdown: list[RatingComponent]
    trend: Trend
    change_30d: float
    updated_at: datetime


class RatingHistoryItem(BaseModel):
    recorded_at: datetime
    competency_index: float
    trust_level: float
    reason: str
    reason_title: str


class RatingHistory(BaseModel):
    period_days: int | None
    items: list[RatingHistoryItem]


class SkillRecommendation(BaseModel):
    technology: str
    required_level: int
    current_level: int
    vacancy_title: str
    text: str


class VacancyMatch(BaseModel):
    vacancy_id: int
    title: str
    company: str
    match_percent: int
    missing_skills: list[str]
    text: str


class Recommendations(BaseModel):
    skills: list[SkillRecommendation]
    vacancies: list[VacancyMatch]


class TrendPoint(BaseModel):
    date: date
    value: float
