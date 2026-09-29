from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from ..errors import ForbiddenError, NotFoundError

ModelT = TypeVar("ModelT", bound=SQLModel)


async def get_owned(
    db: AsyncSession, model: type[ModelT], object_id: int, user_id: int, not_found_message: str
) -> ModelT:
    """Запись профиля, принадлежащая пользователю: 404 — нет записи, 403 — запись чужая."""
    instance = await db.get(model, object_id)
    if instance is None:
        raise NotFoundError(not_found_message)
    if instance.user_id != user_id:
        raise ForbiddenError("Нельзя изменять данные другого пользователя")
    return instance
