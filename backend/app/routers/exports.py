"""Экспорт профиля в PDF/JSON по временной ссылке."""

import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..errors import ApiError, NotFoundError
from ..models import ProfileExport, User
from ..models.base import utcnow
from ..models.enums import ExportFormat, PrivacyMode
from ..responses import ApiResponse, ok
from ..schemas.system import ExportCreate, ExportRead
from ..schemas.users import ProfileRead
from ..services.documents import render_pdf
from ..services.profiles import build_profile
from ..services.reports import profile_report

router = APIRouter(tags=["exports"])

# «Бессрочная» ссылка хранится не дольше двух лет
MAX_EXPORT_TTL_DAYS = 730
_MEDIA_TYPES = {ExportFormat.pdf: "application/pdf", ExportFormat.json: "application/json"}


def _export_read(export: ProfileExport) -> ExportRead:
    now = utcnow()
    return ExportRead(
        id=export.id,
        format=export.export_format,
        privacy_mode=export.privacy_mode,
        file_name=export.file_name,
        download_url=f"{settings.public_base_url}{settings.api_prefix}/exports/{export.token}",
        created_at=export.created_at,
        expires_at=export.expires_at,
        expires_in_seconds=max(0, int((export.expires_at - now).total_seconds())),
        is_expired=export.expires_at <= now,
    )


@router.get(
    "/users/me/export/preview",
    response_model=ApiResponse[ProfileRead],
    summary="Предпросмотр содержимого экспорта",
)
async def preview_export(
    privacy_mode: PrivacyMode = PrivacyMode.full,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mode = "employer" if privacy_mode == PrivacyMode.anonymous else "full"
    return ok(await build_profile(db, current_user.id, mode))


@router.post(
    "/users/me/export",
    response_model=ApiResponse[ExportRead],
    status_code=status.HTTP_201_CREATED,
    summary="Сформировать PDF/JSON профиля и временную ссылку на файл",
)
async def create_export(
    body: ExportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = utcnow()
    if body.format == ExportFormat.pdf:
        content = render_pdf(await profile_report(db, current_user.id, body.privacy_mode))
    else:
        mode = "employer" if body.privacy_mode == PrivacyMode.anonymous else "full"
        profile = await build_profile(db, current_user.id, mode)
        content = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")

    export = ProfileExport(
        user_id=current_user.id,
        token=secrets.token_urlsafe(32),
        export_format=body.format,
        privacy_mode=body.privacy_mode,
        file_name=f"profile_{now:%Y-%m-%d}_{body.format}.{body.format}",
        content=content,
        created_at=now,
        expires_at=now + timedelta(days=body.ttl_days or MAX_EXPORT_TTL_DAYS),
    )
    db.add(export)
    await db.commit()
    return ok(_export_read(export), "Файл сформирован")


@router.get("/users/me/exports", response_model=ApiResponse[list[ExportRead]], summary="Ранее созданные ссылки")
async def list_exports(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    exports = (await db.execute(
        select(ProfileExport)
        .where(ProfileExport.user_id == current_user.id)
        .order_by(ProfileExport.created_at.desc())
        .limit(50)
    )).scalars().all()
    return ok([_export_read(item) for item in exports])


@router.get(
    "/exports/{token}",
    summary="Скачать файл по временной ссылке (без авторизации: токен в ссылке)",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "application/json": {}}}, 410: {"description": "Истекла"}},
)
async def download_export(token: str, db: AsyncSession = Depends(get_db)):
    export = (await db.execute(
        select(ProfileExport).where(ProfileExport.token == token)
    )).scalar_one_or_none()
    if export is None:
        raise NotFoundError("Файл не найден", "EXPORT_NOT_FOUND")
    if export.expires_at <= utcnow():
        raise ApiError(status.HTTP_410_GONE, "EXPORT_EXPIRED", "Ссылка недействительна: срок действия истёк")
    return Response(
        content=export.content,
        media_type=_MEDIA_TYPES[ExportFormat(export.export_format)],
        headers={"Content-Disposition": f'attachment; filename="{export.file_name}"'},
    )
