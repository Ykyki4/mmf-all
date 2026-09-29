"""Все таблицы регистрируются в SQLModel.metadata при импорте этого пакета."""

from .confirmations import Confirmation
from .hr import Shortlist, ShortlistCandidate, Vacancy, VacancySkill
from .profile import Company, EducationalInstitution, EducationRecord, Technology, UserSkill, WorkExperience
from .rating import Rating, RatingHistory
from .system import ApiRequestLog, Notification, ProfileExport, UserIntegration
from .users import AuthCode, RefreshToken, Role, RoleHistory, User

__all__ = [
    "ApiRequestLog",
    "AuthCode",
    "Company",
    "Confirmation",
    "EducationRecord",
    "EducationalInstitution",
    "Notification",
    "ProfileExport",
    "Rating",
    "RatingHistory",
    "RefreshToken",
    "Role",
    "RoleHistory",
    "Shortlist",
    "ShortlistCandidate",
    "Technology",
    "User",
    "UserIntegration",
    "UserSkill",
    "Vacancy",
    "VacancySkill",
    "WorkExperience",
]
