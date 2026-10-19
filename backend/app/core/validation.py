"""אימות ונרמול של התוצאות שחזרו מהמודל (לוגיקה דטרמיניסטית, בקוד)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import Settings
from .entities import EntityList
from .schemas import (
    DATA_FIELDS, FIELD_LABELS, LIST_FIELDS, LIST_SEPARATOR, STATUS_PENDING,
    STATUS_REVIEW, ExtractedForm,
)


@dataclass
class Record:
    """רשומת התחייבות אחת (אחרי נרמול, ואחרי מיזוג עמודים)."""

    first_name: str = ""
    last_name: str = ""
    id_number: str = ""
    central_numbers: list = field(default_factory=list)     # כל מספר בנפרד
    received_date: str = ""       # DD/MM/YYYY
    sending_entity: str = ""
    commitment_numbers: list = field(default_factory=list)  # כל מספר בנפרד
    uncertain: set = field(default_factory=set)
    reasons: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    model_flagged: bool = False
    source_name: str = ""

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
        """שורה לשמירה במסד. שדות הרשימה נשארים רשימות (TEXT[] במסד)."""
        return {
            "source_file": self.source_file,
            **{f: (list(getattr(self, f)) if f in LIST_FIELDS else getattr(self, f))
               for f in DATA_FIELDS},
            "needs_review": self.needs_review,
            "review_reason": "; ".join(self.reasons),
            "uncertain_fields": sorted(self.uncertain),
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


# מפרידים מפורשים בין מספרים
_EXPLICIT_SEP = re.compile(r"[,;|/\\\n]+")
_STRIP_CHARS = ".-:*()[]\"'׳״ "
_MIN_SPLIT_LEN = 4   # מתחת לזה, חלק מופרד ברווח/מקף נחשב לשבר של אותו מספר


def split_numbers(items) -> tuple[list[str], list[str]]:
    """מפצל לרשימת מספרים בודדים, בלי כפילויות ועם שמירת סדר.

    מחזיר (מספרים, הערות). הכללים:
    - פסיק, נקודה-פסיק, קו נטוי, | ושורה חדשה - תמיד מפרידים בין מספרים.
    - רווח או מקף מפרידים רק אם כל החלקים ארוכים (4 תווים ומעלה), למשל
      ״947768 947769״. אחרת זה מספר אחד שנקרא עם רווח (״94776 8״ -> ״947768״),
      והמספר מסומן לבדיקה.
    """
    out: list[str] = []
    notes: list[str] = []
    for item in items or []:
        for chunk in _EXPLICIT_SEP.split(str(item)):
            chunk = chunk.strip(_STRIP_CHARS)
            if not chunk:
                continue
            parts = [p.strip(_STRIP_CHARS) for p in re.split(r"[\s\-–]+", chunk)]
            parts = [p for p in parts if p]
            if len(parts) > 1 and all(len(p) >= _MIN_SPLIT_LEN for p in parts):
                tokens = parts
            elif len(parts) > 1:
                tokens = ["".join(parts)]
                notes.append(f"״{chunk}״ אוחד למספר אחד ({tokens[0]})")
            else:
                tokens = parts
            for tok in tokens:
                if tok not in out:
                    out.append(tok)
    return out, notes


def join_numbers(values) -> str:
    """רשימת מספרים -> טקסט לתצוגה (מופרד בפסיק)."""
    return LIST_SEPARATOR.join(v for v in (values or []) if v)


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


# ------------------------------------------------------------------ מספר מרכזי
def normalize_central(items, rec: Record, settings: Settings) -> list[str]:
    tokens, notes = split_numbers(items)
    for note in notes:
        rec.flag("central_numbers", f"מספר מרכזי: {note}")
    central: list[str] = []
    for tok in tokens:
        digits = re.sub(r"\D", "", tok)
        if not digits:
            rec.flag("central_numbers", f"מספר מרכזי לא תקין: ״{tok}״")
            continue
        if digits != tok:
            rec.flag("central_numbers", f"המספר המרכזי ״{tok}״ כלל תווים שאינם ספרות")
        n = settings.central_number_length
        if n and len(digits) != n:
            rec.flag("central_numbers", f"מספר מרכזי באורך חריג: {digits}")
        if digits not in central:
            central.append(digits)
    return central


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

    rec.central_numbers = normalize_central(form.central_numbers, rec, settings)

    commitments, notes = split_numbers(form.commitment_numbers)
    for note in notes:
        rec.flag("commitment_numbers", f"מספר התחייבות: {note}")
    rec.commitment_numbers = commitments

    # אותו מספר בשני שדות = המודל כנראה העביר מספר לשדה הלא נכון
    id_core = rec.id_number.lstrip("0")
    for num in rec.commitment_numbers:
        if id_core and num.lstrip("0") == id_core:
            rec.flag("commitment_numbers", f"מספר ההתחייבות {num} זהה לת.ז.")
        if num in rec.central_numbers:
            rec.flag("commitment_numbers", f"מספר ההתחייבות {num} זהה למספר המרכזי")
    for num in rec.central_numbers:
        if id_core and num.lstrip("0") == id_core:
            rec.flag("central_numbers", f"המספר המרכזי {num} זהה לת.ז.")

    # סימוני אי-ודאות מהמודל
    for key in form.uncertain_fields or []:
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
    for f in LIST_FIELDS:
        merged = list(getattr(base, f))
        for v in getattr(extra, f):
            if v not in merged:
                merged.append(v)
        setattr(base, f, merged)
    base.uncertain |= extra.uncertain
    for r in extra.reasons:
        base.add_reason(r)
    base.model_flagged = base.model_flagged or extra.model_flagged
    base.pages = sorted(set(base.pages + extra.pages))
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
