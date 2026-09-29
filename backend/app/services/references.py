"""Справочники: поиск по id или названию с добавлением новых записей."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import NotFoundError
from ..models import Company, EducationalInstitution, Technology
from ..models.enums import EducationType, InstitutionType

_INSTITUTION_TYPE_BY_EDUCATION = {
    EducationType.higher: InstitutionType.university,
    EducationType.secondary: InstitutionType.college,
    EducationType.course: InstitutionType.online_school,
    EducationType.certificate: InstitutionType.training_center,
}


async def resolve_institution(
    db: AsyncSession, institution_id: int | None, name: str | None, education_type: str
) -> EducationalInstitution:
    if institution_id is not None:
        institution = await db.get(EducationalInstitution, institution_id)
        if institution is None:
            raise NotFoundError("Учебное заведение не найдено", "INSTITUTION_NOT_FOUND")
        return institution
    institution = (await db.execute(
        select(EducationalInstitution).where(func.lower(EducationalInstitution.name) == name.lower())
    )).scalar_one_or_none()
    if institution is None:
        institution = EducationalInstitution(
            name=name,
            institution_type=_INSTITUTION_TYPE_BY_EDUCATION.get(education_type, InstitutionType.university),
        )
        db.add(institution)
        await db.flush()
    return institution


async def resolve_company(db: AsyncSession, company_id: int | None, name: str | None) -> Company | None:
    if company_id is not None:
        company = await db.get(Company, company_id)
        if company is None:
            raise NotFoundError("Компания не найдена", "COMPANY_NOT_FOUND")
        return company
    if not name:
        return None
    company = (await db.execute(
        select(Company).where(func.lower(Company.name) == name.lower())
    )).scalar_one_or_none()
    if company is None:
        company = Company(name=name)
        db.add(company)
        await db.flush()
    return company


async def resolve_technology(db: AsyncSession, technology_id: int | None, name: str | None) -> Technology:
    if technology_id is not None:
        technology = await db.get(Technology, technology_id)
        if technology is None:
            raise NotFoundError("Технология не найдена", "TECHNOLOGY_NOT_FOUND")
        return technology
    technology = (await db.execute(
        select(Technology).where(func.lower(Technology.name) == name.lower())
    )).scalar_one_or_none()
    if technology is None:
        technology = Technology(name=name)
        db.add(technology)
        await db.flush()
    return technology
