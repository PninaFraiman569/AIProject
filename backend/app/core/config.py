"""הגדרות הבקאנד.

כל ההגדרות נקראות ממשתני סביבה (או מקובץ .env בתיקייה הראשית של הפרויקט),
כך שאותו קוד רץ מקומית וב-Render בלי שינוי.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]   # תיקיית הפרויקט הראשית
CORE_DIR = Path(__file__).resolve().parent

try:  # טעינת קובץ .env מקומי, אם קיים (ב-Render לא קיים - שם משתני סביבה)
    from dotenv import load_dotenv

    load_dotenv(PROJECT_DIR / ".env")
except Exception:  # pragma: no cover
    pass

DEFAULT_MODEL = "gemini-3.8-flash"


def _read(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    # Gemini
    gemini_api_key: str
    gemini_model: str
    # מסד נתונים
    database_url: str
    # כניסה
    app_username: str
    app_password: str
    session_minutes: int
    max_login_attempts: int
    # עיבוד
    entities_path: str
    pdf_zoom: float
    max_image_side: int
    max_pdf_pages: int
    max_workers: int
    max_retries: int
    required_fields: tuple
    central_number_length: int  # 0 = ללא בדיקת אורך
    check_id_checksum: bool
    date_max_age_days: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    required = _read(
        "REQUIRED_FIELDS",
        "first_name,last_name,id_number,central_numbers,received_date,"
        "sending_entity,commitment_numbers",
    )
    return Settings(
        gemini_api_key=_read("GEMINI_API_KEY") or _read("GOOGLE_API_KEY"),
        gemini_model=_read("GEMINI_MODEL", DEFAULT_MODEL),
        database_url=_read("DATABASE_URL"),
        app_username=_read("APP_USERNAME", "lab"),
        app_password=_read("APP_PASSWORD"),
        session_minutes=int(_read("SESSION_MINUTES", "60")),
        max_login_attempts=int(_read("MAX_LOGIN_ATTEMPTS", "5")),
        entities_path=_read("ENTITIES_PATH", str(CORE_DIR / "sending_entities.txt")),
        pdf_zoom=float(_read("PDF_ZOOM", "2.5")),   # כ-180 DPI
        max_image_side=int(_read("MAX_IMAGE_SIDE", "2200")),
        max_pdf_pages=int(_read("MAX_PDF_PAGES", "150")),
        max_workers=int(_read("MAX_WORKERS", "4")),
        max_retries=int(_read("MAX_RETRIES", "3")),
        required_fields=tuple(f.strip() for f in required.split(",") if f.strip()),
        central_number_length=int(_read("CENTRAL_NUMBER_LENGTH", "6")),
        check_id_checksum=_as_bool(_read("CHECK_ID_CHECKSUM", "false")),
        date_max_age_days=int(_read("DATE_MAX_AGE_DAYS", "1095")),
    )
