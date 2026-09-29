from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, Relationship, SQLModel

from .base import created_at_field, enum_check, timestamp_field
from .enums import ConfirmationStatus, ConfirmationType
from .profile import EducationRecord, UserSkill, WorkExperience
from .users import User

# Для каждого типа подтверждения заполнена ровно одна ссылка на подтверждаемый объект
_SUBJECT_MATCHES_TYPE = (
    "(confirmation_type = 'skill' AND user_skill_id IS NOT NULL "
    "AND work_experience_id IS NULL AND education_record_id IS NULL) OR "
    "(confirmation_type = 'experience' AND work_experience_id IS NOT NULL "
    "AND user_skill_id IS NULL AND education_record_id IS NULL) OR "
    "(confirmation_type = 'education' AND education_record_id IS NOT NULL "
    "AND user_skill_id IS NULL AND work_experience_id IS NULL)"
)


# ── Подтверждения («двустороннее рукопожатие») ────────────────────────────────

class Confirmation(SQLModel, table=True):
    """requester — владелец навыка/опыта/образования, запрашивает подтверждение;
    confirmer — коллега, который принимает или отклоняет запрос."""

    __tablename__ = "confirmations"
    __table_args__ = (
        CheckConstraint("requester_id <> confirmer_id", name="ck_confirmations_no_self"),
        CheckConstraint(enum_check("confirmation_type", ConfirmationType), name="ck_confirmations_type"),
        CheckConstraint(enum_check("status", ConfirmationStatus), name="ck_confirmations_status"),
        CheckConstraint(_SUBJECT_MATCHES_TYPE, name="ck_confirmations_subject"),
        CheckConstraint(
            "requested_level IS NULL OR requested_level BETWEEN 0 AND 10",
            name="ck_confirmations_requested_level",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    requester_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    confirmer_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    confirmation_type: str = Field(max_length=20)
    user_skill_id: int | None = Field(default=None, foreign_key="user_skills.id", ondelete="CASCADE")
    work_experience_id: int | None = Field(
        default=None, foreign_key="work_experiences.id", ondelete="CASCADE"
    )
    education_record_id: int | None = Field(
        default=None, foreign_key="education_records.id", ondelete="CASCADE"
    )
    # Уровень навыка на момент запроса («Python 9/10»)
    requested_level: int | None = None
    message: str | None = Field(default=None, max_length=500)
    status: str = Field(
        default=ConfirmationStatus.pending, max_length=20, index=True,
        sa_column_kwargs={"server_default": ConfirmationStatus.pending.value},
    )
    created_at: datetime = created_at_field()
    responded_at: datetime | None = timestamp_field()
    revoked_at: datetime | None = timestamp_field()

    requester: User | None = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Confirmation.requester_id]"}
    )
    confirmer: User | None = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Confirmation.confirmer_id]"}
    )
    user_skill: UserSkill | None = Relationship()
    work_experience: WorkExperience | None = Relationship()
    education_record: EducationRecord | None = Relationship()

