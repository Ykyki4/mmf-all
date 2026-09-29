from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user
from ..database import get_db
from ..models import Technology, User, UserSkill
from ..responses import ApiResponse, ok
from ..schemas.profile import SkillCreate, SkillUpdate, TechnologyRead, TopSkill
from ..schemas.users import SkillRead
from ..services.analytics import top_skills
from ..services.ownership import get_owned
from ..services.profiles import skill_read
from ..services.references import resolve_technology

router = APIRouter(prefix="/skills", tags=["skills"])

_NOT_FOUND = "Навык не найден"


async def _load(db: AsyncSession, skill_id: int) -> UserSkill:
    return (await db.execute(
        select(UserSkill)
        .where(UserSkill.id == skill_id)
        .options(selectinload(UserSkill.technology))
        .execution_options(populate_existing=True)
    )).scalar_one()


@router.get("", response_model=ApiResponse[list[SkillRead]], summary="Навыки текущего пользователя")
async def list_skills(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    skills = (await db.execute(
        select(UserSkill)
        .where(UserSkill.user_id == current_user.id)
        .options(selectinload(UserSkill.technology))
        .order_by(UserSkill.level.desc())
    )).scalars().all()
    return ok([skill_read(skill) for skill in skills])


@router.get(
    "/suggest",
    response_model=ApiResponse[list[TechnologyRead]],
    summary="Автокомплит технологий (популярные — первыми)",
)
async def suggest_skills(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query_text = q.strip().lower()
    specialists = func.count(func.distinct(UserSkill.user_id))
    query = (
        select(Technology, specialists.label("specialists"))
        .outerjoin(UserSkill, UserSkill.technology_id == Technology.id)
        .group_by(Technology.id)
    )
    if query_text:
        name = func.lower(Technology.name)
        query = query.where(name.contains(query_text, autoescape=True)).order_by(
            # Совпадение с начала названия — выше
            case((name.startswith(query_text, autoescape=True), 0), else_=1),
            specialists.desc(),
            Technology.name,
        )
    else:
        query = query.order_by(specialists.desc(), Technology.name)

    rows = (await db.execute(query.limit(limit))).all()
    return ok([
        TechnologyRead(
            id=technology.id, name=technology.name, category=technology.category,
            description=technology.description, specialists_count=count,
        )
        for technology, count in rows
    ])


@router.get("/top", response_model=ApiResponse[list[TopSkill]], summary="Топ технологий по числу специалистов")
async def get_top_skills(
    limit: int = Query(default=5, ge=1, le=20),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await top_skills(db, limit)
    return ok(result, "OK" if result else "Нет данных")


@router.post(
    "",
    response_model=ApiResponse[SkillRead],
    status_code=status.HTTP_201_CREATED,
    summary="Добавить навык (skill_id/technology_id или technology_name, уровень 0–10)",
)
async def create_skill(
    body: SkillCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    technology = await resolve_technology(db, body.technology_id, body.technology_name)
    skill = UserSkill(user_id=current_user.id, technology_id=technology.id, level=body.level)
    db.add(skill)
    await db.commit()  # повтор технологии → 400 DUPLICATE (UNIQUE в БД)
    return ok(skill_read(await _load(db, skill.id)), "Навык добавлен")


@router.put("/{skill_id}", response_model=ApiResponse[SkillRead], summary="Изменить уровень навыка")
async def update_skill(
    skill_id: int,
    body: SkillUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = await get_owned(db, UserSkill, skill_id, current_user.id, _NOT_FOUND)
    skill.level = body.level
    db.add(skill)
    await db.commit()
    return ok(skill_read(await _load(db, skill.id)), "Уровень навыка обновлён")


@router.delete("/{skill_id}", response_model=ApiResponse[None], summary="Удалить навык")
async def delete_skill(
    skill_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = await get_owned(db, UserSkill, skill_id, current_user.id, _NOT_FOUND)
    await db.delete(skill)
    await db.commit()
    return ok(None, "Навык удалён")

