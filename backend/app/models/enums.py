from enum import StrEnum


class UserRole(StrEnum):
    user = "user"              # ИТ-специалист (web/mobile)
    hr = "hr"                  # HR-менеджер (desktop)
    team_lead = "team_lead"    # руководитель команды (desktop)
    analyst = "analyst"        # аналитик (desktop)
    admin = "admin"


# Роли сотрудников компаний — доступ к поиску, подборкам, отчётам, аналитике
EMPLOYER_ROLES = (UserRole.hr, UserRole.team_lead, UserRole.analyst, UserRole.admin)
# Роли, которые могут вести подборки кандидатов
SHORTLIST_ROLES = (UserRole.hr, UserRole.team_lead, UserRole.admin)


class EducationType(StrEnum):
    higher = "higher"                  # ВУЗ
    secondary = "secondary"            # среднее профессиональное
    course = "course"                  # курсы
    certificate = "certificate"        # сертификация


class InstitutionType(StrEnum):
    university = "university"
    college = "college"
    online_school = "online_school"
    training_center = "training_center"


class EmploymentType(StrEnum):
    labor_contract = "labor_contract"                    # трудовой договор (ТК РФ)
    freelance = "freelance"                              # фриланс
    self_employed = "self_employed"                      # самозанятость
    individual_entrepreneur = "individual_entrepreneur"  # ИП
    llc_participant = "llc_participant"                  # участие в ООО
    civil_contract = "civil_contract"                    # договор ГПХ


class ConfirmationType(StrEnum):
    skill = "skill"
    experience = "experience"
    education = "education"


class ConfirmationStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revoked = "revoked"


class NotificationType(StrEnum):
    confirmation_request = "confirmation_request"
    confirmation_accepted = "confirmation_accepted"
    confirmation_rejected = "confirmation_rejected"
    confirmation_revoked = "confirmation_revoked"
    rating_change = "rating_change"
    job_match = "job_match"


class IntegrationProvider(StrEnum):
    gosuslugi = "gosuslugi"
    github = "github"
    telegram = "telegram"


class IntegrationStatus(StrEnum):
    connected = "connected"
    failed = "failed"


class AuthCodePurpose(StrEnum):
    two_factor = "two_factor"
    password_reset = "password_reset"  # noqa: S105 — назначение кода, не секрет


class ExportFormat(StrEnum):
    pdf = "pdf"
    json = "json"


class PrivacyMode(StrEnum):
    full = "full"
    anonymous = "anonymous"


EMPLOYMENT_TYPE_TITLES = {
    EmploymentType.labor_contract: "ТК РФ",
    EmploymentType.freelance: "Фриланс",
    EmploymentType.self_employed: "Самозанятость",
    EmploymentType.individual_entrepreneur: "ИП",
    EmploymentType.llc_participant: "Участие в ООО",
    EmploymentType.civil_contract: "ГПХ",
}

EDUCATION_TYPE_TITLES = {
    EducationType.higher: "ВУЗ",
    EducationType.secondary: "СПО",
    EducationType.course: "Курсы",
    EducationType.certificate: "Сертификация",
}
