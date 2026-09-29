from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from .common import Password, PersonName, Phone
from .users import UserBrief

MIN_REGISTER_PASSWORD_LENGTH = 8


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    password_confirm: Password
    last_name: PersonName
    first_name: PersonName
    patronymic: PersonName | None = None
    phone: Phone = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if len(value) < MIN_REGISTER_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен быть не короче {MIN_REGISTER_PASSWORD_LENGTH} символов")
        if not any(ch.isdigit() for ch in value) or not any(ch.isalpha() for ch in value):
            raise ValueError("Пароль должен содержать буквы и цифры")
        return value

    @model_validator(mode="after")
    def passwords_match(self) -> RegisterRequest:
        if self.password != self.password_confirm:
            raise ValueError("Пароли не совпадают")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: Password
    # true — поле password содержит SHA-256 hex от пароля (хеширование на клиенте)
    password_prehashed: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class LogoutRequest(BaseModel):
    # true — завершить все сессии пользователя («выйти со всех устройств»)
    all_devices: bool = False


class TwoFactorRequest(BaseModel):
    two_factor_token: str = Field(min_length=10)
    code: str = Field(pattern=r"^\d{6}$")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    new_password: Password

    @field_validator("new_password")
    @classmethod
    def password_length(cls, value: str) -> str:
        if len(value) < MIN_REGISTER_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен быть не короче {MIN_REGISTER_PASSWORD_LENGTH} символов")
        return value


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 — тип токена, не секрет
    expires_in: int  # секунд до истечения access-токена
    user: UserBrief


class TwoFactorChallenge(BaseModel):
    requires_2fa: bool = True
    two_factor_token: str
    # Только при EXPOSE_DEBUG_CODES=true (имитация письма в режиме разработки)
    debug_code: str | None = None


class CodeSentResponse(BaseModel):
    debug_code: str | None = None


class CsrfTokenResponse(BaseModel):
    csrf_token: str
    header_name: str = "X-CSRF-Token"
