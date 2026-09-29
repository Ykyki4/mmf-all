from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user
from ..database import get_db
from ..errors import BadRequestError
from ..models import User, WorkExperience
from ..responses import ApiResponse, ok
from ..schemas.profile import ExperienceCreate, ExperienceUpdate
from ..schemas.users import ExperienceRead
from ..services.ownership import get_owned
from ..services.profiles import experience_read
from ..services.references import resolve_company

router = APIRouter(prefix="/experience", tags=["experience"])

_NOT_FOUND = "Запись об опыте работы не найдена"


async def _load(db: AsyncSession, record_id: int) -> WorkExperience:
    return (await db.execute(
        select(WorkExperience)
        .where(WorkExperience.id == record_id)
        .options(selectinload(WorkExperience.company))
        .execution_options(populate_existing=True)
    )).scalar_one()


@router.get("", response_model=ApiResponse[list[ExperienceRead]], summary="Опыт работы текущего пользователя")
async def list_experience(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    records = (await db.execute(
        select(WorkExperience)
        .where(WorkExperience.user_id == current_user.id)
        .options(selectinload(WorkExperience.company))
        .order_by(WorkExperience.start_date.desc())
    )).scalars().all()
    return ok([experience_read(record) for record in records])


@router.post(
    "",
    response_model=ApiResponse[ExperienceRead],
    status_code=status.HTTP_201_CREATED,
    summary="Добавить опыт работы (периоды не должны пересекаться)",
)
async def create_experience(
    body: ExperienceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    company = await resolve_company(db, body.company_id, body.company_name)
    record = WorkExperience(
        user_id=current_user.id,
        company_id=company.id if company else None,
        **body.model_dump(exclude={"company_id", "company_name"}),
    )
    db.add(record)
    await db.commit()  # пересечение периодов блокирует триггер БД → 400 EXPERIENCE_OVERLAP
    return ok(experience_read(await _load(db, record.id)), "Опыт работы добавлен")


@router.put("/{record_id}", response_model=ApiResponse[ExperienceRead], summary="Изменить опыт работы")
async def update_experience(
    record_id: int,
    body: ExperienceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await get_owned(db, WorkExperience, record_id, current_user.id, _NOT_FOUND)
    changes = body.model_dump(exclude_unset=True, exclude={"company_id", "company_name"})
    if body.company_id is not None or body.company_name:
        company = await resolve_company(db, body.company_id, body.company_name)
        record.company_id = company.id if company else None
    for key, value in changes.items():
        if key in ("employment_type", "position", "start_date") and value is None:
            raise BadRequestError(f"Поле {key} не может быть пустым", "VALIDATION_ERROR")
        setattr(record, key, value)
    db.add(record)
    await db.commit()
    return ok(experience_read(await _load(db, record.id)), "Опыт работы обновлён")


@router.delete("/{record_id}", response_model=ApiResponse[None], summary="Удалить опыт работы")
async def delete_experience(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await get_owned(db, WorkExperience, record_id, current_user.id, _NOT_FOUND)
    await db.delete(record)
    await db.commit()
    return ok(None, "Опыт работы удалён")
