from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..responses import Page
from .common import safe_str
from .rating import Trend, TrendPoint

# ── Главная страница HR ───────────────────────────────────────────────────────


class SummaryMetric(BaseModel):
    value: int | float
    delta: int | float


class DashboardSummary(BaseModel):
    active_profiles: SummaryMetric      # активных профилей и прирост за сутки
    job_match: SummaryMetric            # среднее соответствие вакансиям, % и изменение за сутки
    avg_rating: SummaryMetric           # средний рейтинг /10 и изменение за сутки
    confirmations_today: SummaryMetric  # подтверждений за сегодня и разница со вчера
    updated_at: datetime


# ── Поиск кандидатов ──────────────────────────────────────────────────────────

class CandidateSkill(BaseModel):
    name: str
    level: int
    confirmations: int


class CandidateSearchItem(BaseModel):
    id: int
    public_id: str
    skills: list[CandidateSkill]
    competency_index: float
    trust_level: float
    confirmations_count: int
    experience_years: float
    trend: Trend
    activity_status: Literal["online", "recent", "offline"]


# ── Подборки ──────────────────────────────────────────────────────────────────

class ShortlistCreate(BaseModel):
    name: safe_str(150)
    description: safe_str(1000) | None = None
    vacancy_id: int | None = Field(default=None, gt=0)


class ShortlistUpdate(BaseModel):
    name: safe_str(150) | None = None
    description: safe_str(1000) | None = None
    vacancy_id: int | None = Field(default=None, gt=0)


class ShortlistRead(BaseModel):
    id: int
    name: str
    description: str | None
    vacancy_id: int | None
    candidates_count: int
    created_at: datetime
    updated_at: datetime | None
    # Заполняется, если в запросе передан candidate_id (для модального окна «В подборку»)
    contains_candidate: bool | None = None


class ShortlistCandidateRead(BaseModel):
    candidate_id: int
    public_id: str
    competency_index: float
    trust_level: float
    trend: Trend
    top_skills: list[str]
    note: str | None
    added_at: datetime


class ShortlistDetail(ShortlistRead):
    candidates: Page[ShortlistCandidateRead]


class ShortlistCandidateAdd(BaseModel):
    candidate_id: int = Field(gt=0)
    note: safe_str(500) | None = None


class AddToShortlists(BaseModel):
    """Добавление кандидата сразу в несколько подборок (модальное окно множественного выбора)."""

    candidate_id: int = Field(gt=0)
    shortlist_ids: list[int] = Field(min_length=1, max_length=50)


class AddToShortlistsResult(BaseModel):
    added_to: list[int]
    already_in: list[int]


# ── Отчёты ────────────────────────────────────────────────────────────────────

class ReportTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[Any]]


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class ReportChart(BaseModel):
    chart_type: Literal["line", "bar", "pie"]
    title: str
    labels: list[str]
    series: list[ChartSeries]


class ReportSummaryItem(BaseModel):
    label: str
    value: str


class ReportData(BaseModel):
    report_type: Literal["candidate", "shortlist", "profile"]
    title: str
    period: str
    period_start: date
    period_end: date
    generated_at: datetime
    summary: list[ReportSummaryItem]
    tables: list[ReportTable]
    chart: ReportChart | None = None


class ReportSuggestion(BaseModel):
    id: int
    label: str


# ── Аналитика рынка ───────────────────────────────────────────────────────────

class DistributionItem(BaseModel):
    name: str
    specialists_count: int
    percent: float


class TechnologyStat(BaseModel):
    name: str
    avg_level: float
    specialists_count: int


class SkillsAnalytics(BaseModel):
    period_days: int
    total_specialists: int
    distribution: list[DistributionItem]
    top_technologies: list[TechnologyStat]


class RatingTrendResponse(BaseModel):
    period_days: int
    points: list[TrendPoint]
    current: float | None
    change: float
    trend: Trend
