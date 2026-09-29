"""Справочники для автокомплита: компании, учебные заведения, вакансии."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import Company, EducationalInstitution, User, Vacancy
from ..responses import ApiResponse, ok
from ..schemas.users import CompanyBrief, InstitutionBrief

router = APIRouter(tags=["references"])


@router.get("/companies", response_model=ApiResponse[list[CompanyBrief]], summary="Компании")
async def list_companies(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Company).order_by(Company.name).limit(limit)
    if q.strip():
        query = query.where(func.lower(Company.name).contains(q.strip().lower(), autoescape=True))
    companies = (await db.execute(query)).scalars().all()
    return ok([CompanyBrief(id=item.id, name=item.name, city=item.city) for item in companies])


@router.get("/institutions", response_model=ApiResponse[list[InstitutionBrief]], summary="Учебные заведения")
async def list_institutions(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(EducationalInstitution).order_by(EducationalInstitution.name).limit(limit)
    if q.strip():
        query = query.where(
            func.lower(EducationalInstitution.name).contains(q.strip().lower(), autoescape=True)
        )
    institutions = (await db.execute(query)).scalars().all()
    return ok([
        InstitutionBrief(id=item.id, name=item.name, institution_type=item.institution_type, city=item.city)
        for item in institutions
    ])


@router.get("/vacancies", response_model=ApiResponse[list[dict]], summary="Активные вакансии (mock-данные)")
async def list_vacancies(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Vacancy.id, Vacancy.title, Company.name)
        .join(Company, Company.id == Vacancy.company_id)
        .where(Vacancy.is_active == True)
        .order_by(Vacancy.title)
    )).all()
    return ok([{"id": vacancy_id, "title": title, "company": company} for vacancy_id, title, company in rows])
