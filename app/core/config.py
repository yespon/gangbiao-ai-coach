import os
from pathlib import Path

from dotenv import load_dotenv

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

# Load environment variables from local .env before reading os.getenv.
load_dotenv(dotenv_path=BASE_DIR / ".env")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_materials_dir() -> Path | None:
    raw = os.getenv("MATERIALS_DIR", "").strip()
    if not raw:
        return None

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
    return p


def get_cors_allow_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if not raw:
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def get_cors_allow_origin_regex() -> str | None:
    raw = os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    if raw:
        return raw
    # Default to common private-network/browser localhost origins.
    return r"^https?://(localhost|127\.0\.0\.1|10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.(?:\d{1,3})\.(?:\d{1,3})|172\.(?:1[6-9]|2\d|3[0-1])\.(?:\d{1,3})\.(?:\d{1,3}))(?::\d+)?$"
