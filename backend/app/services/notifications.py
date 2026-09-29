from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Notification
from ..models.enums import NotificationType


def notify(
    db: AsyncSession,
    user_id: int,
    notification_type: NotificationType,
    title: str,
    content: str,
    confirmation_id: int | None = None,
    vacancy_id: int | None = None,
) -> None:
    """Добавляет уведомление в текущую транзакцию (сохраняется вместе с основной операцией)."""
    db.add(Notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        content=content,
        confirmation_id=confirmation_id,
        vacancy_id=vacancy_id,
    ))
