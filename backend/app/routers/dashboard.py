from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_employer
from ..database import get_db
from ..models import User
from ..responses import ApiResponse, ok
from ..schemas.hr import DashboardSummary
from ..services.analytics import dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "/summary",
    response_model=ApiResponse[DashboardSummary],
    summary="Сводка для главной страницы HR (активные профили, соответствие вакансиям, средний рейтинг)",
)
async def get_summary(_: User = Depends(get_current_employer), db: AsyncSession = Depends(get_db)):
    return ok(await dashboard_summary(db))
