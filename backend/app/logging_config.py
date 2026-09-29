import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import settings

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_logging() -> None:
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        log_dir / "api.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    app_logger = logging.getLogger("itprof")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        app_logger.addHandler(file_handler)
        app_logger.addHandler(console_handler)
    app_logger.propagate = False


def mask_email(email: str) -> str:
    """Маскирует email для логов: ivanov@mail.ru → i***@mail.ru."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
