"""לוגיקת עריכה ואישור של רשומות (מה קורה כשהלבורנטית מתקנת תא)."""
from __future__ import annotations

from datetime import datetime

from .core.config import Settings
from .core.entities import EntityList
from .core.schemas import DATA_FIELDS, FIELD_LABELS, LIST_FIELDS, STATUS_APPROVED
from .core.validation import clean_text, normalize_date, normalize_id, split_numbers
from .repository import Repository


class EditError(Exception):
    """ערך שהוקלד לא תקין - ההודעה מוצגת ללבורנטית."""


def normalize_edit(field: str, value, entities: EntityList, settings: Settings):
    """מנרמל ערך שהלבורנטית הקלידה. זורק EditError אם הערך לא תקין."""
    label = FIELD_LABELS[field]
    if field in LIST_FIELDS:
        items = value if isinstance(value, list) else [str(value or "")]
        numbers, _ = split_numbers(items)
        return numbers
    text = clean_text(str(value or ""))
    if not text:
        return ""
    if field == "id_number":
        return normalize_id(text, settings)[0] or text
    if field == "received_date":
        normalized, _ = normalize_date(text, max_age_days=settings.date_max_age_days)
        try:
            datetime.strptime(normalized, "%d/%m/%Y")
        except ValueError:
            raise EditError(f"{label}: ״{text}״ אינו תאריך תקין (DD/MM/YYYY)")
        return normalized
    if field == "sending_entity":
        matched = entities.match(text)
        if not matched:
            raise EditError(f"{label}: ״{text}״ אינו ברשימת הגורמים השולחים")
        return matched
    return text


def apply_edit(repo: Repository, record_id: int, changes: dict,
               entities: EntityList, settings: Settings) -> dict:
    """שומר תיקון של תאים.

    - כל תא שתוקן יוצא מרשימת ה״לא בטוח״ (הכתום יורד רק ממנו).
    - כשאין יותר תאים כתומים בשורה - השורה מאושרת אוטומטית.
    - אחרת השורה נשארת ממתינה לבדיקה, והלבורנטית מאשרת ידנית.
    """
    row = repo.get(record_id)
    if row is None:
        raise KeyError(record_id)
    values = {}
    for field, value in changes.items():
        if field not in DATA_FIELDS:
            continue
        new = normalize_edit(field, value, entities, settings)
        if new != row[field]:
            values[field] = new
    if not values:
        return row

    uncertain = [f for f in row["uncertain_fields"] if f not in values]
    values["uncertain_fields"] = uncertain
    had_orange = bool(row["uncertain_fields"])
    if had_orange and not uncertain:
        values.update(status=STATUS_APPROVED, needs_review=False)
    repo.update(record_id, values)
    return repo.get(record_id)
