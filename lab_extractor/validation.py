"""אימות ונרמול של התוצאות שחזרו מהמודל (לוגיקה דטרמיניסטית, בקוד)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import Settings
from .entities import EntityList
from .schemas import (
    DATA_FIELDS, FIELD_LABELS, MODEL_FIELD_ALIASES, STATUS_PENDING,
    STATUS_REVIEW, ExtractedForm,
)


@dataclass
class Record:
    """רשומת התחייבות אחת (אחרי נרמול, ואחרי מיזוג עמודים)."""

    first_name: str = ""
    last_name: str = ""
    id_number: str = ""
    central_number: str = ""      # כמה מספרים: מופרדים ברווח
    received_date: str = ""       # DD/MM/YYYY
    sending_entity: str = ""
    commitment_number: str = ""   # כמה מספרים: מופרדים ברווח
    uncertain: set = field(default_factory=set)
    reasons: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    model_flagged: bool = False
    source_name: str = ""
    previews: list = field(default_factory=list)  # JPEG bytes (בזיכרון בלבד)

    # ---- עזרים ----
    def add_reason(self, reason: str) -> None:
        reason = (reason or "").strip()
        if reason and reason not in self.reasons:
            self.reasons.append(reason)

    def flag(self, field_name: str, reason: str) -> None:
        self.uncertain.add(field_name)
        self.add_reason(reason)

    @property
    def needs_review(self) -> bool:
        return bool(self.model_flagged or self.reasons or self.uncertain)

    @property
    def source_file(self) -> str:
        if not self.pages or self.pages == [0]:
            return self.source_name
        return f"{self.source_name} (עמ' {_pages_label(self.pages)})"

    def to_row(self) -> dict:
        return {
            "source_file": self.source_file,
            **{f: getattr(self, f) for f in DATA_FIELDS},
            "needs_review": int(self.needs_review),
            "review_reason": "; ".join(self.reasons),
            "uncertain_fields": ",".join(sorted(self.uncertain)),
            "status": STATUS_REVIEW if self.needs_review else STATUS_PENDING,
        }


def _pages_label(pages: list[int]) -> str:
    pages = sorted(set(pages))
    if len(pages) > 1 and pages == list(range(pages[0], pages[-1] + 1)):
        return f"{pages[0]}-{pages[-1]}"
    return ", ".join(str(p) for p in pages)


# ------------------------------------------------------------------ טקסט
def clean_text(value: str) -> str:
    return " ".join((value or "").split())


def split_numbers(items) -> list[str]:
    """מפצל לרשימת מספרים בודדים, בלי כפילויות ועם שמירת סדר."""
    out: list[str] = []
    for item in items or []:
        for tok in re.split(r"[\s,;/|]+", str(item)):
            tok = tok.strip(".-:*()[]\"'׳״")
            if tok and tok not in out:
                out.append(tok)
    return out


# ------------------------------------------------------------------ ת.ז.
def _id_checksum_ok(digits9: str) -> bool:
    total = 0
    for i, ch in enumerate(digits9):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def normalize_id(raw: str, settings: Settings | None = None) -> tuple[str, str | None]:
    """משלים אפסים מובילים לתעודת זהות (9 ספרות). מחזיר (ערך, סיבה-לבדיקה)."""
    s = clean_text(raw)
    if not s:
        return "", None
    if re.search(r"[A-Za-z\u0590-\u05FF]", s):
        return s, "מספר הזהות כולל אותיות (ייתכן דרכון)"
    digits = re.sub(r"\D", "", s)
    stripped = digits.lstrip("0")
    if not stripped:
        return "", "מספר הזהות לא תקין"
    if len(stripped) > 9:
        return digits, "מספר הזהות ארוך מ-9 ספרות"
    value = stripped.zfill(9)
    if len(stripped) < 7:
        return value, "מספר הזהות קצר מהצפוי"
    if settings and settings.check_id_checksum and not _id_checksum_ok(value):
        return value, "ספרת הביקורת של ת.ז. לא תקינה"
    return value, None


# ------------------------------------------------------------------ תאריך
_DMY = re.compile(r"(\d{1,2})\s*[./\-\\]\s*(\d{1,2})\s*[./\-\\]\s*(\d{4}|\d{2})")
_YMD = re.compile(r"(\d{4})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})")


def normalize_date(raw: str, today: date | None = None,
                   max_age_days: int = 1095) -> tuple[str, str | None]:
    """מנרמל ל-DD/MM/YYYY ובודק סבירות. מחזיר (ערך, סיבה-לבדיקה)."""
    s = clean_text(raw)
    if not s:
        return "", None
    today = today or date.today()
    m = _YMD.search(s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = _DMY.search(s)
        if not m:
            return s, "תאריך הקבלה לא זוהה כתאריך תקין"
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000
    try:
        dt = date(y, mo, d)
    except ValueError:
        return s, "תאריך הקבלה לא תקין"
    value = dt.strftime("%d/%m/%Y")
    if dt > today + timedelta(days=1):
        return value, "תאריך הקבלה עתידי"
    if (today - dt).days > max_age_days:
        return value, "תאריך הקבלה ישן מהצפוי"
    return value, None


# ------------------------------------------------------------------ נרמול טופס
def normalize_form(form: ExtractedForm, page_no: int, entities: EntityList,
                   settings: Settings, today: date | None = None) -> Record:
    rec = Record(pages=[page_no])
    rec.first_name = clean_text(form.first_name)
    rec.last_name = clean_text(form.last_name)

    rec.id_number, reason = normalize_id(form.id_number, settings)
    if reason:
        rec.flag("id_number", reason)

    rec.received_date, reason = normalize_date(
        form.received_date, today, settings.date_max_age_days
    )
    if reason:
        rec.flag("received_date", reason)

    raw_entity = clean_text(form.sending_entity)
    if raw_entity:
        matched = entities.match(raw_entity)
        if matched:
            rec.sending_entity = matched
        else:
            rec.flag("sending_entity", f"גורם שולח שאינו ברשימה: ״{raw_entity}״")

    central: list[str] = []
    for tok in split_numbers(form.central_numbers):
        digits = re.sub(r"\D", "", tok)
        if not digits:
            rec.flag("central_number", f"מספר מרכזי לא תקין: ״{tok}״")
            continue
        if digits != tok:
            rec.flag("central_number", f"המספר המרכזי ״{tok}״ כלל תווים שאינם ספרות")
        n = settings.central_number_length
        if n and len(digits) != n:
            rec.flag("central_number", f"מספר מרכזי באורך חריג: {digits}")
        if digits not in central:
            central.append(digits)
    rec.central_number = " ".join(central)

    rec.commitment_number = " ".join(split_numbers(form.commitment_numbers))

    # סימוני אי-ודאות מהמודל
    for key in form.uncertain_fields or []:
        key = MODEL_FIELD_ALIASES.get(key, key)
        if key in DATA_FIELDS:
            rec.uncertain.add(key)
    rec.model_flagged = bool(form.needs_review or form.uncertain_fields)
    rec.add_reason(form.review_reason)
    if rec.model_flagged and not rec.reasons:
        labels = ", ".join(FIELD_LABELS[f] for f in sorted(rec.uncertain)) or "שדה כלשהו"
        rec.add_reason(f"פיענוח לא ודאי: {labels}")
    return rec


def merge_records(base: Record, extra: Record) -> Record:
    """ממזג עמוד המשך לרשומה הקודמת: משלים חסרים, מאחד מספרים, מסמן סתירות."""
    for f in ("first_name", "last_name", "id_number", "received_date", "sending_entity"):
        a, b = getattr(base, f), getattr(extra, f)
        if not a and b:
            setattr(base, f, b)
        elif a and b and a != b:
            base.flag(f, f"סתירה בין עמודים ב{FIELD_LABELS[f]}: ״{a}״ / ״{b}״")
    for f in ("central_number", "commitment_number"):
        merged = split_numbers([getattr(base, f), getattr(extra, f)])
        setattr(base, f, " ".join(merged))
    base.uncertain |= extra.uncertain
    for r in extra.reasons:
        base.add_reason(r)
    base.model_flagged = base.model_flagged or extra.model_flagged
    base.pages = sorted(set(base.pages + extra.pages))
    base.previews += extra.previews
    return base


def finalize(rec: Record, settings: Settings) -> Record:
    """בדיקות שנעשות רק אחרי המיזוג: שדות חסרים."""
    if not any(getattr(rec, f) for f in DATA_FIELDS):
        rec.add_reason("לא נמצאו שדות עיקריים בעמוד (ייתכן עמוד המשך או מסמך אחר)")
        rec.uncertain.update(DATA_FIELDS)
        return rec
    missing = [f for f in settings.required_fields if f in DATA_FIELDS and not getattr(rec, f)]
    if missing:
        rec.add_reason("שדות חסרים: " + ", ".join(FIELD_LABELS[f] for f in missing))
        rec.uncertain.update(missing)
    return rec
