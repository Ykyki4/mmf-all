"""Сериализация подтверждений для таймлайна «кто → когда → что подтвердил»."""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Confirmation, EducationRecord, User, UserSkill, WorkExperience
from ..models.enums import EMPLOYMENT_TYPE_TITLES, ConfirmationStatus, ConfirmationType
from ..schemas.confirmations import ConfirmationRead, ConfirmationSubject
from .profiles import user_short, verified_user_ids

STATUS_TITLES = {
    ConfirmationStatus.pending: "Ожидает",
    ConfirmationStatus.accepted: "Подтверждено",
    ConfirmationStatus.rejected: "Отклонено",
    ConfirmationStatus.revoked: "Отозвано",
}

TYPE_TITLES = {
    ConfirmationType.skill: "навык",
    ConfirmationType.experience: "опыт работы",
    ConfirmationType.education: "образование",
}


def with_details(query: Select) -> Select:
    """Подгружает участников и подтверждаемый объект одним набором запросов."""
    return query.options(
        selectinload(Confirmation.requester),
        selectinload(Confirmation.confirmer),
        selectinload(Confirmation.user_skill).selectinload(UserSkill.technology),
        selectinload(Confirmation.work_experience).selectinload(WorkExperience.company),
        selectinload(Confirmation.education_record).selectinload(EducationRecord.institution),
    )


async def load_confirmation(db: AsyncSession, confirmation_id: int) -> Confirmation | None:
    return (await db.execute(
        with_details(select(Confirmation).where(Confirmation.id == confirmation_id))
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


def describe_subject(confirmation: Confirmation) -> ConfirmationSubject:
    if confirmation.user_skill is not None:
        skill = confirmation.user_skill
        return ConfirmationSubject(
            id=skill.id,
            title=skill.technology.name,
            level=skill.level,
            description=skill.technology.description,
        )
    if confirmation.work_experience is not None:
        experience = confirmation.work_experience
        organization = (
            experience.company.name if experience.company
            else EMPLOYMENT_TYPE_TITLES.get(experience.employment_type, experience.employment_type)
        )
        period = f"{experience.start_date:%m.%Y} — {experience.end_date:%m.%Y}" if experience.end_date \
            else f"с {experience.start_date:%m.%Y}"
        return ConfirmationSubject(
            id=experience.id,
            title=f"{experience.position} в {organization}",
            description=f"{period}. {experience.description or ''}".strip(),
        )
    education = confirmation.education_record
    if education is not None:
        return ConfirmationSubject(
            id=education.id,
            title=f"{education.institution.name}" + (f", {education.specialty}" if education.specialty else ""),
            description=f"{education.start_date:%Y} — {education.end_date:%Y}" if education.end_date else None,
        )
    return ConfirmationSubject(id=0, title="Объект удалён")


def _summary(confirmation: Confirmation, subject: ConfirmationSubject, viewer_id: int) -> str:
    """Текст карточки таймлайна с точки зрения просматривающего пользователя."""
    level = confirmation.requested_level if confirmation.requested_level is not None else subject.level
    what = f"{subject.title} {level}/10" if level is not None else subject.title
    created = f"{confirmation.created_at:%d.%m.%y}"
    requester_name = confirmation.requester.last_name
    confirmer_name = confirmation.confirmer.last_name

    if confirmation.requester_id == viewer_id:
        texts = {
            ConfirmationStatus.pending: f"Вы запросили у коллеги {confirmer_name} подтверждение {what} ({created})",
            ConfirmationStatus.accepted: f"Коллега {confirmer_name} подтвердил {what}",
            ConfirmationStatus.rejected: f"Коллега {confirmer_name} отклонил запрос {what}",
            ConfirmationStatus.revoked: f"Подтверждение {what} от коллеги {confirmer_name} отозвано",
        }
    else:
        texts = {
            ConfirmationStatus.pending: f"Коллега {requester_name} запросил подтверждение {what} ({created})",
            ConfirmationStatus.accepted: f"Вы подтвердили {what} коллеге {requester_name}",
            ConfirmationStatus.rejected: f"Вы отклонили запрос {what} от коллеги {requester_name}",
            ConfirmationStatus.revoked: f"Подтверждение {what} коллеге {requester_name} отозвано",
        }
    return texts.get(confirmation.status, what)


async def serialize_confirmations(
    db: AsyncSession, confirmations: list[Confirmation], viewer: User
) -> list[ConfirmationRead]:
    participant_ids = list({c.requester_id for c in confirmations} | {c.confirmer_id for c in confirmations})
    verified = await verified_user_ids(db, participant_ids)
    result = []
    for confirmation in confirmations:
        subject = describe_subject(confirmation)
        result.append(ConfirmationRead(
            id=confirmation.id,
            direction="outgoing" if confirmation.requester_id == viewer.id else "incoming",
            confirmation_type=confirmation.confirmation_type,
            status=confirmation.status,
            status_title=STATUS_TITLES.get(confirmation.status, confirmation.status),
            subject=subject,
            requested_level=confirmation.requested_level,
            message=confirmation.message,
            requester=user_short(confirmation.requester, verified),
            confirmer=user_short(confirmation.confirmer, verified),
            summary=_summary(confirmation, subject, viewer.id),
            created_at=confirmation.created_at,
            responded_at=confirmation.responded_at,
            revoked_at=confirmation.revoked_at,
        ))
    return result
