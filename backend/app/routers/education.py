from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user
from ..database import get_db
from ..errors import BadRequestError
from ..models import EducationRecord, User
from ..responses import ApiResponse, ok
from ..schemas.profile import EducationCreate, EducationReorder, EducationUpdate
from ..schemas.users import EducationRead
from ..services.ownership import get_owned
from ..services.profiles import education_read
from ..services.references import resolve_institution

router = APIRouter(prefix="/education", tags=["education"])

_NOT_FOUND = "Запись об образовании не найдена"


async def _load(db: AsyncSession, record_id: int) -> EducationRecord:
    return (await db.execute(
        select(EducationRecord)
        .where(EducationRecord.id == record_id)
        .options(selectinload(EducationRecord.institution))
        .execution_options(populate_existing=True)
    )).scalar_one()


@router.get("", response_model=ApiResponse[list[EducationRead]], summary="Образование текущего пользователя")
async def list_education(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    records = (await db.execute(
        select(EducationRecord)
        .where(EducationRecord.user_id == current_user.id)
        .options(selectinload(EducationRecord.institution))
        .order_by(EducationRecord.sort_order, EducationRecord.start_date)
    )).scalars().all()
    return ok([education_read(record) for record in records])


@router.post(
    "",
    response_model=ApiResponse[EducationRead],
    status_code=status.HTTP_201_CREATED,
    summary="Добавить образование",
)
async def create_education(
    body: EducationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    institution = await resolve_institution(db, body.institution_id, body.institution_name, body.education_type)
    next_order = (await db.execute(
        select(func.coalesce(func.max(EducationRecord.sort_order) + 1, 0))
        .where(EducationRecord.user_id == current_user.id)
    )).scalar_one()
    record = EducationRecord(
        user_id=current_user.id,
        institution_id=institution.id,
        sort_order=next_order,
        **body.model_dump(exclude={"institution_id", "institution_name"}),
    )
    db.add(record)
    await db.commit()
    return ok(education_read(await _load(db, record.id)), "Образование добавлено")


@router.put("/reorder", response_model=ApiResponse[list[EducationRead]], summary="Изменить порядок (drag-to-reorder)")
async def reorder_education(
    body: EducationReorder,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    records = {
        record.id: record
        for record in (await db.execute(
            select(EducationRecord).where(EducationRecord.user_id == current_user.id)
        )).scalars()
    }
    if len(set(body.ids)) != len(body.ids) or set(body.ids) != set(records):
        raise BadRequestError("Передайте все id записей об образовании без повторов", "INVALID_ORDER")
    for position, record_id in enumerate(body.ids):
        records[record_id].sort_order = position
        db.add(records[record_id])
    await db.commit()
    return await list_education(current_user, db)


@router.put("/{record_id}", response_model=ApiResponse[EducationRead], summary="Изменить образование")
async def update_education(
    record_id: int,
    body: EducationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await get_owned(db, EducationRecord, record_id, current_user.id, _NOT_FOUND)
    changes = body.model_dump(exclude_unset=True, exclude={"institution_id", "institution_name"})
    if body.institution_id is not None or body.institution_name:
        institution = await resolve_institution(
            db, body.institution_id, body.institution_name, body.education_type or record.education_type,
        )
        record.institution_id = institution.id
    for key, value in changes.items():
        if key in ("education_type", "start_date") and value is None:
            raise BadRequestError(f"Поле {key} не может быть пустым", "VALIDATION_ERROR")
        setattr(record, key, value)
    db.add(record)
    await db.commit()  # даты проверяются CHECK и триггером БД
    return ok(education_read(await _load(db, record.id)), "Образование обновлено")


@router.delete("/{record_id}", response_model=ApiResponse[None], summary="Удалить образование")
async def delete_education(
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await get_owned(db, EducationRecord, record_id, current_user.id, _NOT_FOUND)
    await db.delete(record)
    await db.commit()
    return ok(None, "Образование удалено")
