from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..models.enums import ConfirmationType
from .common import safe_str
from .users import UserShort

Direction = Literal["incoming", "outgoing"]


class ConfirmationRequest(BaseModel):
    # Коллега, который должен подтвердить
    confirmer_id: int = Field(gt=0)
    confirmation_type: ConfirmationType = ConfirmationType.skill
    # id навыка пользователя / записи опыта / записи образования
    subject_id: int = Field(gt=0)
    message: safe_str(500) | None = None


class ConfirmationSubject(BaseModel):
    id: int
    title: str               # «Python», «Middle Python в ООО Ромашка», «МГУ, Прикладная информатика»
    level: int | None = None
    description: str | None = None


class ConfirmationRead(BaseModel):
    id: int
    direction: Direction
    confirmation_type: str
    status: str
    status_title: str
    subject: ConfirmationSubject
    requested_level: int | None
    message: str | None
    requester: UserShort
    confirmer: UserShort
    # Текст карточки таймлайна: «Коллега Петров запросил подтверждение Python 9/10 (09.03.26)»
    summary: str
    created_at: datetime
    responded_at: datetime | None
    revoked_at: datetime | None


class ConfirmationCounters(BaseModel):
    incoming_pending: int
    outgoing_pending: int
    accepted: int
