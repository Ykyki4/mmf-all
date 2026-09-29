from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_employer
from ..database import get_db
from ..errors import BadRequestError
from ..models import User
from ..models.base import utcnow
from ..responses import ApiResponse, ok
from ..schemas.hr import RatingTrendResponse, SkillsAnalytics
from ..services.analytics import rating_trend, skills_analytics
from ..services.documents import render_csv

router = APIRouter(prefix="/analytics", tags=["analytics"])

ALLOWED_PERIODS = (30, 90)
_NO_DATA = "Недостаточно данных для анализа"


def analytics_period(period: int = Query(default=30, description="Период, дней: 30 или 90")) -> int:
    if period not in ALLOWED_PERIODS:
        raise BadRequestError("Период должен быть 30 или 90 дней", "VALIDATION_ERROR")
    return period


@router.get("/skills", response_model=ApiResponse[SkillsAnalytics], summary="Распределение специалистов по технологиям")
async def get_skills_analytics(
    period: int = Depends(analytics_period),
    top: int = Query(default=3, ge=1, le=10, description="Секторов круговой диаграммы до «Другое»"),
    _: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    result = await skills_analytics(db, period, top)
    return ok(result, "OK" if result.distribution else _NO_DATA)


@router.get(
    "/rating/trend",
    response_model=ApiResponse[RatingTrendResponse],
    summary="Динамика среднего рейтинга (0–10) по дням",
)
async def get_rating_trend(
    period: int = Depends(analytics_period),
    _: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    result = await rating_trend(db, period)
    return ok(result, "OK" if result.points else _NO_DATA)


@router.get("/export", summary="Экспорт аналитики в CSV", response_class=Response)
async def export_analytics(
    period: int = Depends(analytics_period),
    _: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    skills = await skills_analytics(db, period, 3)
    trend = await rating_trend(db, period)

    rows: list[list] = [["Раздел", "Показатель", "Значение 1", "Значение 2"]]
    rows += [["Распределение", item.name, item.specialists_count, f"{item.percent}%"] for item in skills.distribution]
    rows += [
        ["Топ-10 технологий", item.name, item.avg_level, item.specialists_count]
        for item in skills.top_technologies
    ]
    rows += [["Средний рейтинг", point.date.isoformat(), point.value, ""] for point in trend.points]

    file_name = f"analytics_{period}d_{utcnow():%Y-%m-%d}.csv"
    return Response(
        content=render_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
