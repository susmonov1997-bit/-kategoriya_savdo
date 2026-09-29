"""Sozlamalar .env / Railway environment o'zgaruvchilaridan olinadi."""
import os

from dotenv import load_dotenv

load_dotenv()


def _ids(v: str | None) -> set[int]:
    return {int(x) for x in (v or "").replace(" ", "").split(",") if x}


BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
# Vaqtinchalik allowlist (4-bosqichda bazadagi foydalanuvchilar jadvaliga o'tadi)
ADMIN_IDS: set[int] = _ids(os.environ.get("ADMIN_IDS"))
MAX_FILE_MB: int = int(os.environ.get("MAX_FILE_MB", "20"))   # Telegram Bot API limiti 20 MB

# --- API / Mini App ---
INITDATA_MAX_AGE: int = int(os.environ.get("INITDATA_MAX_AGE", str(24 * 3600)))   # initData amal qilish muddati, s
API_DEV_USER_ID: int | None = int(os.environ["API_DEV_USER_ID"]) if os.environ.get("API_DEV_USER_ID") else None
CORS_ORIGINS: list[str] = [o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o]
# Mini App manzili (HTTPS), masalan https://savdo.up.railway.app/app/ — bo'sh bo'lsa bot tugma ko'rsatmaydi
WEBAPP_URL: str = os.environ.get("WEBAPP_URL", "")
# Railway muhitida API_DEV_USER_ID (sinov rejimi) hech qachon ishlamaydi
ON_RAILWAY: bool = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_ENVIRONMENT_NAME"))
PORT: int = int(os.environ.get("PORT", "8000"))
