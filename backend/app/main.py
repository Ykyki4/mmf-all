from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import settings
from .database import engine
from .errors import register_exception_handlers
from .logging_config import setup_logging
from .middleware import CsrfMiddleware, RequestLoggingMiddleware
from .responses import ApiResponse, ok
from .routers import (
    admin,
    analytics,
    auth,
    confirmations,
    dashboard,
    education,
    experience,
    exports,
    integrations,
    notifications,
    ratings,
    references,
    reports,
    shortlists,
    skills,
    users,
)

# Порядок важен: у users статические пути (/me, /search, /lookup) объявлены раньше /{user_ref}
ROUTERS = [
    auth.router,
    users.router,
    ratings.router,
    exports.router,
    education.router,
    experience.router,
    skills.router,
    confirmations.router,
    notifications.router,
    integrations.router,
    dashboard.router,
    analytics.router,
    shortlists.router,
    reports.router,
    references.router,
    admin.router,
]

OPENAPI_TAGS = [
    {"name": "auth", "description": "Аутентификация: JWT, refresh, logout, 2FA, восстановление пароля"},
    {"name": "users", "description": "Профили пользователей, поиск кандидатов"},
    {"name": "rating", "description": "Цифровой рейтинг надёжности"},
    {"name": "education", "description": "Образование (web/mobile)"},
    {"name": "experience", "description": "Опыт работы (web/mobile)"},
    {"name": "skills", "description": "Навыки и справочник технологий"},
    {"name": "confirmations", "description": "Социальная верификация («двустороннее рукопожатие»)"},
    {"name": "notifications", "description": "Уведомления"},
    {"name": "integrations", "description": "Госуслуги, GitHub, Telegram (заглушки)"},
    {"name": "exports", "description": "Экспорт профиля в PDF/JSON"},
    {"name": "dashboard", "description": "Главная страница HR (desktop)"},
    {"name": "analytics", "description": "Аналитика рынка навыков (desktop)"},
    {"name": "shortlists", "description": "Подборки кандидатов (desktop)"},
    {"name": "reports", "description": "Генератор отчётов PDF/Excel (desktop)"},
    {"name": "references", "description": "Справочники"},
    {"name": "admin", "description": "Администрирование ролей"},
    {"name": "system", "description": "Служебные эндпоинты"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    Path(settings.storage_dir, "avatars").mkdir(parents=True, exist_ok=True)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Единое REST API для desktop (HR), web и mobile клиентов системы «Цифровые рекомендации ИТ-Проф».\n\n"
        "Все ответы имеют формат `{success, data, message, error_code}`. "
        "Авторизация — заголовок `Authorization: Bearer <access_token>` (POST /auth/login)."
    ),
    openapi_tags=OPENAPI_TAGS,
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
    redoc_url=f"{settings.api_prefix}/redoc",
    lifespan=lifespan,
)

# Middleware выполняются в обратном порядке добавления: логирование — самое внешнее
app.add_middleware(CsrfMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(RequestLoggingMiddleware)

register_exception_handlers(app)

for router in ROUTERS:
    # Основной (версионированный) путь — /api/v1/...
    app.include_router(router, prefix=settings.api_prefix)
    # Совместимость с путями из ТЗ без префикса (/auth/login) — скрыты из документации
    app.include_router(router, include_in_schema=False)

app.mount("/static", StaticFiles(directory=settings.storage_dir, check_dir=False), name="static")


@app.get(f"{settings.api_prefix}/health", response_model=ApiResponse[dict], tags=["system"], summary="Проверка работы")
async def health():
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return ok({"status": "ok", "version": settings.app_version})
