"""הגדרות המערכת.

כל ההגדרות נקראות ממשתני סביבה (או מ-st.secrets / קובץ .env), כך שאפשר להעלות
את אותו קוד לכל סביבת הרצה בלי לשנות אותו.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:  # טעינת קובץ .env מקומי, אם קיים
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent


def _read(name: str, default: str = "") -> str:
    """קורא הגדרה ממשתנה סביבה, ואם אין - מ-st.secrets."""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    db_path: str
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
        "first_name,last_name,id_number,central_number,received_date,"
        "sending_entity,commitment_number",
    )
    return Settings(
        gemini_api_key=_read("GEMINI_API_KEY") or _read("GOOGLE_API_KEY"),
        gemini_model=_read("GEMINI_MODEL", "gemini-3.8-flash"),
        db_path=_read("DATABASE_PATH", str(BASE_DIR / "data" / "lab_data.db")),
        entities_path=_read(
            "ENTITIES_PATH", str(PACKAGE_DIR / "sending_entities.txt")
        ),
        pdf_zoom=float(_read("PDF_ZOOM", "3.0")),
        max_image_side=int(_read("MAX_IMAGE_SIDE", "2800")),
        max_pdf_pages=int(_read("MAX_PDF_PAGES", "150")),
        max_workers=int(_read("MAX_WORKERS", "4")),
        max_retries=int(_read("MAX_RETRIES", "3")),
        required_fields=tuple(
            f.strip() for f in required.split(",") if f.strip()
        ),
        central_number_length=int(_read("CENTRAL_NUMBER_LENGTH", "6")),
        check_id_checksum=_as_bool(_read("CHECK_ID_CHECKSUM", "false")),
        date_max_age_days=int(_read("DATE_MAX_AGE_DAYS", "1095")),
    )
