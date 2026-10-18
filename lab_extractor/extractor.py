"""חילוץ שדות מעמוד בודד בעזרת Gemini (פלט מובנה לפי סכמת Pydantic)."""
from __future__ import annotations

import json
import logging
import time
from typing import Protocol

from pydantic import ValidationError

from .config import Settings
from .prompts import USER_PROMPT, build_system_prompt
from .schemas import ExtractedForm

log = logging.getLogger(__name__)

# קודי שגיאה שאין טעם לנסות שוב (מפתח לא תקין, בקשה שגויה וכו')
_NON_RETRYABLE = {400, 401, 403, 404}


class ExtractionError(Exception):
    """החילוץ נכשל (שגיאת רשת, מכסה, תשובה לא תקינה וכו')."""


class FormExtractor(Protocol):
    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> ExtractedForm: ...


class GeminiExtractor:
    def __init__(self, settings: Settings, entity_names: list[str]):
        if not settings.gemini_api_key:
            raise ExtractionError("לא הוגדר מפתח GEMINI_API_KEY")
        from google import genai  # ייבוא מאוחר: לא נדרש לבדיקות

        self._types = __import__("google.genai.types", fromlist=["types"])
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        self._retries = max(1, settings.max_retries)
        self._system_prompt = build_system_prompt(entity_names)

    def extract(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> ExtractedForm:
        types = self._types
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            response_mime_type="application/json",
            response_schema=ExtractedForm,
            temperature=0.0,
        )
        last_error: Exception | None = None
        for attempt in range(self._retries):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        USER_PROMPT,
                    ],
                    config=config,
                )
                parsed = getattr(response, "parsed", None)
                if isinstance(parsed, ExtractedForm):
                    return parsed
                text = (response.text or "").strip()
                if text.startswith("```"):
                    text = text.strip("`").removeprefix("json").strip()
                return ExtractedForm.model_validate(json.loads(text))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc  # תשובה פגומה - ננסה שוב
                log.warning("invalid model response (attempt %d)", attempt + 1)
            except Exception as exc:  # שגיאות רשת / מכסה / API
                last_error = exc
                code = getattr(exc, "code", None)
                if code in _NON_RETRYABLE:
                    raise ExtractionError(_friendly(exc)) from exc
                log.warning("API error (attempt %d): %s", attempt + 1, type(exc).__name__)
            if attempt < self._retries - 1:
                time.sleep(2 ** attempt)
        raise ExtractionError(_friendly(last_error))


def _friendly(exc: Exception | None) -> str:
    code = getattr(exc, "code", None)
    if code in (401, 403):
        return "מפתח ה-API לא תקין או חסרות הרשאות"
    if code == 429:
        return (
            "מכסת Gemini API הגיעה למגבלה (429). בדקי את השימוש והמכסה ב-Google AI Studio; "
            "המתיני עד לאיפוס המכסה או השתמשי בפרויקט עם מכסה זמינה."
        )
    if code == 404:
        return "המודל שהוגדר לא נמצא (בדקי GEMINI_MODEL)"
    return f"הפיענוח נכשל ({type(exc).__name__ if exc else 'שגיאה לא ידועה'})"
