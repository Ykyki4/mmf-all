from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_recruiter
from ..database import get_db
from ..errors import BadRequestError, ForbiddenError, NotFoundError
from ..models import Rating, Shortlist, ShortlistCandidate, Technology, User, UserSkill, Vacancy
from ..models.enums import UserRole
from ..responses import ApiResponse, make_page, ok
from ..schemas.hr import (
    AddToShortlists,
    AddToShortlistsResult,
    ShortlistCandidateAdd,
    ShortlistCandidateRead,
    ShortlistCreate,
    ShortlistDetail,
    ShortlistRead,
    ShortlistUpdate,
)
from ..services.rating import rating_changes, trend_of

router = APIRouter(prefix="/shortlists", tags=["shortlists"])

TOP_SKILLS_PER_CANDIDATE = 3


# ── Вспомогательные функции ───────────────────────────────────────────────────

async def _get_own_shortlist(db: AsyncSession, shortlist_id: int, user: User) -> Shortlist:
    shortlist = await db.get(Shortlist, shortlist_id)
    if shortlist is None:
        raise NotFoundError("Подборка не найдена", "SHORTLIST_NOT_FOUND")
    if shortlist.owner_id != user.id and user.role != UserRole.admin:
        raise ForbiddenError("Подборка принадлежит другому сотруднику")
    return shortlist


async def _ensure_candidate(db: AsyncSession, candidate_id: int) -> None:
    candidate = await db.get(User, candidate_id)
    if candidate is None or not candidate.is_active or candidate.role != UserRole.user:
        raise NotFoundError("Кандидат не найден", "USER_NOT_FOUND")


async def _ensure_vacancy(db: AsyncSession, vacancy_id: int | None) -> None:
    if vacancy_id is not None and await db.get(Vacancy, vacancy_id) is None:
        raise NotFoundError("Вакансия не найдена", "VACANCY_NOT_FOUND")


async def _counts(db: AsyncSession, shortlist_ids: list[int]) -> dict[int, int]:
    if not shortlist_ids:
        return {}
    rows = await db.execute(
        select(ShortlistCandidate.shortlist_id, func.count(ShortlistCandidate.id))
        .where(ShortlistCandidate.shortlist_id.in_(shortlist_ids))
        .group_by(ShortlistCandidate.shortlist_id)
    )
    return dict(rows.all())


def _shortlist_read(shortlist: Shortlist, count: int, contains: bool | None = None) -> ShortlistRead:
    return ShortlistRead(
        id=shortlist.id,
        name=shortlist.name,
        description=shortlist.description,
        vacancy_id=shortlist.vacancy_id,
        candidates_count=count,
        created_at=shortlist.created_at,
        updated_at=shortlist.updated_at,
        contains_candidate=contains,
    )


# ── Подборки ──────────────────────────────────────────────────────────────────

@router.get("", response_model=ApiResponse[list[ShortlistRead]], summary="Подборки текущего сотрудника")
async def list_shortlists(
    candidate_id: int | None = Query(default=None, gt=0, description="Отметить подборки с этим кандидатом"),
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    shortlists = (await db.execute(
        select(Shortlist).where(Shortlist.owner_id == current_user.id).order_by(Shortlist.created_at.desc())
    )).scalars().all()
    counts = await _counts(db, [item.id for item in shortlists])

    containing: set[int] = set()
    if candidate_id is not None:
        containing = set((await db.execute(
            select(ShortlistCandidate.shortlist_id).where(ShortlistCandidate.candidate_id == candidate_id)
        )).scalars().all())
    return ok([
        _shortlist_read(item, counts.get(item.id, 0), item.id in containing if candidate_id else None)
        for item in shortlists
    ])


@router.post(
    "",
    response_model=ApiResponse[ShortlistRead],
    status_code=status.HTTP_201_CREATED,
    summary="Создать подборку",
)
async def create_shortlist(
    body: ShortlistCreate,
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_vacancy(db, body.vacancy_id)
    shortlist = Shortlist(owner_id=current_user.id, **body.model_dump())
    db.add(shortlist)
    await db.commit()  # одинаковое имя у одного владельца → 400 DUPLICATE
    return ok(_shortlist_read(shortlist, 0), "Подборка создана")


@router.post(
    "/candidates",
    response_model=ApiResponse[AddToShortlistsResult],
    summary="Добавить кандидата в несколько подборок",
)
async def add_to_shortlists(
    body: AddToShortlists,
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_candidate(db, body.candidate_id)
    shortlist_ids = list(dict.fromkeys(body.shortlist_ids))
    for shortlist_id in shortlist_ids:
        await _get_own_shortlist(db, shortlist_id, current_user)

    already = set((await db.execute(
        select(ShortlistCandidate.shortlist_id).where(
            ShortlistCandidate.candidate_id == body.candidate_id,
            ShortlistCandidate.shortlist_id.in_(shortlist_ids),
        )
    )).scalars().all())
    added = [shortlist_id for shortlist_id in shortlist_ids if shortlist_id not in already]
    for shortlist_id in added:
        db.add(ShortlistCandidate(shortlist_id=shortlist_id, candidate_id=body.candidate_id))
    await db.commit()
    return ok(
        AddToShortlistsResult(added_to=added, already_in=sorted(already)),
        f"Кандидат добавлен в подборки: {len(added)}",
    )


@router.get("/{shortlist_id}", response_model=ApiResponse[ShortlistDetail], summary="Подборка и её кандидаты")
async def get_shortlist(
    shortlist_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    shortlist = await _get_own_shortlist(db, shortlist_id, current_user)
    total = (await _counts(db, [shortlist.id])).get(shortlist.id, 0)

    rows = (await db.execute(
        select(ShortlistCandidate, Rating)
        .join(Rating, Rating.user_id == ShortlistCandidate.candidate_id)
        .where(ShortlistCandidate.shortlist_id == shortlist.id)
        .order_by(ShortlistCandidate.added_at, ShortlistCandidate.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()
    candidate_ids = [item.candidate_id for item, _ in rows]
    changes = await rating_changes(db, candidate_ids)

    skills: dict[int, list[str]] = {}
    if candidate_ids:
        skill_rows = (await db.execute(
            select(UserSkill.user_id, Technology.name)
            .join(Technology, Technology.id == UserSkill.technology_id)
            .where(UserSkill.user_id.in_(candidate_ids))
            .order_by(UserSkill.level.desc(), Technology.name)
        )).all()
        for user_id, name in skill_rows:
            user_skills = skills.setdefault(user_id, [])
            if len(user_skills) < TOP_SKILLS_PER_CANDIDATE:
                user_skills.append(name)

    candidates = [
        ShortlistCandidateRead(
            candidate_id=item.candidate_id,
            public_id=f"ITP-{item.candidate_id:05d}",
            competency_index=rating.competency_index,
            trust_level=rating.trust_level,
            trend=trend_of(changes.get(item.candidate_id, 0.0)),
            top_skills=skills.get(item.candidate_id, []),
            note=item.note,
            added_at=item.added_at,
        )
        for item, rating in rows
    ]
    detail = ShortlistDetail(
        **_shortlist_read(shortlist, total).model_dump(),
        candidates=make_page(candidates, total, page, page_size),
    )
    return ok(detail)


@router.put("/{shortlist_id}", response_model=ApiResponse[ShortlistRead], summary="Переименовать / изменить подборку")
async def update_shortlist(
    shortlist_id: int,
    body: ShortlistUpdate,
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    shortlist = await _get_own_shortlist(db, shortlist_id, current_user)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and not changes["name"]:
        raise BadRequestError("Название подборки не может быть пустым", "VALIDATION_ERROR")
    await _ensure_vacancy(db, changes.get("vacancy_id"))
    for key, value in changes.items():
        setattr(shortlist, key, value)
    db.add(shortlist)
    await db.commit()
    await db.refresh(shortlist)
    count = (await _counts(db, [shortlist.id])).get(shortlist.id, 0)
    return ok(_shortlist_read(shortlist, count), "Подборка обновлена")


@router.delete("/{shortlist_id}", response_model=ApiResponse[None], summary="Удалить подборку")
async def delete_shortlist(
    shortlist_id: int,
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    shortlist = await _get_own_shortlist(db, shortlist_id, current_user)
    await db.execute(delete(Shortlist).where(Shortlist.id == shortlist.id))  # кандидаты — ON DELETE CASCADE
    await db.commit()
    return ok(None, "Подборка удалена")


# ── Кандидаты подборки ────────────────────────────────────────────────────────

@router.post(
    "/{shortlist_id}/candidates",
    response_model=ApiResponse[ShortlistRead],
    status_code=status.HTTP_201_CREATED,
    summary="Добавить кандидата в подборку",
)
async def add_candidate(
    shortlist_id: int,
    body: ShortlistCandidateAdd,
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    shortlist = await _get_own_shortlist(db, shortlist_id, current_user)
    await _ensure_candidate(db, body.candidate_id)
    db.add(ShortlistCandidate(shortlist_id=shortlist.id, candidate_id=body.candidate_id, note=body.note))
    await db.commit()  # повторное добавление → 400 DUPLICATE
    count = (await _counts(db, [shortlist.id])).get(shortlist.id, 0)
    return ok(_shortlist_read(shortlist, count), "Кандидат добавлен в подборку")


@router.delete(
    "/{shortlist_id}/candidates/{candidate_id}",
    response_model=ApiResponse[ShortlistRead],
    summary="Удалить кандидата из подборки",
)
async def remove_candidate(
    shortlist_id: int,
    candidate_id: int,
    current_user: User = Depends(get_current_recruiter),
    db: AsyncSession = Depends(get_db),
):
    shortlist = await _get_own_shortlist(db, shortlist_id, current_user)
    result = await db.execute(
        delete(ShortlistCandidate).where(
            ShortlistCandidate.shortlist_id == shortlist.id, ShortlistCandidate.candidate_id == candidate_id,
        )
    )
    if result.rowcount == 0:
        raise NotFoundError("Кандидата нет в подборке", "CANDIDATE_NOT_IN_SHORTLIST")
    await db.commit()
    count = (await _counts(db, [shortlist.id])).get(shortlist.id, 0)
    return ok(_shortlist_read(shortlist, count), "Кандидат удалён из подборки")
