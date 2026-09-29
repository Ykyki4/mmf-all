import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, StringConstraints

# Разметка и скрипты во входных строках отклоняются (защита от хранимого XSS)
_MARKUP_PATTERN = re.compile(r"<\s*/?\s*[a-z!]|javascript:|\bon\w+\s*=", re.IGNORECASE)
_NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё' \-]*$")
_PHONE_PATTERN = re.compile(r"^\+?[0-9]{10,15}$")


def _reject_markup(value: str) -> str:
    if _MARKUP_PATTERN.search(value):
        raise ValueError("Недопустимое содержимое: HTML-разметка и скрипты запрещены")
    return value


def _validate_person_name(value: str) -> str:
    if not _NAME_PATTERN.match(value):
        raise ValueError("Допустимы только буквы, пробел, дефис и апостроф")
    return value


def _normalize_phone(value: object) -> object:
    if isinstance(value, str):
        value = re.sub(r"[\s()\-]", "", value)
        if value and not _PHONE_PATTERN.match(value):
            raise ValueError("Телефон должен содержать 10–15 цифр, допускается ведущий +")
        return value or None
    return value


def safe_str(max_length: int, min_length: int = 1):
    """Строка без разметки с обрезкой пробелов и ограничением длины."""
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
        AfterValidator(_reject_markup),
    ]


PersonName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    AfterValidator(_validate_person_name),
]
Phone = Annotated[str | None, BeforeValidator(_normalize_phone)]
Password = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class OrmModel(BaseModel):
    model_config = {"from_attributes": True}


class IdResponse(BaseModel):
    id: int
