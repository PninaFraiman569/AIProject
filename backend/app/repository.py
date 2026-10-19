"""שכבת הגישה למסד הנתונים (PostgreSQL / Supabase).

כל הגישה למסד עוברת דרך המחלקה Repository - אף קובץ אחר לא כותב SQL.
מבנה הטבלאות עצמו נמצא ב-db/schema.sql.
"""
from __future__ import annotations

import logging
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .core.config import PROJECT_DIR
from .core.schemas import DATA_FIELDS, STATUS_APPROVED, STATUS_PENDING, STATUS_REVIEW

log = logging.getLogger(__name__)

SCHEMA_PATH = PROJECT_DIR / "db" / "schema.sql"

_INSERT_COLUMNS = (
    "source_file", *DATA_FIELDS, "needs_review", "review_reason",
    "uncertain_fields", "status",
)
_UPDATABLE = set(DATA_FIELDS) | {"needs_review", "review_reason", "uncertain_fields", "status"}
_RECORD_COLUMNS = ("id", *_INSERT_COLUMNS, "created_at", "updated_at")


class Repository:
    def __init__(self, database_url: str, max_size: int = 5):
        if not database_url:
            raise RuntimeError("לא הוגדר DATABASE_URL")
        # prepare_threshold=None: נדרש כשעובדים דרך ה-pooler של Supabase
        # autocommit: בלי BEGIN/COMMIT נפרדים - כל שאילתה היא פנייה אחת למסד (המסד רחוק)
        self._pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=max_size,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "autocommit": True},
            open=True,
        )

    def close(self) -> None:
        self._pool.close()

    def init_schema(self, path: Path = SCHEMA_PATH) -> None:
        sql = path.read_text(encoding="utf-8")
        with self._pool.connection() as conn:
            conn.execute(sql)

    # ================================================================ התחייבויות
    def insert_many(self, rows: list[dict]) -> list[int]:
        if not rows:
            return []
        sql = (
            f"INSERT INTO commitments ({', '.join(_INSERT_COLUMNS)}) "
            f"VALUES ({', '.join('%s' for _ in _INSERT_COLUMNS)}) RETURNING id"
        )
        ids: list[int] = []
        with self._pool.connection() as conn, conn.transaction():
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(sql, [row.get(col, _default(col)) for col in _INSERT_COLUMNS])
                    ids.append(int(cur.fetchone()["id"]))
        return ids

    def fetch_all(self, view: str = "all") -> list[dict]:
        where = ""
        params: list = []
        if view == "review":
            where = "WHERE needs_review AND status <> %s"
            params = [STATUS_APPROVED]
        elif view == "approved":
            where = "WHERE status = %s"
            params = [STATUS_APPROVED]
        sql = f"SELECT {', '.join(_RECORD_COLUMNS)} FROM commitments {where} ORDER BY id"
        with self._pool.connection() as conn:
            return [_clean(r) for r in conn.execute(sql, params).fetchall()]

    def get(self, record_id: int) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_RECORD_COLUMNS)} FROM commitments WHERE id = %s",
                [record_id],
            ).fetchone()
        return _clean(row) if row else None

    def update(self, record_id: int, values: dict) -> None:
        values = {k: v for k, v in values.items() if k in _UPDATABLE}
        if not values:
            return
        sets = ", ".join(f"{k} = %s" for k in values)
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE commitments SET {sets}, updated_at = now() WHERE id = %s",
                [*values.values(), record_id],
            )

    def approve(self, record_id: int) -> None:
        self.update(record_id, {"status": STATUS_APPROVED, "needs_review": False,
                                "uncertain_fields": []})

    def unapprove(self, record_id: int) -> None:
        row = self.get(record_id)
        if row:
            status = STATUS_REVIEW if row["needs_review"] else STATUS_PENDING
            self.update(record_id, {"status": status})

    def approve_all_clean(self) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE commitments SET status = %s, updated_at = now() "
                "WHERE NOT needs_review AND status <> %s",
                [STATUS_APPROVED, STATUS_APPROVED],
            )
            return cur.rowcount

    def delete(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._pool.connection() as conn:
            cur = conn.execute("DELETE FROM commitments WHERE id = ANY(%s)",
                               [[int(i) for i in ids]])
            return cur.rowcount

    def delete_all(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("DELETE FROM commitments").rowcount

    def counts(self) -> dict:
        with self._pool.connection() as conn:
            r = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COUNT(*) FILTER (WHERE status = %s) AS approved, "
                "COUNT(*) FILTER (WHERE needs_review AND status <> %s) AS review "
                "FROM commitments",
                [STATUS_APPROVED, STATUS_APPROVED],
            ).fetchone()
        total, approved, review = int(r["total"]), int(r["approved"]), int(r["review"])
        return {"total": total, "approved": approved, "review": review,
                "pending": total - approved - review}

    # ================================================================ משתמשות
    def get_user(self, username: str) -> dict | None:
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT id, username, password_hash FROM users WHERE username = %s",
                [username],
            ).fetchone()

    def upsert_user(self, username: str, password_hash: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
                "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
                [username, password_hash],
            )

    # ================================================================ התחברויות
    def create_session(self, token_hash: str, user_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute("INSERT INTO sessions (token_hash, user_id) VALUES (%s, %s)",
                         [token_hash, user_id])

    def touch_session(self, token_hash: str, max_idle_minutes: int) -> dict | None:
        """מאמת התחברות ומעדכן את זמן הפעילות האחרון. None אם לא קיימת או פגה."""
        # פקודה אחת (פנייה אחת למסד): מחיקת התחברויות שפגו + עדכון ההתחברות הנוכחית
        with self._pool.connection() as conn:
            return conn.execute(
                "WITH expired AS ("
                "  DELETE FROM sessions WHERE last_seen < now() - make_interval(mins => %s)"
                ") "
                "UPDATE sessions s SET last_seen = now() FROM users u "
                "WHERE s.token_hash = %s AND u.id = s.user_id "
                "AND s.last_seen >= now() - make_interval(mins => %s) "
                "RETURNING u.id, u.username",
                [max_idle_minutes, token_hash, max_idle_minutes],
            ).fetchone()

    def delete_session(self, token_hash: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", [token_hash])

    def delete_user_sessions(self, username: str) -> None:
        """אחרי החלפת סיסמה - מנתק את כל ההתחברויות הקיימות."""
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username = %s)",
                [username],
            )


def _default(col: str):
    if col in ("central_numbers", "commitment_numbers", "uncertain_fields"):
        return []
    if col == "needs_review":
        return False
    return ""


def _clean(row: dict) -> dict:
    """ממיר ערכים לפורמט שאפשר לשלוח כ-JSON."""
    out = dict(row)
    for k in ("created_at", "updated_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    for k in ("central_numbers", "commitment_numbers", "uncertain_fields"):
        out[k] = list(out.get(k) or [])
    return out


__all__ = ["Repository", "STATUS_APPROVED", "STATUS_PENDING", "STATUS_REVIEW"]
