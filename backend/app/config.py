from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Цифровые рекомендации ИТ-Проф API"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"

    database_url: str
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    two_factor_token_expire_minutes: int = 10
    auth_code_expire_minutes: int = 15
    auth_code_max_attempts: int = 5

    # Имитация отправки email: коды 2FA/восстановления возвращаются в ответе API
    expose_debug_codes: bool = False

    cors_origins: str = "*"
    csrf_protection: bool = True
    csrf_token_ttl_hours: int = 12
    auth_rate_limit_per_minute: int = 20

    public_base_url: str = "http://localhost:8000"
    log_dir: str = "logs"
    storage_dir: str = "storage"
    max_avatar_size_bytes: int = 2 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
