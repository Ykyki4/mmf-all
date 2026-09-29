from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_employer
from ..database import get_db
from ..errors import ForbiddenError, NotFoundError
from ..models import Shortlist, User
from ..models.base import utcnow
from ..models.enums import UserRole
from ..responses import ApiResponse, ok
from ..schemas.hr import ReportData, ReportSuggestion
from ..services.documents import render_pdf, render_xlsx
from ..services.profiles import get_user_or_404, parse_user_ref
from ..services.reports import ReportPeriod, candidate_report, load_shortlist, shortlist_report

router = APIRouter(prefix="/reports", tags=["reports"])

ReportFormat = Literal["json", "pdf", "xlsx"]
_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def report_response(report: ReportData, report_format: ReportFormat, file_stem: str):
    """JSON — для предпросмотра, pdf/xlsx — файл для скачивания."""
    if report_format == "json":
        return ok(report)
    content = render_pdf(report) if report_format == "pdf" else render_xlsx(report)
    file_name = f"{file_stem}_{utcnow():%Y-%m-%d}.{report_format}"
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[report_format],
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get(
    "/suggest",
    response_model=ApiResponse[list[ReportSuggestion]],
    summary="Автокомплит ID для генератора отчётов",
)
async def suggest_report_targets(
    report_type: Literal["candidate", "shortlist"] = Query(alias="type"),
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    pattern = f"%{q.strip().lower().removeprefix('#').removeprefix('itp-').lstrip('0')}%"
    if report_type == "candidate":
        users = (await db.execute(
            select(User)
            .where(User.role == UserRole.user, User.is_active == True, User.is_profile_public == True,
                   cast(User.id, String).like(pattern))
            .order_by(User.id)
            .limit(limit)
        )).scalars().all()
        return ok([ReportSuggestion(id=user.id, label=f"#{user.public_id} {user.anonymized_name}") for user in users])

    query = select(Shortlist).where(
        or_(func.lower(Shortlist.name).like(pattern), cast(Shortlist.id, String).like(pattern))
    )
    if current_user.role != UserRole.admin:
        query = query.where(Shortlist.owner_id == current_user.id)
    shortlists = (await db.execute(query.order_by(Shortlist.name).limit(limit))).scalars().all()
    return ok([ReportSuggestion(id=item.id, label=f"#{item.id} {item.name}") for item in shortlists])


@router.get(
    "/candidate/{candidate_ref}",
    response_model=ApiResponse[ReportData],
    summary="Отчёт по кандидату (format=json для предпросмотра, pdf/xlsx — файл)",
    responses={200: {"content": {"application/pdf": {}, _MEDIA_TYPES["xlsx"]: {}}}},
)
async def get_candidate_report(
    candidate_ref: str,
    period: ReportPeriod = "month",
    report_format: ReportFormat = Query(default="json", alias="format"),
    current_user: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    candidate = await get_user_or_404(db, parse_user_ref(candidate_ref, current_user))
    if candidate.role != UserRole.user:
        raise NotFoundError("Кандидат не найден", "USER_NOT_FOUND")
    report = await candidate_report(db, candidate.id, period)
    return report_response(report, report_format, f"candidate_{candidate.public_id}")


@router.get(
    "/shortlist/{shortlist_id}",
    response_model=ApiResponse[ReportData],
    summary="Отчёт по подборке (format=json для предпросмотра, pdf/xlsx — файл)",
    responses={200: {"content": {"application/pdf": {}, _MEDIA_TYPES["xlsx"]: {}}}},
)
async def get_shortlist_report(
    shortlist_id: int,
    period: ReportPeriod = "month",
    report_format: ReportFormat = Query(default="json", alias="format"),
    current_user: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    shortlist = await load_shortlist(db, shortlist_id)
    if shortlist is None:
        raise NotFoundError("Подборка не найдена", "SHORTLIST_NOT_FOUND")
    if shortlist.owner_id != current_user.id and current_user.role != UserRole.admin:
        raise ForbiddenError("Подборка принадлежит другому сотруднику")
    report = await shortlist_report(db, shortlist, period)
    return report_response(report, report_format, f"shortlist_{shortlist.id}")
