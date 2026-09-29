import logging
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import verify_csrf_token
from .config import settings
from .database import AsyncSessionLocal
from .errors import ApiError
from .models import ApiRequestLog
from .responses import error_body

request_logger = logging.getLogger("itprof.api")

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


# ── Логирование всех операций API ─────────────────────────────────────────────

class RequestLoggingMiddleware:
    """Пишет каждую операцию в файл logs/api.log и таблицу api_request_logs:
    метод, путь (без query — там могут быть персональные данные), user_id, статус, длительность."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", []).extend(_SECURITY_HEADERS)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            user_id = scope.get("state", {}).get("user_id")
            client = scope.get("client")
            await _write_log(
                method=scope["method"],
                path=scope["path"][:500],
                status_code=status_holder["status"],
                duration_ms=duration_ms,
                user_id=user_id,
                client_ip=client[0] if client else None,
            )


async def _write_log(**fields) -> None:
    request_logger.info(
        "%s %s status=%s user_id=%s duration=%sms",
        fields["method"], fields["path"], fields["status_code"], fields["user_id"], fields["duration_ms"],
    )
    try:
        async with AsyncSessionLocal() as session:
            session.add(ApiRequestLog(**fields))
            await session.commit()
    except Exception:  # noqa: BLE001 — сбой журнала не должен ломать ответ клиенту
        request_logger.warning("Failed to persist request log", exc_info=True)


# ── CSRF ──────────────────────────────────────────────────────────────────────

class CsrfMiddleware:
    """Для изменяющих запросов из браузера с чужого origin требует заголовок X-CSRF-Token,
    полученный через GET /auth/csrf-token. Нативные клиенты (desktop/mobile) не отправляют
    заголовок Origin и проверку не проходят."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and settings.csrf_protection and scope["method"] in _UNSAFE_METHODS:
            headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
            origin = headers.get("origin")
            if origin and urlsplit(origin).netloc != headers.get("host"):
                if not verify_csrf_token(headers.get("x-csrf-token")):
                    response = JSONResponse(
                        status_code=403,
                        content=error_body("CSRF_TOKEN_INVALID", "Отсутствует или недействителен CSRF-токен"),
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


# ── Ограничение частоты запросов (auth-эндпоинты) ─────────────────────────────

class RateLimiter:
    """Скользящее окно в памяти процесса: не более `limit` запросов в минуту с одного IP."""

    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def __call__(self, request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.limit:
            raise ApiError(429, "RATE_LIMITED", "Слишком много попыток. Повторите через минуту")
        hits.append(now)


auth_rate_limit = RateLimiter(settings.auth_rate_limit_per_minute)
