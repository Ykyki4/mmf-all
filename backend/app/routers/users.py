import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_employer, get_current_user
from ..config import settings
from ..database import get_db
from ..errors import ApiError, BadRequestError, ForbiddenError
from ..models import User, UserSkill
from ..models.enums import EMPLOYER_ROLES, EmploymentType, UserRole
from ..responses import ApiResponse, Page, ok
from ..schemas.hr import CandidateSearchItem
from ..schemas.rating import Recommendations
from ..schemas.users import ProfileMode, ProfileRead, UserLookupItem, UserUpdate
from ..services.matching import get_recommendations
from ..services.profiles import (
    build_profile,
    ensure_profile_visible,
    get_user_or_404,
    parse_user_ref,
    verified_user_ids,
)
from ..services.references import resolve_technology
from ..services.search import SearchFilters, SortField, search_candidates

router = APIRouter(prefix="/users", tags=["users"])

AVATAR_CONTENT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
PAGE_SIZES = (25, 50, 100)


# ── Текущий пользователь ──────────────────────────────────────────────────────

@router.get("/me", response_model=ApiResponse[ProfileRead], summary="Текущий профиль")
async def get_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ok(await build_profile(db, current_user.id, "full"))


@router.put("/me", response_model=ApiResponse[ProfileRead], summary="Обновление профиля")
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    changes = body.model_dump(exclude_unset=True, exclude={"skills"})
    for required_field in ("email", "last_name", "first_name"):
        if required_field in changes and changes[required_field] is None:
            raise BadRequestError(f"Поле {required_field} не может быть пустым", "VALIDATION_ERROR")
    if "email" in changes:
        changes["email"] = changes["email"].lower()
    for key, value in changes.items():
        setattr(current_user, key, value)
    db.add(current_user)

    if body.skills is not None:
        await _replace_skills(db, current_user.id, body.skills)

    await db.commit()
    return ok(await build_profile(db, current_user.id, "full"), "Профиль обновлён")


async def _replace_skills(db: AsyncSession, user_id: int, skills) -> None:
    """Синхронизирует набор навыков с переданным списком (добавить/обновить/удалить)."""
    existing = {
        skill.technology_id: skill
        for skill in (await db.execute(select(UserSkill).where(UserSkill.user_id == user_id))).scalars()
    }
    keep: set[int] = set()
    for item in skills:
        if item.technology_id is None and not item.technology_name:
            raise BadRequestError("Для навыка укажите technology_id или technology_name", "VALIDATION_ERROR")
        technology = await resolve_technology(db, item.technology_id, item.technology_name)
        keep.add(technology.id)
        skill = existing.get(technology.id)
        if skill is None:
            db.add(UserSkill(user_id=user_id, technology_id=technology.id, level=item.level))
        elif skill.level != item.level:
            skill.level = item.level
            db.add(skill)
    removed = [skill.id for tech_id, skill in existing.items() if tech_id not in keep]
    if removed:
        await db.execute(delete(UserSkill).where(UserSkill.id.in_(removed)))


@router.post("/me/avatar", response_model=ApiResponse[ProfileRead], summary="Загрузка фото профиля")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    extension = AVATAR_CONTENT_TYPES.get(file.content_type or "")
    if extension is None:
        raise BadRequestError("Допустимые форматы: PNG, JPEG, WEBP", "UNSUPPORTED_FILE_TYPE")
    content = await file.read(settings.max_avatar_size_bytes + 1)
    if len(content) > settings.max_avatar_size_bytes:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Размер файла не должен превышать 2 МБ")

    avatars_dir = Path(settings.storage_dir) / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{current_user.id}_{secrets.token_hex(8)}{extension}"
    (avatars_dir / file_name).write_bytes(content)

    current_user.avatar_url = f"/static/avatars/{file_name}"
    db.add(current_user)
    await db.commit()
    return ok(await build_profile(db, current_user.id, "full"), "Фото обновлено")


@router.get(
    "/me/recommendations",
    response_model=ApiResponse[Recommendations],
    summary="Рекомендации по развитию и подходящие вакансии",
)
async def my_recommendations(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ok(await get_recommendations(db, current_user.id))


# ── Поиск ─────────────────────────────────────────────────────────────────────

@router.get(
    "/search",
    response_model=ApiResponse[Page[CandidateSearchItem]],
    summary="Поиск кандидатов (HR)",
)
async def search_users(
    skills: list[str] = Query(default=[], max_length=20, description="Технологии: ?skills=Python&skills=Docker"),
    min_level: int | None = Query(default=None, ge=0, le=10),
    max_level: int | None = Query(default=None, ge=0, le=10),
    min_experience: float | None = Query(default=None, ge=0, le=60, description="Минимальный стаж, лет"),
    max_experience: float | None = Query(default=None, ge=0, le=60),
    min_rating: float | None = Query(default=None, ge=0, le=100, description="Индекс компетенций, %"),
    max_rating: float | None = Query(default=None, ge=0, le=100),
    min_trust: float | None = Query(default=None, ge=0, le=100),
    employment_type: EmploymentType | None = None,
    candidate_id: str | None = Query(default=None, max_length=20, description="ID или публичный ID (ITP-00012)"),
    sort_by: SortField = "rating",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25),
    current_user: User = Depends(get_current_employer),
    db: AsyncSession = Depends(get_db),
):
    if page_size not in PAGE_SIZES:
        raise BadRequestError("page_size должен быть 25, 50 или 100", "VALIDATION_ERROR")
    if min_level is not None and max_level is not None and min_level > max_level:
        raise BadRequestError("Минимальный уровень больше максимального", "VALIDATION_ERROR")
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise BadRequestError("Минимальный рейтинг больше максимального", "VALIDATION_ERROR")

    # Чипы могут прийти и как ?skills=Python,Docker
    skill_names = [name.strip() for value in skills for name in value.split(",") if name.strip()]
    filters = SearchFilters(
        skills=skill_names,
        min_level=min_level,
        max_level=max_level,
        min_experience=min_experience,
        max_experience=max_experience,
        min_rating=min_rating,
        max_rating=max_rating,
        min_trust=min_trust,
        employment_type=employment_type,
        candidate_id=parse_user_ref(candidate_id, current_user) if candidate_id else None,
    )
    result = await search_candidates(db, filters, sort_by, order == "desc", page, page_size)
    return ok(result)


@router.get(
    "/lookup",
    response_model=ApiResponse[list[UserLookupItem]],
    summary="Поиск коллег по имени/email для запроса подтверждения",
)
async def lookup_users(
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pattern = f"%{q.strip().lower()}%"
    full_name = func.concat_ws(" ", User.last_name, User.first_name, User.patronymic)
    users = (await db.execute(
        select(User)
        .where(
            User.id != current_user.id,
            User.is_active == True,
            User.is_profile_public == True,
            User.role == UserRole.user,
            or_(func.lower(full_name).like(pattern), User.email.like(pattern)),
        )
        .order_by(User.last_name, User.first_name)
        .limit(limit)
    )).scalars().all()
    verified = await verified_user_ids(db, [user.id for user in users])
    return ok([
        UserLookupItem(
            id=user.id, public_id=user.public_id, full_name=user.full_name,
            avatar_url=user.avatar_url, is_verified=user.id in verified, city=user.city,
        )
        for user in users
    ])


# ── Профиль другого пользователя ─────────────────────────────────────────────

@router.get(
    "/{user_ref}",
    response_model=ApiResponse[ProfileRead],
    summary="Профиль пользователя; ?mode=employer — обезличенный профиль для работодателя",
)
async def get_user(
    user_ref: str,
    mode: ProfileMode | None = Query(default=None, description="full | public | employer"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await get_user_or_404(db, parse_user_ref(user_ref, current_user))
    is_owner = target.id == current_user.id
    is_admin = current_user.role == UserRole.admin

    if mode is None:
        if is_owner:
            mode = "full"
        elif current_user.role in EMPLOYER_ROLES and not is_admin:
            mode = "employer"
        else:
            mode = "public"
    if mode == "full" and not (is_owner or is_admin):
        raise ForbiddenError("Полный профиль доступен только владельцу", "FORBIDDEN")
    ensure_profile_visible(current_user, target)
    return ok(await build_profile(db, target.id, mode))
