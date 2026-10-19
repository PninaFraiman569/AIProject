"""טעינת קבצים והכנת תמונות: PDF -> תמונות, יישור, חידוד.

החידוד נעשה *בצבע* ובעדינות: בטפסים צבע הוא מידע (סימון צהוב, עט כחול),
ולכן לא ממירים לשחור-לבן ולא מבצעים בינאריזציה.
"""
from __future__ import annotations

import io
import logging
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # תמונות HEIC מאייפון
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover
    pass

log = logging.getLogger(__name__)


class UnsupportedFile(Exception):
    """הקובץ פגום, מוגן בסיסמה או מסוג שלא נתמך."""


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def pdf_to_images(data: bytes, zoom: float = 3.0, max_pages: int = 150) -> list[Image.Image]:
    """ממיר כל עמוד ב-PDF לתמונה ברזולוציה גבוהה (מטריצת zoom x zoom)."""
    try:
        import pymupdf as fitz  # PyMuPDF (השם החדש)
    except ImportError:  # pragma: no cover
        import fitz

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnsupportedFile(f"לא ניתן לפתוח את ה-PDF: {exc}") from exc
    if doc.needs_pass:
        raise UnsupportedFile("ה-PDF מוגן בסיסמה")
    if doc.page_count == 0:
        raise UnsupportedFile("ה-PDF ריק")
    if doc.page_count > max_pages:
        raise UnsupportedFile(
            f"ה-PDF כולל {doc.page_count} עמודים (המקסימום הוא {max_pages})"
        )
    images: list[Image.Image] = []
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    except Exception as exc:
        raise UnsupportedFile(f"שגיאה בהמרת עמוד ב-PDF: {exc}") from exc
    finally:
        doc.close()
    return images


def load_image_file(data: bytes) -> list[Image.Image]:
    """טוען תמונה (כולל HEIC, וכולל תיקון כיוון EXIF של מצלמת טלפון)."""
    try:
        img = Image.open(io.BytesIO(data))
        frames = []
        n = getattr(img, "n_frames", 1)
        for i in range(n):
            img.seek(i)
            frame = ImageOps.exif_transpose(img.copy())
            frames.append(frame.convert("RGB"))
        return frames
    except Exception as exc:
        raise UnsupportedFile(f"קובץ התמונה פגום או לא נתמך: {exc}") from exc


def load_pages(data: bytes, zoom: float, max_pages: int) -> list[Image.Image]:
    if is_pdf(data):
        return pdf_to_images(data, zoom, max_pages)
    return load_image_file(data)


def estimate_skew_angle(bgr: np.ndarray, max_angle: float = 12.0) -> float:
    """מעריך את זווית ההטיה (במעלות) לפי קווים כמעט-אופקיים. 0 אם אין ודאות."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    scale = 1000.0 / max(gray.shape)
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 720, threshold=80,
        minLineLength=int(gray.shape[1] * 0.25), maxLineGap=10,
    )
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(a) <= max_angle:
            angles.append(a)
    if len(angles) < 3:
        return 0.0
    return float(np.median(angles))


def crop_margins(bgr: np.ndarray, pad_ratio: float = 0.02) -> np.ndarray:
    """חותך שוליים לבנים סביב תוכן הטופס, עם מרווח ביטחון קטן.

    שורה/עמודה נחשבת ״תוכן״ אם יש בה מספיק פיקסלים כהים (כדי שגרגירי סריקה בודדים
    לא ימנעו את החיתוך). אם לא נמצא תוכן, או שהחיתוך זניח - מחזירים כמו שהוא.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dark = gray < 200
    h, w = dark.shape
    rows = np.where(dark.sum(axis=1) > w * 0.003)[0]
    cols = np.where(dark.sum(axis=0) > h * 0.003)[0]
    if rows.size == 0 or cols.size == 0:
        return bgr
    pad = int(max(h, w) * pad_ratio)
    top, bottom = max(0, rows[0] - pad), min(h, rows[-1] + pad + 1)
    left, right = max(0, cols[0] - pad), min(w, cols[-1] + pad + 1)
    if (bottom - top) * (right - left) > 0.95 * h * w:
        return bgr
    return bgr[top:bottom, left:right]


def _rotate(bgr: np.ndarray, angle: float) -> np.ndarray:
    h, w = bgr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        bgr, m, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
    )


def preprocess(img: Image.Image, max_side: int = 2200,
               deskew: bool = True, enhance: bool = True) -> Image.Image:
    """חיתוך שוליים, הקטנה (אם צריך), יישור הטיה קלה, ושיפור ניגודיות וחדות - בצבע.

    בכל כשל מחזירים את התמונה המקורית: עדיף תמונה לא מושלמת מאשר אין תמונה.
    """
    try:
        bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
        bgr = crop_margins(bgr)
        h, w = bgr.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1:
            bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if deskew:
            angle = estimate_skew_angle(bgr)
            if abs(angle) >= 0.4:
                bgr = _rotate(bgr, angle)
        if enhance:
            lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
            bgr = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
            blurred = cv2.GaussianBlur(bgr, (0, 0), 1.2)
            bgr = cv2.addWeighted(bgr, 1.5, blurred, -0.5, 0)
        return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    except Exception:
        log.exception("preprocess failed - using original image")
        return img.convert("RGB")


def to_jpeg(img: Image.Image, quality: int = 90, max_side: int | None = None) -> bytes:
    if max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
