"""
Заполняет БД осмысленными тестовыми данными.

    python seed_data.py            # добавить данные (упадёт, если они уже есть)
    python seed_data.py --if-empty # только если в БД ещё нет пользователей (используется в Docker)

Создаёт:
  - 30 технологий, 12 учебных заведений, 8 компаний, 10 вакансий с требованиями
  - 25 ИТ-специалистов (образование, опыт без пересечений, навыки 0–10)
  - сотрудников компаний: HR-менеджер, руководитель, аналитик, администратор
  - подтверждения (принятые/ожидающие/отклонённые), интеграции, подборки, уведомления
  - историю рейтинга за 120 дней (сам рейтинг рассчитывают триггеры БД)

Пароль всех тестовых пользователей: Password123
"""
import argparse
import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.config import settings
from app.models import (
    Company,
    Confirmation,
    EducationalInstitution,
    EducationRecord,
    Notification,
    Rating,
    RatingHistory,
    Shortlist,
    ShortlistCandidate,
    Technology,
    User,
    UserIntegration,
    UserSkill,
    Vacancy,
    VacancySkill,
    WorkExperience,
)

PASSWORD = "Password123"
NOW = datetime.now(UTC)
TODAY = NOW.date()
HISTORY_DAYS = 120

# ── Справочники ───────────────────────────────────────────────────────────────

TECHNOLOGIES = [
    ("Python", "backend", "Язык программирования общего назначения: backend, анализ данных, автоматизация"),
    ("JavaScript", "frontend", "Язык веб-разработки для браузера и Node.js"),
    ("TypeScript", "frontend", "Типизированное надмножество JavaScript"),
    ("Java", "backend", "Язык для корпоративных backend-систем на JVM"),
    ("Kotlin", "mobile", "Язык для Android и JVM-сервисов"),
    ("Go", "backend", "Компилируемый язык для высоконагруженных сервисов"),
    ("C#", "backend", "Язык платформы .NET"),
    ("Swift", "mobile", "Язык разработки под iOS"),
    ("Dart", "mobile", "Язык фреймворка Flutter"),
    ("React", "frontend", "Библиотека построения пользовательских интерфейсов"),
    ("Vue.js", "frontend", "Прогрессивный фреймворк для UI"),
    ("Angular", "frontend", "Фреймворк для SPA от Google"),
    ("Flutter", "mobile", "Кроссплатформенный UI-фреймворк"),
    ("Django", "backend", "Веб-фреймворк на Python"),
    ("FastAPI", "backend", "Асинхронный веб-фреймворк на Python"),
    ("Spring", "backend", "Фреймворк для Java-приложений"),
    ("Node.js", "backend", "Среда выполнения JavaScript на сервере"),
    ("PostgreSQL", "database", "Объектно-реляционная СУБД"),
    ("MySQL", "database", "Реляционная СУБД"),
    ("MongoDB", "database", "Документоориентированная СУБД"),
    ("Redis", "database", "Хранилище ключ-значение в памяти"),
    ("Docker", "devops", "Контейнеризация приложений"),
    ("Kubernetes", "devops", "Оркестрация контейнеров"),
    ("Git", "devops", "Система контроля версий"),
    ("Linux", "devops", "Администрирование ОС Linux"),
    ("CI/CD", "devops", "Непрерывная интеграция и доставка"),
    ("SQL", "database", "Язык запросов к реляционным БД"),
    ("Pandas", "data", "Библиотека анализа табличных данных"),
    ("Machine Learning", "data", "Методы машинного обучения"),
    ("Kafka", "backend", "Распределённый брокер сообщений"),
]

INSTITUTIONS = [
    ("Нижегородский государственный университет им. Н.И. Лобачевского", "university", "Нижний Новгород"),
    ("НИУ ВШЭ — Нижний Новгород", "university", "Нижний Новгород"),
    ("Московский государственный университет им. М.В. Ломоносова", "university", "Москва"),
    ("МГТУ им. Н.Э. Баумана", "university", "Москва"),
    ("Университет ИТМО", "university", "Санкт-Петербург"),
    ("Санкт-Петербургский государственный университет", "university", "Санкт-Петербург"),
    ("Казанский федеральный университет", "university", "Казань"),
    ("Уральский федеральный университет", "university", "Екатеринбург"),
    ("Нижегородский радиотехнический колледж", "college", "Нижний Новгород"),
    ("Онлайн-школа «Код Будущего»", "online_school", "Москва"),
    ("Учебный центр «ИТ-Практикум»", "training_center", "Нижний Новгород"),
    ("Онлайн-академия «DataPro»", "online_school", "Москва"),
]

COMPANIES = [
    ("ООО «Ромашка»", "5260123456", "Нижний Новгород", "ул. Большая Покровская, 1", "+78312000001",
     "hr@romashka.example", "romashka.example"),
    ("АО «ТехноСофт»", "7701234567", "Москва", "Ленинский пр-т, 30", "+74950000002",
     "info@technosoft.example", "technosoft.example"),
    ("ООО «Цифровые решения»", "5262345678", "Нижний Новгород", "ул. Родионова, 165", "+78312000003",
     "job@digital.example", "digital.example"),
    ("ПАО «ИнфоБанк»", "7702345678", "Москва", "ул. Тверская, 10", "+74950000004",
     "career@infobank.example", "infobank.example"),
    ("ООО «ВолгаИТ»", "5263456789", "Нижний Новгород", "ул. Минина, 24", "+78312000005",
     "hello@volgait.example", "volgait.example"),
    ("ООО «СеверДата»", "7803456789", "Санкт-Петербург", "Невский пр-т, 50", "+78120000006",
     "hr@severdata.example", "severdata.example"),
    ("АО «НейроЛаб»", "1654567890", "Казань", "ул. Баумана, 7", "+78430000007",
     "team@neurolab.example", "neurolab.example"),
    ("ООО «ОблакоСервис»", "6674567890", "Екатеринбург", "ул. Малышева, 51", "+73430000008",
     "jobs@cloudservice.example", "cloudservice.example"),
]

# Специализации: ключевые технологии с типичным диапазоном уровня
SPECIALIZATIONS = {
    "python": ("Python-разработчик", ["Python", "FastAPI", "Django", "PostgreSQL", "Docker", "Git", "Redis", "SQL"]),
    "frontend": ("Frontend-разработчик", ["JavaScript", "TypeScript", "React", "Vue.js", "Git", "Node.js"]),
    "java": ("Java-разработчик", ["Java", "Spring", "Kotlin", "PostgreSQL", "Kafka", "Docker", "Git"]),
    "mobile": ("Mobile-разработчик", ["Kotlin", "Flutter", "Dart", "Swift", "Git", "Java"]),
    "devops": ("DevOps-инженер", ["Docker", "Kubernetes", "Linux", "CI/CD", "Go", "Python", "Git"]),
    "data": ("Data Scientist", ["Python", "Pandas", "Machine Learning", "SQL", "PostgreSQL", "Git"]),
    "go": ("Go-разработчик", ["Go", "PostgreSQL", "Redis", "Kafka", "Docker", "Kubernetes", "Git"]),
}

SPECIALISTS = [
    # (фамилия, имя, отчество, email, телефон, город, специализация, опыт лет)
    ("Петров", "Иван", "Сергеевич", "user@itprof.ru", "+79101234501", "Нижний Новгород", "python", 6),
    ("Смирнова", "Анна", "Игоревна", "smirnova@itprof.ru", "+79101234502", "Нижний Новгород", "frontend", 4),
    ("Кузнецов", "Дмитрий", "Олегович", "kuznetsov@itprof.ru", "+79101234503", "Москва", "java", 9),
    ("Попова", "Мария", "Андреевна", "popova@itprof.ru", "+79101234504", "Москва", "data", 3),
    ("Васильев", "Алексей", "Николаевич", "vasiliev@itprof.ru", "+79101234505", "Казань", "devops", 7),
    ("Соколова", "Елена", "Викторовна", "sokolova@itprof.ru", "+79101234506", "Нижний Новгород", "python", 2),
    ("Михайлов", "Сергей", "Павлович", "mikhailov@itprof.ru", "+79101234507", "Санкт-Петербург", "go", 5),
    ("Новикова", "Ольга", "Дмитриевна", "novikova@itprof.ru", "+79101234508", "Екатеринбург", "mobile", 4),
    ("Фёдоров", "Андрей", "Алексеевич", "fedorov@itprof.ru", "+79101234509", "Москва", "java", 12),
    ("Морозова", "Татьяна", "Сергеевна", "morozova@itprof.ru", "+79101234510", "Нижний Новгород", "frontend", 6),
    ("Волков", "Николай", "Игоревич", "volkov@itprof.ru", "+79101234511", "Казань", "python", 8),
    ("Алексеева", "Наталья", "Олеговна", "alekseeva@itprof.ru", "+79101234512", "Москва", "data", 5),
    ("Лебедев", "Павел", "Андреевич", "lebedev@itprof.ru", "+79101234513", "Санкт-Петербург", "devops", 3),
    ("Семёнова", "Ирина", "Николаевна", "semenova@itprof.ru", "+79101234514", "Нижний Новгород", "mobile", 1),
    ("Егоров", "Максим", "Викторович", "egorov@itprof.ru", "+79101234515", "Москва", "go", 6),
    ("Павлова", "Юлия", "Александровна", "pavlova@itprof.ru", "+79101234516", "Екатеринбург", "frontend", 2),
    ("Козлов", "Артём", "Сергеевич", "kozlov@itprof.ru", "+79101234517", "Нижний Новгород", "python", 4),
    ("Степанова", "Дарья", "Павловна", "stepanova@itprof.ru", "+79101234518", "Казань", "java", 3),
    ("Николаев", "Кирилл", "Дмитриевич", "nikolaev@itprof.ru", "+79101234519", "Москва", "devops", 10),
    ("Орлова", "Светлана", "Игоревна", "orlova@itprof.ru", "+79101234520", "Санкт-Петербург", "data", 7),
    ("Андреев", "Роман", "Олегович", "andreev@itprof.ru", "+79101234521", "Нижний Новгород", "frontend", 5),
    ("Макарова", "Виктория", "Андреевна", "makarova@itprof.ru", "+79101234522", "Москва", "python", 1),
    ("Захаров", "Евгений", "Николаевич", "zakharov@itprof.ru", "+79101234523", "Екатеринбург", "mobile", 8),
    ("Зайцева", "Ксения", "Сергеевна", "zaitseva@itprof.ru", "+79101234524", "Нижний Новгород", "go", 2),
    ("Белов", "Григорий", "Павлович", "belov@itprof.ru", "+79101234525", "Казань", "python", 11),
]

STAFF = [
    # (фамилия, имя, отчество, email, роль, компания)
    ("Иванова", "Марина", "Александровна", "hr@itprof.ru", "hr", "ООО «Ромашка»"),
    ("Григорьев", "Олег", "Витальевич", "hr2@itprof.ru", "hr", "АО «ТехноСофт»"),
    ("Сидоров", "Виталий", "Петрович", "lead@itprof.ru", "team_lead", "ООО «Ромашка»"),
    ("Крылова", "Алина", "Максимовна", "analyst@itprof.ru", "analyst", "ООО «Цифровые решения»"),
    ("Администратов", "Пётр", "Ильич", "admin@itprof.ru", "admin", None),
]

VACANCIES = [
    ("Middle Python Developer", "ООО «Ромашка»", {"Python": 7, "FastAPI": 6, "PostgreSQL": 6, "Docker": 5}),
    ("Senior Python Developer", "АО «ТехноСофт»", {"Python": 9, "Django": 7, "PostgreSQL": 7, "Docker": 8, "Redis": 6}),
    ("Frontend Developer", "ООО «Цифровые решения»", {"JavaScript": 7, "TypeScript": 6, "React": 7, "Git": 5}),
    ("Java Backend Developer", "ПАО «ИнфоБанк»", {"Java": 8, "Spring": 7, "PostgreSQL": 6, "Kafka": 5}),
    ("DevOps Engineer", "ООО «ОблакоСервис»", {"Docker": 8, "Kubernetes": 7, "Linux": 7, "CI/CD": 7}),
    ("Data Scientist", "АО «НейроЛаб»", {"Python": 7, "Pandas": 7, "Machine Learning": 7, "SQL": 6}),
    ("Android Developer", "ООО «ВолгаИТ»", {"Kotlin": 7, "Java": 5, "Git": 5}),
    ("Flutter Developer", "ООО «ВолгаИТ»", {"Flutter": 7, "Dart": 7, "Git": 5}),
    ("Go Developer", "ООО «СеверДата»", {"Go": 7, "PostgreSQL": 6, "Kafka": 5, "Docker": 6}),
    ("Junior Frontend Developer", "ООО «Ромашка»", {"JavaScript": 5, "React": 4, "Git": 4}),
]

POSITIONS_BY_LEVEL = ["Junior", "Middle", "Senior", "Lead"]
EMPLOYMENT_TYPES_WEIGHTED = ["labor_contract"] * 6 + ["freelance", "self_employed", "civil_contract",
                                                      "individual_entrepreneur"]


# ── Генерация ─────────────────────────────────────────────────────────────────

def ago(days: float, hours: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours)


def build_experience(user_id: int, total_years: int, title: str, companies: list[Company]) -> list[WorkExperience]:
    """Последовательные непересекающиеся периоды работы, последний — текущий."""
    total_days = int(total_years * 365.25)
    jobs = 1 if total_years <= 2 else random.randint(2, min(4, total_years))
    segment = total_days // jobs
    start = TODAY - timedelta(days=total_days)
    records = []
    for index in range(jobs):
        is_last = index == jobs - 1
        end = None if is_last else start + timedelta(days=segment - random.randint(10, 40))
        employment_type = "labor_contract" if is_last else random.choice(EMPLOYMENT_TYPES_WEIGHTED)
        company = random.choice(companies) if employment_type in ("labor_contract", "civil_contract") else None
        grade = POSITIONS_BY_LEVEL[min(3, index + (1 if total_years >= 5 else 0))]
        records.append(WorkExperience(
            user_id=user_id,
            company_id=company.id if company else None,
            employment_type=employment_type,
            position=f"{grade} {title.split('-')[0].strip()}",
            start_date=start,
            end_date=end,
            description="Разработка и поддержка продуктовых сервисов, участие в код-ревью и планировании.",
        ))
        if end:
            start = end + timedelta(days=random.randint(1, 20))
    return records


def build_education(user_id: int, total_years: int, institutions: list[EducationalInstitution]) -> list:
    universities = [item for item in institutions if item.institution_type == "university"]
    courses = [item for item in institutions if item.institution_type in ("online_school", "training_center")]
    graduated = TODAY - timedelta(days=int(total_years * 365.25) + random.randint(30, 200))
    records = [EducationRecord(
        user_id=user_id,
        institution_id=random.choice(universities).id,
        education_type="higher",
        specialty=random.choice(["Прикладная информатика", "Программная инженерия",
                                 "Информатика и вычислительная техника", "Прикладная математика"]),
        qualification=random.choice(["Бакалавр", "Магистр", "Специалист"]),
        start_date=graduated - timedelta(days=4 * 365),
        end_date=graduated,
        result=f"Средний балл {random.choice(['4.2', '4.5', '4.7', '4.9'])}",
        document_number=f"1052{random.randint(10**7, 10**8 - 1)}",
        sort_order=0,
    )]
    if total_years >= 7:
        # Магистратура после бакалавриата
        records.append(EducationRecord(
            user_id=user_id,
            institution_id=random.choice(universities).id,
            education_type="higher",
            specialty="Программная инженерия",
            qualification="Магистр",
            start_date=graduated + timedelta(days=60),
            end_date=graduated + timedelta(days=60 + 2 * 365),
            result="Диплом с отличием",
            sort_order=1,
        ))
    if random.random() < 0.6:
        course_start = TODAY - timedelta(days=random.randint(120, 900))
        records.append(EducationRecord(
            user_id=user_id,
            institution_id=random.choice(courses).id,
            education_type=random.choice(["course", "certificate"]),
            specialty=random.choice(["Продвинутый Python", "DevOps-практики", "Архитектура микросервисов",
                                     "Анализ данных", "Современный frontend"]),
            start_date=course_start,
            end_date=course_start + timedelta(days=random.randint(60, 180)),
            result="Сертификат с отличием",
            sort_order=len(records),
        ))
    return records


async def seed(session: AsyncSession) -> None:
    random.seed(2026)
    password_hash = hash_password(PASSWORD)

    technologies = {name: Technology(name=name, category=category, description=description)
                    for name, category, description in TECHNOLOGIES}
    institutions = [EducationalInstitution(name=name, institution_type=kind, city=city)
                    for name, kind, city in INSTITUTIONS]
    companies = {name: Company(name=name, inn=inn, city=city, address=address, phone=phone, email=email,
                               website=site)
                 for name, inn, city, address, phone, email, site in COMPANIES}
    session.add_all([*technologies.values(), *institutions, *companies.values()])
    await session.flush()

    # Вакансии
    for title, company_name, requirements in VACANCIES:
        vacancy = Vacancy(company_id=companies[company_name].id, title=title,
                          description=f"Вакансия «{title}» в компании {company_name}", created_at=ago(30))
        session.add(vacancy)
        await session.flush()
        session.add_all([
            VacancySkill(vacancy_id=vacancy.id, technology_id=technologies[name].id, required_level=level)
            for name, level in requirements.items()
        ])

    # Сотрудники компаний
    staff: dict[str, User] = {}
    for last, first, middle, email, role, company_name in STAFF:
        user = User(email=email, last_name=last, first_name=first, patronymic=middle, role=role,
                    hashed_password=password_hash, created_at=ago(300),
                    employer_company_id=companies[company_name].id if company_name else None,
                    last_seen_at=ago(0, 1))
        session.add(user)
        staff[email] = user
    await session.flush()

    # Специалисты
    specialists: list[User] = []
    specialist_skills: dict[int, list[UserSkill]] = {}
    for index, (last, first, middle, email, phone, city, spec, years) in enumerate(SPECIALISTS):
        title, stack = SPECIALIZATIONS[spec]
        registered = ago(2 if index in (5, 21) else random.randint(130, 400), random.randint(0, 12))
        if index == 21:
            registered = ago(0, 5)  # новый профиль за последние сутки
        user = User(
            email=email, phone=phone, last_name=last, first_name=first, patronymic=middle, city=city,
            role="user", hashed_password=password_hash, created_at=registered,
            about=f"{title}, опыт {years} лет. Открыт к интересным проектам.",
            is_profile_public=index != 24,  # один закрытый профиль — для проверки 403
            two_factor_enabled=email == "zakharov@itprof.ru",
            last_seen_at=ago(random.choice([0, 0, 1, 3, 10, 30]), random.randint(0, 20)),
        )
        session.add(user)
        await session.flush()
        specialists.append(user)

        session.add_all(build_education(user.id, years, institutions))
        for record in build_experience(user.id, years, title, list(companies.values())):
            session.add(record)
            await session.flush()  # по одной записи — триггер непересечения видит предыдущие

        base_level = min(9, 3 + years // 2)
        chosen = stack[:random.randint(4, len(stack))]
        extra = random.sample([name for name in technologies if name not in chosen], k=random.randint(0, 2))
        skills = []
        for position, name in enumerate(chosen + extra):
            level = max(1, min(10, base_level + random.randint(-2, 2) - (position // 3) - (2 if name in extra else 0)))
            # Навык добавлен после регистрации, но не раньше чем 120 дней назад
            added_after = max(registered, ago(HISTORY_DAYS))
            skills.append(UserSkill(user_id=user.id, technology_id=technologies[name].id, level=level,
                                    created_at=added_after + (NOW - added_after) * random.random()))
        session.add_all(skills)
        await session.flush()
        specialist_skills[user.id] = skills

    # Интеграции: Госуслуги у ~60% (их подтверждения считаются верифицированными)
    for user in specialists:
        if random.random() < 0.6:
            session.add(UserIntegration(user_id=user.id, provider="gosuslugi", status="connected",
                                        verified_at=ago(random.randint(10, 100))))
        if random.random() < 0.35:
            session.add(UserIntegration(user_id=user.id, provider="github", status="connected",
                                        external_username=user.email.split("@")[0],
                                        repositories_count=random.randint(3, 60), verified_at=ago(20)))
    await session.flush()

    # Подтверждения навыков от коллег
    for user in specialists:
        colleagues = [other for other in specialists if other.id != user.id]
        skills = specialist_skills[user.id]
        years = SPECIALISTS[specialists.index(user)][7]
        # Опытных специалистов подтверждает больше коллег — у них рейтинг выше 80%
        count = random.randint(9, 12) if years >= 7 else random.randint(1, 5)
        pairs = random.sample([(skill, other) for skill in skills for other in colleagues], k=count)
        for skill, confirmer in pairs:
            days = random.choice([0, 1, 3, 7, 15, 30, 45, 60, 90])
            created = ago(days + 1, random.randint(1, 10))
            status = random.choices(["accepted", "rejected"], weights=[85, 15])[0]
            session.add(Confirmation(
                requester_id=user.id, confirmer_id=confirmer.id, confirmation_type="skill",
                user_skill_id=skill.id, requested_level=skill.level, status=status,
                created_at=created, responded_at=ago(days, random.randint(0, 5)),
            ))
        await session.flush()

    # Демо-пользователь: входящие и исходящие ожидающие запросы
    demo = specialists[0]
    for requester in specialists[1:4]:
        skill = specialist_skills[requester.id][0]
        session.add(Confirmation(
            requester_id=requester.id, confirmer_id=demo.id, confirmation_type="skill",
            user_skill_id=skill.id, requested_level=skill.level, status="pending", created_at=ago(0, 3),
            message="Работали вместе над проектом, подтверди, пожалуйста",
        ))
    for confirmer in specialists[4:6]:
        skill = specialist_skills[demo.id][-1]
        session.add(Confirmation(
            requester_id=demo.id, confirmer_id=confirmer.id, confirmation_type="skill",
            user_skill_id=skill.id, requested_level=skill.level, status="pending", created_at=ago(1),
        ))
    await session.flush()

    # Подборки HR
    hr = staff["hr@itprof.ru"]
    python_vacancy = (await session.execute(
        select(Vacancy).where(Vacancy.title == "Middle Python Developer")
    )).scalar_one()
    shortlist_python = Shortlist(owner_id=hr.id, name="Python-разработчики", vacancy_id=python_vacancy.id,
                                 description="Кандидаты на вакансию Middle Python Developer", created_at=ago(14))
    shortlist_frontend = Shortlist(owner_id=hr.id, name="Frontend Q4", created_at=ago(5))
    session.add_all([shortlist_python, shortlist_frontend])
    await session.flush()
    for user in specialists:
        spec = SPECIALISTS[specialists.index(user)][6]
        if spec == "python":
            session.add(ShortlistCandidate(shortlist_id=shortlist_python.id, candidate_id=user.id, added_at=ago(10)))
        elif spec == "frontend":
            session.add(ShortlistCandidate(shortlist_id=shortlist_frontend.id, candidate_id=user.id, added_at=ago(3)))
    await session.flush()

    await rebuild_rating_history(session, specialists)
    await seed_notifications(session, demo, python_vacancy)


async def rebuild_rating_history(session: AsyncSession, specialists: list[User]) -> None:
    """Триггеры записали историю «на сейчас». Для графиков динамики заменяем её
    правдоподобной историей за 120 дней, которая заканчивается текущим рейтингом."""
    ids = [user.id for user in specialists]
    await session.execute(delete(RatingHistory).where(RatingHistory.user_id.in_(ids)))
    await session.execute(delete(Notification).where(Notification.notification_type == "rating_change"))
    ratings = {rating.user_id: rating for rating in (await session.execute(
        select(Rating).where(Rating.user_id.in_(ids))
    )).scalars()}
    reasons = ["experience_changed", "education_changed", "confirmation_accepted", "confirmation_accepted",
               "integration_changed"]
    for user in specialists:
        rating = ratings[user.id]
        start_share = random.uniform(0.55, 0.85)
        registered_days = min(HISTORY_DAYS, (NOW - user.created_at).days)
        points = sorted(random.sample(range(1, max(2, registered_days)), k=min(8, max(1, registered_days - 1))),
                        reverse=True)
        for step, days in enumerate(points):
            share = start_share + (1 - start_share) * step / max(1, len(points))
            session.add(RatingHistory(
                user_id=user.id,
                competency_index=round(rating.competency_index * share, 2),
                trust_level=round(rating.trust_level * min(1.0, share + 0.1), 2),
                education_score=rating.education_score,
                experience_score=round(rating.experience_score * min(1.0, share + 0.1), 2),
                confirmation_score=round(rating.confirmation_score * share, 2),
                reason="registration" if step == 0 else random.choice(reasons),
                recorded_at=ago(days, random.randint(0, 12)),
            ))
        session.add(RatingHistory(
            user_id=user.id, competency_index=rating.competency_index, trust_level=rating.trust_level,
            education_score=rating.education_score, experience_score=rating.experience_score,
            confirmation_score=rating.confirmation_score, reason="confirmation_accepted", recorded_at=NOW,
        ))
    await session.flush()


async def seed_notifications(session: AsyncSession, demo: User, vacancy: Vacancy) -> None:
    session.add_all([
        Notification(user_id=demo.id, notification_type="job_match", title="Совпадение с вакансией",
                     content=f"Ваш профиль на 89% соответствует вакансии «{vacancy.title}»",
                     vacancy_id=vacancy.id, created_at=ago(0, 6)),
        Notification(user_id=demo.id, notification_type="rating_change", title="Рейтинг изменён",
                     content="Индекс компетенций вырос на 2.4 пункта после нового подтверждения",
                     created_at=ago(1)),
        Notification(user_id=demo.id, notification_type="job_match", title="Новая подходящая вакансия",
                     content="Senior Python Developer — 74% соответствие", is_read=True, created_at=ago(3)),
    ])
    await session.flush()


async def main(if_empty: bool) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        users_count = (await session.execute(select(func.count(User.id)))).scalar_one()
        if users_count and if_empty:
            print(f"[skip] В БД уже есть пользователи ({users_count}) — тестовые данные не загружаются")
            await engine.dispose()
            return
        await seed(session)
        await session.commit()
        # Синхронизация последовательностей не требуется: id выдаёт БД
        total = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()
        print(f"[ok] Тестовые данные загружены: пользователей {total}. Пароль: {PASSWORD}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Загрузка тестовых данных")
    parser.add_argument("--if-empty", action="store_true", help="Только если в БД нет пользователей")
    asyncio.run(main(parser.parse_args().if_empty))
