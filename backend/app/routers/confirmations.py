from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..errors import BadRequestError, ForbiddenError, NotFoundError
from ..models import Confirmation, EducationRecord, User, UserSkill, WorkExperience
from ..models.enums import ConfirmationStatus, ConfirmationType, NotificationType, UserRole
from ..responses import ApiResponse, Page, make_page, ok
from ..schemas.confirmations import ConfirmationCounters, ConfirmationRead, ConfirmationRequest
from ..services.confirmations import (
    TYPE_TITLES,
    describe_subject,
    load_confirmation,
    serialize_confirmations,
    with_details,
)
from ..services.notifications import notify

router = APIRouter(prefix="/confirmations", tags=["confirmations"])

# Тип подтверждения → (модель объекта, поле в confirmations)
_SUBJECTS = {
    ConfirmationType.skill: (UserSkill, "user_skill_id"),
    ConfirmationType.experience: (WorkExperience, "work_experience_id"),
    ConfirmationType.education: (EducationRecord, "education_record_id"),
}


async def _get_for_participant(db: AsyncSession, confirmation_id: int, user: User) -> Confirmation:
    confirmation = await load_confirmation(db, confirmation_id)
    if confirmation is None:
        raise NotFoundError("Запрос на подтверждение не найден", "CONFIRMATION_NOT_FOUND")
    if user.id not in (confirmation.requester_id, confirmation.confirmer_id):
        raise ForbiddenError("Вы не участник этого запроса")
    return confirmation


async def _respond(db: AsyncSession, confirmation_id: int, user: User, new_status: ConfirmationStatus):
    confirmation = await _get_for_participant(db, confirmation_id, user)
    if confirmation.confirmer_id != user.id:
        raise ForbiddenError("Ответить на запрос может только коллега, у которого запрошено подтверждение")
    if confirmation.status != ConfirmationStatus.pending:
        raise BadRequestError("Запрос уже обработан", "CONFIRMATION_ALREADY_PROCESSED")

    # Смена статуса и пересчёт рейтинга (триггер) выполняются в одной транзакции
    confirmation.status = new_status
    db.add(confirmation)
    subject = describe_subject(confirmation)
    accepted = new_status == ConfirmationStatus.accepted
    notify(
        db,
        user_id=confirmation.requester_id,
        notification_type=NotificationType.confirmation_accepted if accepted
        else NotificationType.confirmation_rejected,
        title="Навык подтверждён" if accepted else "Запрос отклонён",
        content=f"{user.last_name} {'подтвердил' if accepted else 'отклонил'}: {subject.title}",
        confirmation_id=confirmation.id,
    )
    await db.commit()
    return await serialize_confirmations(db, [await load_confirmation(db, confirmation.id)], user)


@router.post(
    "/request",
    response_model=ApiResponse[ConfirmationRead],
    status_code=status.HTTP_201_CREATED,
    summary="Запросить подтверждение навыка/опыта/образования у коллеги",
)
async def request_confirmation(
    body: ConfirmationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    confirmer = await db.get(User, body.confirmer_id)
    if confirmer is None or not confirmer.is_active:
        raise NotFoundError("Коллега не найден", "USER_NOT_FOUND")
    if confirmer.role != UserRole.user and current_user.role == UserRole.user:
        raise BadRequestError("Подтверждение можно запросить только у ИТ-специалиста", "INVALID_CONFIRMER")

    model, field_name = _SUBJECTS[body.confirmation_type]
    subject = await db.get(model, body.subject_id)
    if subject is None:
        raise NotFoundError("Подтверждаемый объект не найден", "SUBJECT_NOT_FOUND")

    # Самоподтверждение и принадлежность объекта дополнительно проверяет триггер БД
    confirmation = Confirmation(
        requester_id=current_user.id,
        confirmer_id=body.confirmer_id,
        confirmation_type=body.confirmation_type,
        requested_level=subject.level if body.confirmation_type == ConfirmationType.skill else None,
        message=body.message,
        **{field_name: body.subject_id},
    )
    db.add(confirmation)
    await db.flush()

    loaded = await load_confirmation(db, confirmation.id)
    subject_info = describe_subject(loaded)
    level = f" {loaded.requested_level}/10" if loaded.requested_level is not None else ""
    notify(
        db,
        user_id=body.confirmer_id,
        notification_type=NotificationType.confirmation_request,
        title="Новый запрос на подтверждение",
        content=f"Коллега {current_user.last_name} запросил подтверждение "
                f"({TYPE_TITLES[body.confirmation_type]}): {subject_info.title}{level}",
        confirmation_id=confirmation.id,
    )
    await db.commit()
    result = await serialize_confirmations(db, [await load_confirmation(db, confirmation.id)], current_user)
    return ok(result[0], "Запрос на подтверждение отправлен")


@router.get(
    "",
    response_model=ApiResponse[Page[ConfirmationRead]],
    summary="Входящие и исходящие запросы и подтверждения",
)
async def list_confirmations(
    direction: Literal["all", "incoming", "outgoing"] = "all",
    status_filter: ConfirmationStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = {
        "incoming": [Confirmation.confirmer_id == current_user.id],
        "outgoing": [Confirmation.requester_id == current_user.id],
        "all": [or_(Confirmation.confirmer_id == current_user.id, Confirmation.requester_id == current_user.id)],
    }[direction]
    if status_filter is not None:
        conditions.append(Confirmation.status == status_filter)

    total = (await db.execute(select(func.count(Confirmation.id)).where(*conditions))).scalar_one()
    confirmations = (await db.execute(
        with_details(select(Confirmation).where(*conditions))
        .order_by(Confirmation.created_at.desc(), Confirmation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    items = await serialize_confirmations(db, list(confirmations), current_user)
    message = "OK" if total else "У вас нет активных запросов на подтверждение"
    return ok(make_page(items, total, page, page_size), message)


@router.get("/counters", response_model=ApiResponse[ConfirmationCounters], summary="Счётчики для badge")
async def confirmation_counters(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    incoming, outgoing, accepted = (await db.execute(
        select(
            func.count(Confirmation.id).filter(
                Confirmation.confirmer_id == current_user.id, Confirmation.status == ConfirmationStatus.pending,
            ),
            func.count(Confirmation.id).filter(
                Confirmation.requester_id == current_user.id, Confirmation.status == ConfirmationStatus.pending,
            ),
            func.count(Confirmation.id).filter(
                Confirmation.requester_id == current_user.id, Confirmation.status == ConfirmationStatus.accepted,
            ),
        )
    )).one()
    return ok(ConfirmationCounters(incoming_pending=incoming, outgoing_pending=outgoing, accepted=accepted))


@router.get("/{confirmation_id}", response_model=ApiResponse[ConfirmationRead], summary="Детали запроса")
async def get_confirmation(
    confirmation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    confirmation = await _get_for_participant(db, confirmation_id, current_user)
    return ok((await serialize_confirmations(db, [confirmation], current_user))[0])


@router.post("/{confirmation_id}/accept", response_model=ApiResponse[ConfirmationRead], summary="Подтвердить")
async def accept_confirmation(
    confirmation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await _respond(db, confirmation_id, current_user, ConfirmationStatus.accepted)
    return ok(result[0], "Навык подтверждён")


@router.post("/{confirmation_id}/reject", response_model=ApiResponse[ConfirmationRead], summary="Отклонить")
async def reject_confirmation(
    confirmation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await _respond(db, confirmation_id, current_user, ConfirmationStatus.rejected)
    return ok(result[0], "Запрос отклонён")


@router.post(
    "/{confirmation_id}/revoke",
    response_model=ApiResponse[ConfirmationRead],
    summary="Отозвать выданное подтверждение (любой участник)",
)
async def revoke_confirmation(
    confirmation_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    confirmation = await _get_for_participant(db, confirmation_id, current_user)
    if confirmation.status != ConfirmationStatus.accepted:
        raise BadRequestError("Отозвать можно только принятое подтверждение", "INVALID_STATUS_TRANSITION")
    confirmation.status = ConfirmationStatus.revoked
    db.add(confirmation)
    other_id = (
        confirmation.confirmer_id if current_user.id == confirmation.requester_id else confirmation.requester_id
    )
    notify(
        db,
        user_id=other_id,
        notification_type=NotificationType.confirmation_revoked,
        title="Подтверждение отозвано",
        content=f"{current_user.last_name} отозвал подтверждение: {describe_subject(confirmation).title}",
        confirmation_id=confirmation.id,
    )
    await db.commit()
    result = await serialize_confirmations(db, [await load_confirmation(db, confirmation.id)], current_user)
    return ok(result[0], "Подтверждение отозвано")
