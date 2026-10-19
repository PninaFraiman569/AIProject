"""צינור העיבוד: קובץ -> עמודים -> תמונות -> חילוץ -> נרמול -> מיזוג עמודי המשך."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from typing import Callable

from .config import Settings
from .entities import EntityList
from .extractor import ExtractionError, FormExtractor
from .image_utils import UnsupportedFile, is_pdf, load_pages, preprocess, to_jpeg
from .validation import Record, finalize, merge_records, normalize_form

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]


def _extract_safe(extractor: FormExtractor, jpeg: bytes):
    try:
        return extractor.extract(jpeg, "image/jpeg"), None
    except ExtractionError as exc:
        return None, str(exc)
    except Exception as exc:  # לא מפילים אצווה שלמה בגלל עמוד אחד
        log.exception("unexpected extraction failure")
        return None, f"שגיאה לא צפויה ({type(exc).__name__})"


def process_upload(filename: str, data: bytes, extractor: FormExtractor,
                   entities: EntityList, settings: Settings,
                   on_progress: ProgressCb | None = None,
                   today: date | None = None) -> list[Record]:
    """מעבד קובץ אחד (PDF או תמונה) ומחזיר רשומות. זורק UnsupportedFile לקובץ פגום."""
    pages = load_pages(data, settings.pdf_zoom, settings.max_pdf_pages)
    multi_page_source = is_pdf(data) or len(pages) > 1

    prepared = [preprocess(p, settings.max_image_side) for p in pages]
    jpegs = [to_jpeg(p, 90) for p in prepared]
    del pages, prepared  # תמונות הטפסים לא נשמרות - רק נשלחות לפענוח

    total = len(jpegs)
    records: list[Record] = []
    previous: Record | None = None

    with ThreadPoolExecutor(max_workers=max(1, settings.max_workers)) as pool:
        futures = [pool.submit(_extract_safe, extractor, j) for j in jpegs]
        for idx, fut in enumerate(futures):
            page_no = idx + 1 if multi_page_source else 0
            form, error = fut.result()

            if form is None:  # חילוץ נכשל - שורה ריקה מסומנת, כדי שלא יאבד העמוד
                rec = Record(pages=[page_no], source_name=filename)
                rec.model_flagged = True
                rec.add_reason(f"החילוץ נכשל: {error}")
                records.append(rec)
                previous = None
            else:
                rec = normalize_form(form, page_no, entities, settings, today)
                rec.source_name = filename
                if form.is_continuation_page and previous is not None:
                    merge_records(previous, rec)
                else:
                    if form.is_continuation_page:
                        rec.add_reason("עמוד המשך ללא עמוד קודם לחיבור")
                    records.append(rec)
                    previous = rec
            if on_progress:
                on_progress(idx + 1, total)

    for rec in records:
        finalize(rec, settings)
    return split_by_commitment(records)


def split_by_commitment(records: list[Record]) -> list[Record]:
    """כמה מספרי התחייבות בטופס אחד = כמה התחייבויות: רשומה נפרדת לכל מספר.

    כל שאר הפרטים (שם, ת.ז., מספר מרכזי, תאריך, גורם שולח, סימוני בדיקה) זהים.
    """
    out: list[Record] = []
    for rec in records:
        if len(rec.commitment_numbers) <= 1:
            out.append(rec)
            continue
        for num in rec.commitment_numbers:
            copy = deepcopy(rec)
            copy.commitment_numbers = [num]
            out.append(copy)
    return out
