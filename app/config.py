from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0", alias="CHAT2API_HOST")
    port: int = Field(default=8765, alias="CHAT2API_PORT")

    # Console authentication is account/password based. These credentials are not
    # accepted by /v1/* business APIs and are not used by Chrome extensions.
    admin_username: str = Field(default="admin", alias="CHAT2API_ADMIN_USERNAME")
    admin_password: str = Field(default="change-me-admin", alias="CHAT2API_ADMIN_PASSWORD")
    admin_session_hours: int = Field(default=24, alias="CHAT2API_ADMIN_SESSION_HOURS")

    # Deprecated migration inputs. CHAT2API_API_KEY is no longer an administrator
    # credential nor a business API key; it is used only to migrate old encrypted
    # managed-key secrets. CHAT2API_PAIRING_CODE is imported once into the managed
    # pairing-code list so existing installations can transition without lockout.
    api_key: str = Field(default="", alias="CHAT2API_API_KEY")
    pairing_code: str = Field(default="", alias="CHAT2API_PAIRING_CODE")

    public_url: str = Field(default="", alias="CHAT2API_PUBLIC_URL")
    data_dir: Path = Field(default=Path("data"), alias="CHAT2API_DATA_DIR")
    request_timeout_seconds: int = Field(default=300, alias="CHAT2API_REQUEST_TIMEOUT_SECONDS")
    desktop_wake_timeout_seconds: int = Field(default=45, alias="CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS")
    allowed_origins: str = Field(default="*", alias="CHAT2API_ALLOWED_ORIGINS")

    @property
    def origins(self) -> list[str]:
        value = self.allowed_origins.strip()
        if not value or value == "*":
            return ["*"]
        return [item.strip() for item in value.split(",") if item.strip()]

    def resolved_public_url(self, request_base_url: str) -> str:
        configured = self.public_url.strip().rstrip("/")
        return configured or request_base_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
