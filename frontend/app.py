"""ממשק Streamlit: כניסה -> העלאת טפסים -> בקרה ועריכה -> ייצוא לאקסל.

הממשק מציג בלבד. כל העיבוד, השמירה והייצוא נעשים בבקאנד (api_client.py).
העיצוב נמצא ב-styles/style.css.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from st_aggrid import AgGrid, JsCode

from api_client import Api, ApiError, AuthExpired

st.set_page_config(page_title="חילוץ טפסי התחייבות", page_icon="🧪", layout="wide")

STYLE_PATH = Path(__file__).parent / "styles" / "style.css"

# שדות הנתונים, לפי סדר העמודות (זהה לבקאנד)
DATA_FIELDS = ("first_name", "last_name", "id_number", "central_numbers",
               "received_date", "sending_entity", "commitment_numbers")
LIST_FIELDS = ("central_numbers", "commitment_numbers")
LIST_SEPARATOR = ", "
FIELD_LABELS = {
    "first_name": "שם פרטי", "last_name": "שם משפחה", "id_number": "ת.ז.",
    "central_numbers": "מספר מרכזי", "received_date": "תאריך קבלה",
    "sending_entity": "גורם שולח", "commitment_numbers": "מספר התחייבות",
}
STATUS_APPROVED = "אושר"


@st.cache_data
def load_css() -> str:
    return STYLE_PATH.read_text(encoding="utf-8")


st.markdown(f"<style>{load_css()}</style>", unsafe_allow_html=True)

# Streamlit מסמן את הדף כאנגלית, ולכן Chrome מציע לתרגם אותו. התרגום משנה את
# מבנה הדף ו-Streamlit קורס (NotFoundError: removeChild). מסמנים: עברית, בלי תרגום.
components.html(
    """<script>
    const root = window.parent.document.documentElement;
    root.setAttribute("lang", "he");
    root.setAttribute("translate", "no");
    root.classList.add("notranslate");
    const head = window.parent.document.head;
    if (!head.querySelector('meta[name="google"]')) {
      const m = window.parent.document.createElement("meta");
      m.name = "google"; m.content = "notranslate";
      head.appendChild(m);
    }
    </script>""",
    height=0,
)

st.session_state.setdefault("token", None)
st.session_state.setdefault("uploader_ver", 0)
st.session_state.setdefault("editor_ver", 0)
st.session_state.setdefault("flash", None)


def flash(kind: str, text: str) -> None:
    st.session_state.flash = (kind, text)


def show_flash() -> None:
    if st.session_state.flash:
        kind, text = st.session_state.flash
        getattr(st, kind)(text)
        st.session_state.flash = None


# ================================================================== כניסה
def login_screen() -> None:
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown(
            '<div class="login-wrap"><div class="login-card">'
            '<h1 class="app-title">טפסי התחייבות לאקסל</h1>'
            '<p class="app-sub">כניסה למערכת המעבדה המרכזית לנגיפים</p>'
            '</div></div>',
            unsafe_allow_html=True,
        )
        show_flash()
        with st.form("login"):
            username = st.text_input("שם משתמש")
            password = st.text_input("סיסמה", type="password")
            submitted = st.form_submit_button("כניסה", type="primary", use_container_width=True)
        if submitted:
            api = Api()
            try:
                with st.spinner("מתחבר…"):
                    st.session_state.token = api.login(username, password)
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))


# ================================================================== טבלה
# הטבלה היא AG Grid (streamlit-aggrid): רק בה אפשר גם לערוך תא וגם לצבוע אותו.
# תא שהמנוע לא בטוח בו (ויש בו ערך) - רקע צהוב-כתום זוהר. תא ריק לא נצבע; מה שלא
# חולץ מופיע בעמודת ההערות.
GRID_COLUMNS = ["id", "_unsure", "flag", *DATA_FIELDS, "approved", "notes", "source_file", "delete"]

UNSURE_CELL_STYLE = JsCode("""
function(params) {
  const unsure = ((params.data && params.data._unsure) || '').split(',');
  if (unsure.includes(params.colDef.field)) {
    return {backgroundColor: '#FFD60A', color: '#3B2A00', fontWeight: '700',
            boxShadow: 'inset 0 0 0 2px #FF9500'};
  }
  return null;
}
""")


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else False


def _notes(row) -> str:
    """עמודת ההערות: הסבר המנוע + מה לא חולץ + מה צריך לבדוק."""
    if row["approved"]:
        return ""
    parts = [p for p in _norm(row["review_reason"]).split("; ")
             if p and not p.startswith("שדות חסרים")]   # רשימת החסרים מחושבת כאן מחדש
    missing = [FIELD_LABELS[f] for f in DATA_FIELDS if not _norm(row[f])]
    if missing:
        parts.append("לא חולץ: " + ", ".join(missing))
    if row["_unsure"]:
        parts.append("יש לבדוק ולוודא את תקינות: "
                     + ", ".join(FIELD_LABELS[f] for f in row["_unsure"].split(",")))
    return ". ".join(parts)


def rows_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for f in LIST_FIELDS:   # רשימת מספרים -> טקסט מופרד בפסיק
        df[f] = df[f].apply(lambda v: LIST_SEPARATOR.join(v or []))
    df["approved"] = df["status"].eq(STATUS_APPROVED)
    df["flag"] = [
        "✅" if a else ("⚠️" if n else "")
        for a, n in zip(df["approved"], df["needs_review"].astype(bool))
    ]
    # תאים לצביעה: שדות שהמנוע לא בטוח בהם ויש בהם ערך (תא ריק = ״לא חולץ״, בלי צבע)
    df["_unsure"] = [
        "" if a else ",".join(f for f in DATA_FIELDS if f in (u or []) and _norm(r[f]))
        for a, u, (_, r) in zip(df["approved"], df["uncertain_fields"], df.iterrows())
    ]
    df["notes"] = df.apply(_notes, axis=1)
    df["delete"] = False
    df.index = df["id"]
    df.index.name = None
    return df


def grid_options(entities: list[str]) -> dict:
    data_cols = []
    for f in DATA_FIELDS:
        col = {"field": f, "headerName": FIELD_LABELS[f], "editable": True,
               "cellStyle": UNSURE_CELL_STYLE, "minWidth": 110}
        if f == "sending_entity":
            col.update(cellEditor="agSelectCellEditor",
                       cellEditorParams={"values": ["", *entities]}, minWidth=150)
        if f in LIST_FIELDS:
            col["headerTooltip"] = "כמה מספרים - מופרדים בפסיק"
        if f == "received_date":
            col["headerTooltip"] = "DD/MM/YYYY"
        data_cols.append(col)
    return {
        "enableRtl": True,                  # עמודה ראשונה מימין
        "singleClickEdit": True,
        "stopEditingWhenCellsLoseFocus": True,
        "defaultColDef": {"resizable": True, "sortable": True, "filter": False},
        "columnDefs": [
            {"field": "id", "hide": True},
            {"field": "_unsure", "hide": True},
            {"field": "flag", "headerName": "", "width": 60, "editable": False},
            *data_cols,
            {"field": "approved", "headerName": "אושר", "editable": True,
             "cellDataType": "boolean", "width": 80},
            {"field": "notes", "headerName": "הערות", "editable": False,
             "wrapText": True, "autoHeight": True, "minWidth": 260, "flex": 1},
            {"field": "source_file", "headerName": "קובץ מקור", "editable": False},
            {"field": "delete", "headerName": "מחיקה", "editable": True,
             "cellDataType": "boolean", "width": 80},
        ],
    }


def save_changes(api: Api, original: pd.DataFrame, edited: pd.DataFrame) -> int:
    """משווה את הטבלה אחרי העריכה למקור, ושולח לבקאנד רק את מה שהשתנה.

    תיקון תא מוריד את הכתום רק ממנו. השורה מאושרת כשמסמנים ״אושר״, או
    אוטומטית כשאין בה יותר תאים כתומים (הבקאנד מחליט).
    """
    changed, errors = 0, []
    for rid in original.index:
        if rid not in edited.index:
            continue
        old, new = original.loc[rid], edited.loc[rid]
        fields = {f: _norm(new[f]) for f in DATA_FIELDS if _norm(old[f]) != _norm(new[f])}
        try:
            if fields:
                api.edit(int(rid), fields)
                changed += 1
            if _bool(old["approved"]) != _bool(new["approved"]):
                api.approve(int(rid)) if _bool(new["approved"]) else api.unapprove(int(rid))
                changed += 1
        except AuthExpired:
            raise
        except ApiError as exc:
            errors.append(f"שורה #{rid}: {exc}")
    if errors:
        flash("warning", "לא נשמר:\n" + "\n".join(f"- {e}" for e in errors))
        changed = max(changed, 1)
    return changed


# ================================================================== המסך הראשי
def main_screen(api: Api) -> None:
    # ההגדרות (רשימת גורמים שולחים וכו') לא משתנות בזמן ההתחברות - נטענות פעם אחת
    if st.session_state.get("cfg") is None:
        st.session_state.cfg = api.config()
    cfg = st.session_state.cfg

    head, user_col = st.columns([5, 1])
    with head:
        st.markdown('<h1 class="app-title">טפסי התחייבות לאקסל</h1>', unsafe_allow_html=True)
    with user_col:
        if st.button("יציאה"):
            api.logout()
            st.session_state.token = None
            st.session_state.cfg = None
            st.rerun()
    st.markdown(
        '<p class="app-sub">גוררים טפסים סרוקים, מצולמים או כתובים ביד - המנוע מחלץ את השדות, '
        'מסמן בכתום כל מה שהוא לא בטוח בו, ואחרי בדיקה מייצאים קובץ אקסל.</p>',
        unsafe_allow_html=True,
    )

    counts = api.stats()
    st.markdown(
        f"""<div class="stats">
          <div class="stat"><div class="n">{counts['total']}</div><div class="l">סה״כ התחייבויות</div></div>
          <div class="stat review"><div class="n">{counts['review']}</div><div class="l">ממתינות לבדיקה</div></div>
          <div class="stat"><div class="n">{counts['pending']}</div><div class="l">תקינות, טרם אושרו</div></div>
          <div class="stat ok"><div class="n">{counts['approved']}</div><div class="l">אושרו</div></div>
        </div>""",
        unsafe_allow_html=True,
    )
    show_flash()

    upload_section(api, cfg)
    review_section(api, cfg)
    export_section(api, counts)
    data_section(api)


# ------------------------------------------------------------------ 1. העלאה
def upload_section(api: Api, cfg: dict) -> None:
    st.markdown('<div class="panel-title">1. העלאת טפסים</div>', unsafe_allow_html=True)
    files = st.file_uploader(
        "גוררים לכאן קובצי PDF או תמונות (אפשר כמה יחד)",
        type=["pdf", "png", "jpg", "jpeg", "heic", "webp", "tif", "tiff"],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_ver}",
    )
    if not cfg.get("api_ready"):
        st.error("בשרת לא הוגדר מפתח GEMINI_API_KEY. מגדירים אותו בהגדרות הבקאנד ומפעילים מחדש.")

    clicked = st.button("חלץ נתונים מהטפסים", type="primary",
                        disabled=not files or not cfg.get("api_ready"))
    if not files:
        st.info("📄 יש להעלות טפסים כדי לחלץ נתונים. גוררים קבצים לתיבה שלמעלה, ואז הכפתור יופעל.")
    if clicked:
        bar = st.progress(0.0, text="שולח את הקבצים לשרת…")
        job = api.start_job([(f.name, f.getvalue(), f.type) for f in files])
        while job["status"] == "running":
            time.sleep(1)
            job = api.job(job["id"])
            if job["file_name"]:
                text = (f"({job['file_index']}/{job['files_total']}) {job['file_name']}"
                        f" - עמוד {job['page_done']} מתוך {job['page_total'] or '?'}")
            else:
                text = "מתחיל…"
            bar.progress(min(job["progress"], 1.0), text=f"מעבד {text}")
        bar.empty()

        if job["status"] == "error":
            flash("error", f"העיבוד נכשל: {job['error']}")
        else:
            msg = f"נוספו {job['created']} רשומות."
            if job["failed"]:
                msg += " לא עובדו:\n" + "\n".join(f"- {x}" for x in job["failed"])
                flash("warning", msg)
            else:
                flash("success", msg)
        st.session_state.uploader_ver += 1
        st.rerun()


# ------------------------------------------------------------------ 2. בדיקה ותיקון
def review_section(api: Api, cfg: dict) -> None:
    st.markdown('<div class="panel-title">2. בדיקה ותיקון</div>', unsafe_allow_html=True)
    rows = api.records()
    if not rows:
        st.info("עדיין אין רשומות. מעלים טפסים למעלה, והתוצאות יופיעו כאן.")
        return

    view = st.radio("תצוגה", ["הכול", "לבדיקה", "אושרו"], horizontal=True,
                    label_visibility="collapsed")
    st.markdown(
        '<div class="legend"><span class="sw cell"></span>שדה שהמנוע לא בטוח בו - '
        'יש לבדוק ולוודא את תקינותו. מה שלא חולץ מופיע בעמודת ״הערות״. '
        'תיקון התא מוריד ממנו את הסימון; השורה מאושרת כשמסמנים ״אושר״ '
        'או כשאין בה יותר שדות מסומנים. כמה מספרים מרכזיים - מפרידים בפסיק.</div>',
        unsafe_allow_html=True,
    )
    df = rows_to_df(rows)
    if view == "לבדיקה":
        df = df[df["needs_review"].astype(bool) & ~df["approved"]]
    elif view == "אושרו":
        df = df[df["approved"]]
    if df.empty:
        st.caption("אין רשומות בתצוגה הזו.")
        return

    response = AgGrid(
        df[GRID_COLUMNS].reset_index(drop=True),
        gridOptions=grid_options(cfg["entities"]),
        height=min(620, 90 + 48 * len(df)),
        allow_unsafe_jscode=True,          # נדרש לצביעת התאים (UNSURE_CELL_STYLE)
        update_on=["cellValueChanged"],
        data_return_mode="AS_INPUT",
        theme="streamlit",
        show_search=False,
        show_download_button=False,
        key=f"grid_{st.session_state.editor_ver}_{view}",
    )
    edited = response.data if response is not None and response.data is not None else df
    edited = edited.set_index(edited["id"].astype(int), drop=False)
    edited.index.name = None

    if save_changes(api, df, edited):
        st.session_state.editor_ver += 1
        st.rerun()

    c1, c2, _ = st.columns([1.3, 1.3, 3])
    with c1:
        if st.button("אשר את כל התקינות"):
            n = api.approve_clean()
            flash("success", f"אושרו {n} רשומות תקינות.")
            st.session_state.editor_ver += 1
            st.rerun()
    with c2:
        marked = [int(i) for i in edited.index[edited["delete"].map(_bool)]]
        if st.button(f"מחק מסומנות ({len(marked)})", disabled=not marked):
            api.delete(marked)
            st.session_state.editor_ver += 1
            st.rerun()


# ------------------------------------------------------------------ 3. ייצוא
def export_section(api: Api, counts: dict) -> None:
    st.markdown('<div class="panel-title">3. ייצוא לאקסל</div>', unsafe_allow_html=True)
    scope_label = st.radio("מה לייצא", ["רק רשומות שאושרו", "הכול (שורות לא מאושרות יסומנו)"],
                           horizontal=True, label_visibility="collapsed")
    include_source = st.checkbox("הוסף עמודה עם קובץ מקור")
    approved_only = scope_label.startswith("רק")
    n = counts["approved"] if approved_only else counts["total"]
    scope = "approved" if approved_only else "all"

    st.download_button(
        f"ייצוא לאקסל ({n} שורות)",
        # הקובץ נבנה רק בלחיצה, ולא בכל רענון של המסך
        data=lambda: api.export(scope, include_source),
        file_name=f"התחייבויות_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        disabled=not n,
    )
    if approved_only and not n:
        st.caption("אין עדיין רשומות מאושרות. אשרי רשומות בטבלה, או בחרי ״הכול״.")


# ------------------------------------------------------------------ ניהול נתונים
def data_section(api: Api) -> None:
    with st.expander("ניהול נתונים"):
        st.caption("הנתונים נשמרים במסד עד שמוחקים אותם. תמונות הטפסים לא נשמרות כלל.")
        sure = st.checkbox("אני מבינה שהמחיקה סופית")
        if st.button("מחק את כל הרשומות", disabled=not sure):
            api.delete_all()
            st.session_state.editor_ver += 1
            flash("success", "כל הרשומות נמחקו.")
            st.rerun()


# ================================================================== הרצה
if not st.session_state.token:
    login_screen()
else:
    try:
        main_screen(Api(st.session_state.token))
    except AuthExpired as exc:
        st.session_state.token = None
        flash("info", str(exc))
        st.rerun()
    except ApiError as exc:
        st.error(str(exc))
