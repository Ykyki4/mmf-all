from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Rating, RatingHistory, User
from ..models.base import utcnow
from ..schemas.rating import RatingComponent, RatingRead, Trend

# Вклад компонентов в индекс компетенций (совпадает с функцией recalc_user_rating в БД)
COMPONENT_WEIGHTS = {"education": 30, "experience": 30, "confirmations": 40}
COMPONENT_TITLES = {"education": "Образование", "experience": "Опыт", "confirmations": "Подтверждения"}
TREND_WINDOW_DAYS = 30
# Изменение меньше порога считается «без изменений» (➡️)
TREND_THRESHOLD = 0.5

REASON_TITLES = {
    "registration": "Регистрация",
    "education_changed": "Изменено образование",
    "experience_changed": "Изменён опыт работы",
    "confirmation_pending": "Отправлен запрос подтверждения",
    "confirmation_accepted": "Получено подтверждение",
    "confirmation_rejected": "Запрос отклонён",
    "confirmation_revoked": "Подтверждение отозвано",
    "confirmation_deleted": "Подтверждение удалено",
    "integration_changed": "Изменены интеграции",
    "confirmer_verified": "Коллега прошёл верификацию",
    "history_import": "Исторические данные",
}


def trend_of(change: float) -> Trend:
    if change > TREND_THRESHOLD:
        return "up"
    if change < -TREND_THRESHOLD:
        return "down"
    return "flat"


async def get_rating(db: AsyncSession, user_id: int) -> Rating:
    rating = await db.get(Rating, user_id)
    if rating is None:
        # Запись создаётся триггером при регистрации; пустой рейтинг — на случай старых данных
        rating = Rating(user_id=user_id, updated_at=utcnow())
    return rating


async def rating_changes(
    db: AsyncSession, user_ids: list[int], window_days: int = TREND_WINDOW_DAYS
) -> dict[int, float]:
    """Изменение индекса компетенций за `window_days` дней для набора пользователей."""
    if not user_ids:
        return {}
    since = utcnow() - timedelta(days=window_days)
    baseline_query = (
        select(RatingHistory.user_id, RatingHistory.competency_index)
        .where(RatingHistory.user_id.in_(user_ids), RatingHistory.recorded_at <= since)
        .order_by(RatingHistory.user_id, RatingHistory.recorded_at.desc())
        .distinct(RatingHistory.user_id)
    )
    baseline = dict((await db.execute(baseline_query)).all())

    # Если истории старше окна нет — сравниваем с самой ранней записью
    missing = [uid for uid in user_ids if uid not in baseline]
    if missing:
        earliest_query = (
            select(RatingHistory.user_id, RatingHistory.competency_index)
            .where(RatingHistory.user_id.in_(missing))
            .order_by(RatingHistory.user_id, RatingHistory.recorded_at.asc())
            .distinct(RatingHistory.user_id)
        )
        baseline.update(dict((await db.execute(earliest_query)).all()))

    current = dict((await db.execute(
        select(Rating.user_id, Rating.competency_index).where(Rating.user_id.in_(user_ids))
    )).all())
    return {
        uid: round(current.get(uid, 0.0) - baseline.get(uid, current.get(uid, 0.0)), 2)
        for uid in user_ids
    }


async def build_rating_read(db: AsyncSession, user: User) -> RatingRead:
    rating = await get_rating(db, user.id)
    change = (await rating_changes(db, [user.id])).get(user.id, 0.0)
    scores = {
        "education": rating.education_score,
        "experience": rating.experience_score,
        "confirmations": rating.confirmation_score,
    }
    breakdown = [
        RatingComponent(
            component=component,
            title=COMPONENT_TITLES[component],
            weight=weight,
            score=scores[component],
            contribution=round(scores[component] * weight / 100, 2),
        )
        for component, weight in COMPONENT_WEIGHTS.items()
    ]
    return RatingRead(
        user_id=user.id,
        public_id=user.public_id,
        competency_index=rating.competency_index,
        trust_level=rating.trust_level,
        confirmations_count=rating.confirmations_count,
        breakdown=breakdown,
        trend=trend_of(change),
        change_30d=change,
        updated_at=rating.updated_at,
    )
