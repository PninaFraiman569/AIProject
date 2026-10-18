"""ממשק Streamlit: העלאת טפסים -> חילוץ -> בקרה ועריכה -> ייצוא לאקסל."""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="חילוץ טפסי התחייבות", page_icon="🧪", layout="wide")

from lab_extractor.config import get_settings  # noqa: E402
from lab_extractor.db import Database  # noqa: E402
from lab_extractor.entities import EntityList  # noqa: E402
from lab_extractor.exporter import build_workbook  # noqa: E402
from lab_extractor.extractor import ExtractionError, GeminiExtractor  # noqa: E402
from lab_extractor.image_utils import UnsupportedFile  # noqa: E402
from lab_extractor.pipeline import process_upload  # noqa: E402
from lab_extractor.schemas import (  # noqa: E402
    DATA_FIELDS, FIELD_LABELS, STATUS_APPROVED,
)
from lab_extractor.ui_style import CSS  # noqa: E402
from lab_extractor.validation import normalize_date, normalize_id  # noqa: E402

logging.basicConfig(level=logging.INFO)  # לא רושמים פרטים אישיים ביומן
st.markdown(CSS, unsafe_allow_html=True)

# סדר העמודות בטבלה מתהפך כדי שהעמודה הראשונה תופיע בצד ימין (קריאה בעברית)
REVERSE_COLUMNS = True


# ------------------------------------------------------------------ משאבים משותפים
@st.cache_resource
def get_db() -> Database:
    return Database(get_settings().db_path)


@st.cache_resource
def get_entities() -> EntityList:
    return EntityList.from_file(get_settings().entities_path)


@st.cache_resource
def get_extractor() -> GeminiExtractor:
    return GeminiExtractor(get_settings(), get_entities().names)


settings = get_settings()
db = get_db()
entities = get_entities()

st.session_state.setdefault("previews", {})       # id -> [jpeg bytes] (בזיכרון בלבד)
st.session_state.setdefault("uploader_ver", 0)
st.session_state.setdefault("editor_ver", 0)
st.session_state.setdefault("flash", None)


# ------------------------------------------------------------------ כותרת ומדדים
st.markdown('<h1 class="app-title">טפסי התחייבות לאקסל</h1>', unsafe_allow_html=True)
st.markdown(
    '<p class="app-sub">גוררים טפסים סרוקים, מצולמים או כתובים ביד - המנוע מחלץ את השדות, '
    'מסמן בכתום כל מה שהוא לא בטוח בו, ואחרי בדיקה מייצאים קובץ אקסל.</p>',
    unsafe_allow_html=True,
)

counts = db.counts()
st.markdown(
    f"""<div class="stats">
      <div class="stat"><div class="n">{counts['total']}</div><div class="l">סה״כ התחייבויות</div></div>
      <div class="stat review"><div class="n">{counts['review']}</div><div class="l">ממתינות לבדיקה</div></div>
      <div class="stat"><div class="n">{counts['pending']}</div><div class="l">תקינות, טרם אושרו</div></div>
      <div class="stat ok"><div class="n">{counts['approved']}</div><div class="l">אושרו</div></div>
    </div>""",
    unsafe_allow_html=True,
)

if st.session_state.flash:
    kind, text = st.session_state.flash
    getattr(st, kind)(text)
    st.session_state.flash = None


# ------------------------------------------------------------------ העלאה ועיבוד
st.markdown('<div class="panel-title">1. העלאת טפסים</div>', unsafe_allow_html=True)
files = st.file_uploader(
    "גוררים לכאן קובצי PDF או תמונות (אפשר כמה יחד)",
    type=["pdf", "png", "jpg", "jpeg", "heic", "webp", "tif", "tiff"],
    accept_multiple_files=True,
    key=f"uploader_{st.session_state.uploader_ver}",
)

api_ready = bool(settings.gemini_api_key)
if not api_ready:
    st.error(
        "לא הוגדר מפתח API. מגדירים את GEMINI_API_KEY כמשתנה סביבה (או ב-.env / "
        "st.secrets) ומפעילים מחדש."
    )

if st.button("חלץ נתונים מהטפסים", type="primary", disabled=not files or not api_ready):
    try:
        extractor = get_extractor()
    except ExtractionError as exc:
        st.error(str(exc))
        st.stop()

    created, failed = 0, []
    bar = st.progress(0.0, text="מתחיל…")
    for f_idx, f in enumerate(files):
        label = f"({f_idx + 1}/{len(files)}) {f.name}"

        def on_progress(done: int, total: int, _i=f_idx, _label=label):
            frac = (_i + done / max(total, 1)) / len(files)
            bar.progress(min(frac, 1.0), text=f"מעבד {_label} - עמוד {done} מתוך {total}")

        try:
            records = process_upload(f.name, f.getvalue(), extractor, entities,
                                     settings, on_progress=on_progress)
            ids = db.insert_many([r.to_row() for r in records])
            for rid, rec in zip(ids, records):
                st.session_state.previews[rid] = rec.previews
            created += len(ids)
        except UnsupportedFile as exc:
            failed.append(f"{f.name}: {exc}")
        except Exception as exc:  # קובץ בעייתי לא מפיל את שאר האצווה
            logging.exception("file failed")
            failed.append(f"{f.name}: שגיאה לא צפויה ({type(exc).__name__})")
    bar.empty()

    msg = f"נוספו {created} רשומות."
    if failed:
        msg += " לא עובדו:\n" + "\n".join(f"- {x}" for x in failed)
        st.session_state.flash = ("warning", msg)
    else:
        st.session_state.flash = ("success", msg)
    st.session_state.uploader_ver += 1
    st.rerun()


# ------------------------------------------------------------------ טבלת בקרה
def rows_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["approved"] = df["status"].eq(STATUS_APPROVED)
    df["flag"] = [
        "✅" if a else ("⚠️" if n else "")
        for a, n in zip(df["approved"], df["needs_review"].astype(bool))
    ]
    df["delete"] = False
    df.index = df["id"]
    df.index.name = None
    return df


def highlight(df: pd.DataFrame) -> pd.DataFrame:
    """רקע בהיר לשורה שדורשת בדיקה, וכתום חזק לתאים שהמנוע לא בטוח בהם."""
    styles = pd.DataFrame("", index=df.index, columns=df.columns)
    for idx, row in df.iterrows():
        if row["needs_review"] and not row["approved"]:
            styles.loc[idx, :] = "background-color: #FFF7E6"
            for f in str(row["uncertain_fields"]).split(","):
                if f in styles.columns:
                    styles.loc[idx, f] = "background-color: #FDBA74; font-weight: 600"
    return styles


def _norm(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def save_changes(original: pd.DataFrame, edited: pd.DataFrame) -> int:
    """משווה את הטבלה אחרי העריכה למקור, ושומר במסד. עריכת שדה => סטטוס 'אושר'."""
    changed = 0
    for rid in original.index:
        if rid not in edited.index:
            continue
        old, new = original.loc[rid], edited.loc[rid]
        data_changed = {f: _norm(new[f]) for f in DATA_FIELDS if _norm(old[f]) != _norm(new[f])}
        if data_changed:
            if "id_number" in data_changed:
                data_changed["id_number"] = normalize_id(data_changed["id_number"], settings)[0] or data_changed["id_number"]
            if "received_date" in data_changed:
                data_changed["received_date"] = normalize_date(data_changed["received_date"])[0]
            db.update(int(rid), {**data_changed, "status": STATUS_APPROVED,
                                 "needs_review": 0, "uncertain_fields": ""})
            changed += 1
        elif bool(old["approved"]) != bool(new["approved"]):
            db.approve(int(rid)) if new["approved"] else db.unapprove(int(rid))
            changed += 1
    return changed


st.markdown('<div class="panel-title">2. בדיקה ותיקון</div>', unsafe_allow_html=True)
rows = db.fetch_all()

if not rows:
    st.info("עדיין אין רשומות. מעלים טפסים למעלה, והתוצאות יופיעו כאן.")
else:
    view = st.radio("תצוגה", ["הכול", "לבדיקה", "אושרו"], horizontal=True, label_visibility="collapsed")
    st.markdown(
        '<div class="legend"><span class="sw row"></span>שורה לבדיקה'
        '&nbsp;&nbsp;<span class="sw cell"></span>שדה שהמנוע לא בטוח בו. '
        'עריכה של תא מאשרת את השורה אוטומטית.</div>',
        unsafe_allow_html=True,
    )
    df = rows_to_df(rows)
    if view == "לבדיקה":
        df = df[df["needs_review"].astype(bool) & ~df["approved"]]
    elif view == "אושרו":
        df = df[df["approved"]]

    if df.empty:
        st.caption("אין רשומות בתצוגה הזו.")
    else:
        # ערך ריק בעמודת הבחירה מוצג כריק; חוזר למחרוזת ריקה בשמירה
        df["sending_entity"] = df["sending_entity"].replace("", None)
        order = ["flag", *DATA_FIELDS, "approved", "review_reason", "source_file", "delete"]
        if REVERSE_COLUMNS:
            order = order[::-1]
        config = {
            "flag": st.column_config.TextColumn("", width="small", disabled=True),
            "first_name": st.column_config.TextColumn(FIELD_LABELS["first_name"]),
            "last_name": st.column_config.TextColumn(FIELD_LABELS["last_name"]),
            "id_number": st.column_config.TextColumn(FIELD_LABELS["id_number"]),
            "central_number": st.column_config.TextColumn(FIELD_LABELS["central_number"]),
            "received_date": st.column_config.TextColumn(FIELD_LABELS["received_date"], help="DD/MM/YYYY"),
            "sending_entity": st.column_config.SelectboxColumn(
                FIELD_LABELS["sending_entity"], options=entities.names, width="medium"),
            "commitment_number": st.column_config.TextColumn(FIELD_LABELS["commitment_number"]),
            "approved": st.column_config.CheckboxColumn("אושר"),
            "review_reason": st.column_config.TextColumn("סיבת הבדיקה", disabled=True, width="large"),
            "source_file": st.column_config.TextColumn("קובץ מקור", disabled=True),
            "delete": st.column_config.CheckboxColumn("מחיקה"),
        }
        kwargs = dict(
            column_order=order, column_config=config, hide_index=True,
            width="stretch", num_rows="fixed",
            key=f"editor_{st.session_state.editor_ver}_{view}",
        )
        try:  # הדגשת שורות/תאים; אם הגרסה לא תומכת ב-Styler - טבלה רגילה
            edited = st.data_editor(df.style.apply(highlight, axis=None), **kwargs)
        except Exception:
            edited = st.data_editor(df, **kwargs)

        if save_changes(df, edited):
            st.session_state.editor_ver += 1
            st.rerun()

        c1, c2, c3 = st.columns([1.3, 1.3, 3])
        with c1:
            if st.button("אשר את כל התקינות"):
                n = db.approve_all_clean()
                st.session_state.flash = ("success", f"אושרו {n} רשומות תקינות.")
                st.session_state.editor_ver += 1
                st.rerun()
        with c2:
            marked = [int(i) for i in edited.index[edited["delete"].astype(bool)]]
            if st.button(f"מחק מסומנות ({len(marked)})", disabled=not marked):
                db.delete(marked)
                for i in marked:
                    st.session_state.previews.pop(i, None)
                st.session_state.editor_ver += 1
                st.rerun()

        # צפייה בטופס המקורי - קיים בזיכרון רק עבור העלאות של הסשן הנוכחי
        with st.expander("הצגת הטופס המקורי לצד השורה"):
            options = [int(i) for i in df.index]
            pick = st.selectbox(
                "בחרי שורה", options,
                format_func=lambda i: f"#{i} · {df.loc[i, 'source_file']} · "
                                      f"{_norm(df.loc[i, 'last_name'])} {_norm(df.loc[i, 'first_name'])}",
            )
            imgs = st.session_state.previews.get(pick)
            if imgs:
                for img in imgs:
                    st.image(img, width="stretch")
            else:
                st.caption("התמונה זמינה רק להעלאות מהסשן הנוכחי (לא נשמרת בדיסק, מטעמי פרטיות).")


# ------------------------------------------------------------------ ייצוא
st.markdown('<div class="panel-title">3. ייצוא לאקסל</div>', unsafe_allow_html=True)
scope = st.radio("מה לייצא", ["רק רשומות שאושרו", "הכול (שורות לא מאושרות יסומנו)"], horizontal=True,
                 label_visibility="collapsed")
include_source = st.checkbox("הוסף עמודה עם קובץ מקור")
all_rows = db.fetch_all()
approved_only = scope.startswith("רק")
export_rows = [r for r in all_rows if r["status"] == STATUS_APPROVED] if approved_only else all_rows

st.download_button(
    f"ייצוא לאקסל ({len(export_rows)} שורות)",
    data=build_workbook(export_rows, include_source=include_source, mark_review=not approved_only)
    if export_rows else b"",
    file_name=f"התחייבויות_{datetime.now():%Y%m%d_%H%M}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    disabled=not export_rows,
)
if approved_only and not export_rows:
    st.caption("אין עדיין רשומות מאושרות. אשרי רשומות בטבלה, או בחרי ״הכול״.")

# ------------------------------------------------------------------ ניקוי נתונים
with st.expander("ניהול נתונים"):
    st.caption("הנתונים נשמרים במסד המקומי עד שתמחקי אותם.")
    sure = st.checkbox("אני מבינה שהמחיקה סופית")
    if st.button("מחק את כל הרשומות", disabled=not sure):
        db.delete_all()
        st.session_state.previews.clear()
        st.session_state.editor_ver += 1
        st.session_state.flash = ("success", "כל הרשומות נמחקו.")
        st.rerun()
