from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
CONTEXT_FILE = BASE_DIR / "岗位标准化母体.history.json"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_ROOT = BASE_DIR / "uploads"
SUPPORTED_ATTACHMENT_EXTS = (
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".pdf",
)


class Settings(BaseSettings):
    # --- OpenAI / LLM ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # --- Materials / context ---
    materials_autoload: bool = True
    materials_dir: str = ""
    materials_max_files: int = 20
    materials_max_excerpt_chars: int = 1200

    # --- Attachments ---
    attachment_excerpt_chars: int = 0
    attachment_hint_chars: int = 0
    attachment_show_meta: bool = False

    # --- LLM payload debug ---
    llm_payload_debug: bool = False
    llm_payload_preview_chars: int = 180

    # --- Logging ---
    log_level: str = "INFO"
    log_file: str = "app.log"
    log_rotation: str = "1 day"
    log_retention: str = "14 days"
    log_json: bool = False

    # --- CORS ---
    cors_allow_origins: str = "*"
    cors_allow_origin_regex: str = ""

    # --- Spreadsheet extraction limits ---
    spreadsheet_raw_row_limit: int = 0
    spreadsheet_raw_col_limit: int = 0

    # --- Database / Auth (new) ---
    database_url: str = "postgresql+asyncpg://gangbiao:gangbiao@localhost:5432/gangbiao"
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 7

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        extra="ignore",
    )

    def resolved_materials_dir(self) -> Path | None:
        raw = self.materials_dir.strip()
        if not raw:
            return None
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
        return p


settings = Settings()


def get_cors_allow_origins() -> list[str]:
    raw = settings.cors_allow_origins.strip()
    if not raw:
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def get_cors_allow_origin_regex() -> str | None:
    raw = settings.cors_allow_origin_regex.strip()
    if raw:
        return raw
    # Default to common private-network/browser localhost origins.
    return r"^https?://(localhost|127\.0\.0\.1|10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3})\.(?:\d{1,3})|172\.(?:1[6-9]|2\d|3[0-1])\.(?:\d{1,3})\.(?:\d{1,3}))(?::\d+)?$"
