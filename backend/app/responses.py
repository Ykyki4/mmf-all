from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Единый формат ответа API: {success, data, message, error_code}."""

    success: bool = True
    data: T | None = None
    message: str = "OK"
    error_code: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


def ok(data: Any = None, message: str = "OK") -> ApiResponse:
    return ApiResponse(success=True, data=data, message=message, error_code=None)


def error_body(error_code: str, message: str, data: Any = None) -> dict:
    return {"success": False, "data": data, "message": message, "error_code": error_code}


def make_page(items: list, total: int, page: int, page_size: int) -> Page:
    pages = (total + page_size - 1) // page_size if page_size else 0
    return Page(items=items, total=total, page=page, page_size=page_size, pages=pages)
