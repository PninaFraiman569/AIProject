import os
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.entities import EntityList  # noqa: E402
from app.core.schemas import ExtractedForm  # noqa: E402

# העיבוד בבדיקות רץ בתהליכון אחד, כדי שהמודל המדומה יקבל את העמודים לפי הסדר
S = replace(get_settings(), max_workers=1)
ENT = EntityList.from_file(S.entities_path)
TODAY = date(2026, 10, 18)


def form(**kw):
    base = dict(is_continuation_page=False, first_name="דנה", last_name="כהן",
                id_number="11223344", central_numbers=["947768"],
                received_date="27-08-2026", sending_entity="שמיר",
                commitment_numbers=["2646"], uncertain_fields=[],
                needs_review=False, review_reason="")
    base.update(kw)
    return ExtractedForm(**base)


class FakeExtractor:
    """מודל מדומה: מחזיר את התשובות שהוגדרו מראש, לפי סדר העמודים."""

    def __init__(self, forms):
        self.forms, self.i = list(forms), 0

    def extract(self, image_bytes, mime_type="image/jpeg"):
        f = self.forms[self.i]
        self.i += 1
        if isinstance(f, Exception):
            raise f
        return f


def make_pdf(n):
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i}")
    return doc.tobytes()


@pytest.fixture
def database_url():
    """בדיקות המסד רצות רק כשמוגדר TEST_DATABASE_URL (מסד ריק לבדיקות!)."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL לא הוגדר - מדלגים על בדיקות המסד")
    return url
