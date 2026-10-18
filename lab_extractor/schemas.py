"""סכמות נתונים וקבועים משותפים."""
from __future__ import annotations

from pydantic import BaseModel, Field

# סטטוסים של רשומה
STATUS_PENDING = "ממתין"
STATUS_REVIEW = "ממתין לבדיקה"
STATUS_APPROVED = "אושר"

# שדות הנתונים (לפי סדר העמודות באקסל)
DATA_FIELDS = (
    "first_name",
    "last_name",
    "id_number",
    "central_number",
    "received_date",
    "sending_entity",
    "commitment_number",
)

FIELD_LABELS = {
    "first_name": "שם פרטי",
    "last_name": "שם משפחה",
    "id_number": "ת.ז.",
    "central_number": "מספר מרכזי",
    "received_date": "תאריך קבלה",
    "sending_entity": "גורם שולח",
    "commitment_number": "מספר התחייבות",
}

# שמות שדות שהמודל עשוי להחזיר -> שם השדה במסד הנתונים
MODEL_FIELD_ALIASES = {
    "central_numbers": "central_number",
    "commitment_numbers": "commitment_number",
}


class ExtractedForm(BaseModel):
    """הפלט שהמודל מחויב להחזיר, עבור עמוד אחד.

    הערה: ריבוי מספרים (מרכזיים / התחייבות) מוחזר כרשימה כדי שיהיה אמין יותר,
    והקוד מאחד אותם לשדה טקסט אחד עם רווח בין המספרים.
    """

    is_continuation_page: bool = Field(
        description="true רק אם זה עמוד המשך של טופס (למשל עמוד 2 מתוך 2, או "
        "עמוד תנאים) שאין בו פרטי מטופל משלו"
    )
    first_name: str = Field(description="שם פרטי של המטופל, או ריק אם לא ברור")
    last_name: str = Field(description="שם משפחה של המטופל, או ריק אם לא ברור")
    id_number: str = Field(description="מספר תעודת זהות כפי שנראה, או ריק")
    central_numbers: list[str] = Field(
        description="כל המספרים המרכזיים שנמצאו (רשימה ריקה אם אין)"
    )
    received_date: str = Field(
        description="תאריך קבלת הדגימה במעבדה (חותמת 'התקבל' או כתב יד), "
        "בפורמט DD/MM/YYYY, או ריק"
    )
    sending_entity: str = Field(
        description="הגורם השולח - בדיוק כפי שמופיע ברשימה הסגורה, או ריק"
    )
    commitment_numbers: list[str] = Field(
        description="כל מספרי ההתחייבות שנמצאו (רשימה ריקה אם אין)"
    )
    uncertain_fields: list[str] = Field(
        description="שמות השדות שהפיענוח שלהם לא ודאי: first_name, last_name, "
        "id_number, central_numbers, received_date, sending_entity, "
        "commitment_numbers"
    )
    needs_review: bool = Field(
        description="true אם יש שדה לא ודאי, כתב יד לא ברור, מדבקה חתוכה או "
        "כל ספק אחר"
    )
    review_reason: str = Field(
        description="הסבר קצר בעברית לסיבת הבדיקה, או ריק"
    )
