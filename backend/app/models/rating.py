from datetime import datetime

from sqlalchemy import CheckConstraint, Index
from sqlmodel import Field, SQLModel

from .base import created_at_field, score_field

# ── Рейтинг (пересчитывается триггерами) ──────────────────────────────────────

class Rating(SQLModel, table=True):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint("competency_index BETWEEN 0 AND 100", name="ck_ratings_competency_range"),
        CheckConstraint("trust_level BETWEEN 0 AND 100", name="ck_ratings_trust_range"),
        CheckConstraint("confirmations_count >= 0", name="ck_ratings_confirmations_count"),
    )

    user_id: int = Field(primary_key=True, foreign_key="users.id", ondelete="CASCADE")
    competency_index: float = score_field()
    trust_level: float = score_field()
    # Составляющие индекса компетенций (вклад: образование 30%, опыт 30%, подтверждения 40%)
    education_score: float = score_field()
    experience_score: float = score_field()
    confirmation_score: float = score_field()
    confirmations_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    updated_at: datetime = created_at_field()


class RatingHistory(SQLModel, table=True):
    __tablename__ = "rating_history"
    __table_args__ = (Index("ix_rating_history_user_recorded", "user_id", "recorded_at"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE")
    competency_index: float = score_field()
    trust_level: float = score_field()
    education_score: float = score_field()
    experience_score: float = score_field()
    confirmation_score: float = score_field()
    # Событие, вызвавшее пересчёт (confirmation_accepted, experience_changed, ...)
    reason: str = Field(max_length=50)
    recorded_at: datetime = created_at_field()
