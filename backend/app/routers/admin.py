from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_admin
from ..database import get_db, set_acting_user
from ..errors import BadRequestError, NotFoundError
from ..models import RefreshToken, RoleHistory, User
from ..models.base import utcnow
from ..models.enums import UserRole
from ..responses import ApiResponse, Page, make_page, ok
from ..schemas.users import ActiveUpdate, AdminUserRead, RoleHistoryRead, RoleUpdate

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin_user_read(user: User) -> AdminUserRead:
    return AdminUserRead(
        id=user.id, public_id=user.public_id, email=user.email, full_name=user.full_name,
        role=user.role, is_active=user.is_active, created_at=user.created_at, last_seen_at=user.last_seen_at,
    )


async def _get_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("Пользователь не найден", "USER_NOT_FOUND")
    return user


@router.get("/users", response_model=ApiResponse[Page[AdminUserRead]], summary="Пользователи системы")
async def list_users(
    q: str = Query(default="", max_length=100),
    role: UserRole | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if role is not None:
        conditions.append(User.role == role)
    if q.strip():
        pattern = f"%{q.strip().lower()}%"
        conditions.append(or_(User.email.like(pattern), func.lower(User.last_name).like(pattern)))
    total = (await db.execute(select(func.count(User.id)).where(*conditions))).scalar_one()
    users = (await db.execute(
        select(User).where(*conditions).order_by(User.id).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return ok(make_page([_admin_user_read(user) for user in users], total, page, page_size))


@router.put("/users/{user_id}/role", response_model=ApiResponse[AdminUserRead], summary="Сменить роль пользователя")
async def change_role(
    user_id: int,
    body: RoleUpdate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(db, user_id)
    if user.id == current_admin.id:
        raise BadRequestError("Нельзя изменить собственную роль", "SELF_ROLE_CHANGE")
    # Инициатор попадает в role_history через триггер
    await set_acting_user(db, current_admin.id)
    user.role = body.role
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return ok(_admin_user_read(user), "Роль изменена")


@router.put("/users/{user_id}/active", response_model=ApiResponse[AdminUserRead], summary="Блокировка пользователя")
async def change_active(
    user_id: int,
    body: ActiveUpdate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(db, user_id)
    if user.id == current_admin.id:
        raise BadRequestError("Нельзя заблокировать самого себя", "SELF_BLOCK")
    user.is_active = body.is_active
    db.add(user)
    if not body.is_active:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
    await db.commit()
    await db.refresh(user)
    return ok(_admin_user_read(user), "Статус пользователя изменён")


@router.get("/role-history", response_model=ApiResponse[list[RoleHistoryRead]], summary="Журнал смены ролей")
async def role_history(
    user_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(RoleHistory).order_by(RoleHistory.changed_at.desc()).limit(limit)
    if user_id is not None:
        query = query.where(RoleHistory.user_id == user_id)
    records = (await db.execute(query)).scalars().all()
    return ok([RoleHistoryRead.model_validate(record, from_attributes=True) for record in records])
