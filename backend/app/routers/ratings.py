from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import RatingHistory, User
from ..models.base import utcnow
from ..responses import ApiResponse, ok
from ..schemas.rating import RatingHistory as RatingHistorySchema
from ..schemas.rating import RatingHistoryItem, RatingRead
from ..services.profiles import ensure_profile_visible, get_user_or_404, parse_user_ref
from ..services.rating import REASON_TITLES, build_rating_read

router = APIRouter(prefix="/users", tags=["rating"])

HISTORY_PERIODS = {"30": 30, "90": 90, "365": 365, "all": None}


@router.get(
    "/me/rating/history",
    response_model=ApiResponse[RatingHistorySchema],
    summary="История изменений рейтинга текущего пользователя",
)
async def my_rating_history(
    period: Literal["30", "90", "365", "all"] = Query(default="all", description="Период в днях"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    period_days = HISTORY_PERIODS[period]
    query = select(RatingHistory).where(RatingHistory.user_id == current_user.id)
    if period_days is not None:
        query = query.where(RatingHistory.recorded_at >= utcnow() - timedelta(days=period_days))
    records = (await db.execute(query.order_by(RatingHistory.recorded_at))).scalars().all()
    items = [
        RatingHistoryItem(
            recorded_at=record.recorded_at,
            competency_index=record.competency_index,
            trust_level=record.trust_level,
            reason=record.reason,
            reason_title=REASON_TITLES.get(record.reason, record.reason),
        )
        for record in records
    ]
    message = "OK" if items else "Рейтинг формируется"
    return ok(RatingHistorySchema(period_days=period_days, items=items), message)


@router.get(
    "/{user_ref}/rating",
    response_model=ApiResponse[RatingRead],
    summary="Актуальный рейтинг пользователя (индекс компетенций, доверие, вклад компонентов)",
)
async def get_user_rating(
    user_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await get_user_or_404(db, parse_user_ref(user_ref, current_user))
    ensure_profile_visible(current_user, target)
    return ok(await build_rating_read(db, target))
