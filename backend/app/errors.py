import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .responses import error_body

logger = logging.getLogger("itprof.errors")


# ── Исключения приложения ─────────────────────────────────────────────────────

class ApiError(StarletteHTTPException):
    """HTTP-ошибка с машиночитаемым error_code для единого формата ответа."""

    def __init__(self, status_code: int, error_code: str, message: str, data: Any = None):
        super().__init__(status_code=status_code, detail=message)
        self.error_code = error_code
        self.message = message
        self.data = data


class BadRequestError(ApiError):
    def __init__(self, message: str, error_code: str = "BAD_REQUEST", data: Any = None):
        super().__init__(status.HTTP_400_BAD_REQUEST, error_code, message, data)


class UnauthorizedError(ApiError):
    def __init__(self, message: str = "Требуется авторизация", error_code: str = "UNAUTHORIZED"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, error_code, message)


class ForbiddenError(ApiError):
    def __init__(self, message: str = "Недостаточно прав", error_code: str = "FORBIDDEN"):
        super().__init__(status.HTTP_403_FORBIDDEN, error_code, message)


class NotFoundError(ApiError):
    def __init__(self, message: str = "Ресурс не найден", error_code: str = "NOT_FOUND"):
        super().__init__(status.HTTP_404_NOT_FOUND, error_code, message)


class ConflictError(ApiError):
    def __init__(self, message: str, error_code: str = "CONFLICT"):
        super().__init__(status.HTTP_409_CONFLICT, error_code, message)


_DEFAULT_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}

_DEFAULT_MESSAGES = {
    404: "Ресурс не найден",
    405: "Метод не поддерживается",
}


# ── Ошибки БД → ответ API ─────────────────────────────────────────────────────

# Сообщения для нарушений UNIQUE (SQLSTATE 23505) по имени ограничения
_UNIQUE_MESSAGES = {
    "uq_users_email": "Пользователь с таким email уже зарегистрирован",
    "uq_users_phone": "Пользователь с таким телефоном уже зарегистрирован",
    "uq_user_skills_user_technology": "Этот навык уже добавлен в профиль",
    "uq_shortlists_owner_name": "Подборка с таким названием уже существует",
    "uq_shortlist_candidates_shortlist_candidate": "Кандидат уже есть в подборке",
    "uq_confirmations_pending_item": "Запрос на подтверждение этому коллеге уже отправлен",
    "uq_technologies_name": "Такая технология уже существует",
    "uq_companies_name": "Такая компания уже существует",
    "uq_educational_institutions_name": "Такое учебное заведение уже существует",
    "uq_user_integrations_user_provider": "Сервис уже подключён",
}

# Сообщения для нарушений CHECK (SQLSTATE 23514) без HINT из триггера
_CHECK_MESSAGES = {
    "ck_user_skills_level_range": ("SKILL_LEVEL_OUT_OF_RANGE", "Уровень навыка должен быть от 0 до 10"),
    "ck_vacancy_skills_level_range": ("SKILL_LEVEL_OUT_OF_RANGE", "Уровень навыка должен быть от 0 до 10"),
    "ck_education_records_dates": ("INVALID_DATE_RANGE", "Дата окончания раньше даты начала"),
    "ck_work_experiences_dates": ("INVALID_DATE_RANGE", "Дата окончания раньше даты начала"),
    "ck_confirmations_no_self": ("SELF_CONFIRMATION", "Нельзя подтвердить самого себя"),
    "ck_users_email_format": ("INVALID_EMAIL", "Некорректный формат email"),
    "ck_users_phone_format": ("INVALID_PHONE", "Некорректный формат телефона"),
}


def map_db_error(exc: DBAPIError) -> ApiError | None:
    """Преобразует ошибку PostgreSQL (ограничения/триггеры) в понятную ошибку API.

    Триггеры поднимают исключения с HINT = машинный код ошибки (например, EXPERIENCE_OVERLAP),
    а MESSAGE содержит текст для пользователя.
    """
    orig = exc.orig
    pg_error = getattr(orig, "__cause__", None) or orig
    sqlstate = getattr(orig, "sqlstate", None) or getattr(pg_error, "sqlstate", None)
    constraint = getattr(pg_error, "constraint_name", None)
    hint = getattr(pg_error, "hint", None)
    pg_message = getattr(pg_error, "message", None)

    if hint and pg_message and sqlstate in ("23514", "P0001", "23P01"):
        return BadRequestError(pg_message, error_code=hint)
    if sqlstate == "23505":
        return BadRequestError(
            _UNIQUE_MESSAGES.get(constraint, "Запись с такими данными уже существует"),
            error_code="DUPLICATE",
        )
    if sqlstate == "23514":
        code, message = _CHECK_MESSAGES.get(
            constraint, ("CONSTRAINT_VIOLATION", "Данные нарушают ограничения целостности")
        )
        return BadRequestError(message, error_code=code)
    if sqlstate == "23P01":
        return BadRequestError("Периоды работы пересекаются", error_code="EXPERIENCE_OVERLAP")
    if sqlstate == "23503":
        return BadRequestError("Связанная запись не найдена", error_code="RELATED_NOT_FOUND")
    if sqlstate == "23502":
        return BadRequestError("Не заполнено обязательное поле", error_code="VALIDATION_ERROR")
    if sqlstate in ("22000", "22001", "22003", "22007", "22008", "22P02"):
        return BadRequestError("Недопустимое значение поля", error_code="VALIDATION_ERROR")
    return None


# ── Обработчики ───────────────────────────────────────────────────────────────

def _format_validation_errors(exc: RequestValidationError) -> list[dict]:
    errors = []
    for err in exc.errors():
        location = [str(part) for part in err.get("loc", ()) if part not in ("body", "query", "path")]
        message = str(err.get("msg", "Некорректное значение"))
        # Сообщения из наших валидаторов приходят с префиксом "Value error, "
        message = message.removeprefix("Value error, ")
        errors.append({"field": ".".join(location), "message": message})
    return errors


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc, ApiError):
            body = error_body(exc.error_code, exc.message, exc.data)
        else:
            message = exc.detail if isinstance(exc.detail, str) else "Ошибка запроса"
            message = _DEFAULT_MESSAGES.get(exc.status_code, message)
            body = error_body(_DEFAULT_ERROR_CODES.get(exc.status_code, "ERROR"), message)
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = _format_validation_errors(exc)
        first = errors[0] if errors else {"field": "", "message": "Некорректные данные"}
        prefix = f"{first['field']}: " if first["field"] else ""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=error_body("VALIDATION_ERROR", f"Ошибка валидации — {prefix}{first['message']}", errors),
        )

    @app.exception_handler(DBAPIError)
    async def db_error_handler(request: Request, exc: DBAPIError):
        api_error = map_db_error(exc)
        if api_error is None:
            logger.exception("Database error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=error_body("DATABASE_ERROR", "Ошибка базы данных. Попробуйте позже"),
            )
        return JSONResponse(
            status_code=api_error.status_code,
            content=error_body(api_error.error_code, api_error.message),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("INTERNAL_ERROR", "Внутренняя ошибка сервера"),
        )
