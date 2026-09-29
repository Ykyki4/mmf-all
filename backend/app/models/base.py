from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Numeric, func
from sqlmodel import Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def created_at_field() -> Any:
    """Метка времени создания: заполняется и приложением, и БД (для вставок из триггеров)."""
    return Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
        nullable=False,
    )


def timestamp_field(nullable: bool = True, index: bool = False) -> Any:
    return Field(default=None, sa_type=DateTime(timezone=True), nullable=nullable, index=index)


def score_field(default: float = 0.0) -> Any:
    """Показатель рейтинга 0.00–100.00."""
    return Field(
        default=default,
        sa_type=Numeric(5, 2, asdecimal=False),
        sa_column_kwargs={"server_default": "0"},
        nullable=False,
    )


def enum_check(column: str, enum: type[StrEnum]) -> str:
    """SQL-выражение CHECK (column IN (...)) для допустимых значений перечисления."""
    values = ", ".join(f"'{item.value}'" for item in enum)
    return f"{column} IN ({values})"
