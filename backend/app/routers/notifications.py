from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..errors import NotFoundError
from ..models import Confirmation, Notification, User
from ..models.enums import ConfirmationStatus, NotificationType
from ..responses import ApiResponse, Page, make_page, ok
from ..schemas.system import NotificationRead, UnreadCount

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _read(notification: Notification) -> NotificationRead:
    return NotificationRead.model_validate(notification, from_attributes=True)


async def _get_own(db: AsyncSession, notification_id: int, user: User) -> Notification:
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise NotFoundError("Уведомление не найдено", "NOTIFICATION_NOT_FOUND")
    return notification


@router.get("", response_model=ApiResponse[Page[NotificationRead]], summary="Уведомления пользователя")
async def list_notifications(
    unread_only: bool = False,
    notification_type: NotificationType | None = Query(default=None, alias="type"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = [Notification.user_id == current_user.id]
    if unread_only:
        conditions.append(Notification.is_read == False)
    if notification_type is not None:
        conditions.append(Notification.notification_type == notification_type)

    total = (await db.execute(select(func.count(Notification.id)).where(*conditions))).scalar_one()
    notifications = (await db.execute(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    message = "OK" if total else "Нет новых событий"
    return ok(make_page([_read(item) for item in notifications], total, page, page_size), message)


@router.get("/unread-count", response_model=ApiResponse[UnreadCount], summary="Счётчик для badge")
async def unread_count(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    unread = (await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id, Notification.is_read == False,
        )
    )).scalar_one()
    pending = (await db.execute(
        select(func.count(Confirmation.id)).where(
            Confirmation.confirmer_id == current_user.id, Confirmation.status == ConfirmationStatus.pending,
        )
    )).scalar_one()
    return ok(UnreadCount(unread=unread, pending_confirmations=pending))


@router.post("/read-all", response_model=ApiResponse[None], summary="Отметить все как прочитанные")
async def mark_all_read(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return ok(None, "Все уведомления прочитаны")


@router.post("/{notification_id}/read", response_model=ApiResponse[NotificationRead], summary="Отметить прочитанным")
async def mark_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notification = await _get_own(db, notification_id, current_user)
    notification.is_read = True
    db.add(notification)
    await db.commit()
    return ok(_read(notification))


@router.delete("/{notification_id}", response_model=ApiResponse[None], summary="Удалить уведомление")
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notification = await _get_own(db, notification_id, current_user)
    await db.execute(delete(Notification).where(Notification.id == notification.id))
    await db.commit()
    return ok(None, "Уведомление удалено")
