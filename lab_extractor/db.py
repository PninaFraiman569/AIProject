"""שכבת מסד הנתונים (SQLite).

כל הגישה למסד עוברת דרך המחלקה Database. כדי לעבור בעתיד ל-PostgreSQL
(למשל באחסון בענן) מספיק להחליף את המימוש כאן, בלי לגעת בשאר הקוד.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .schemas import DATA_FIELDS, STATUS_APPROVED, STATUS_PENDING, STATUS_REVIEW

_COLUMNS = (
    "source_file", *DATA_FIELDS, "needs_review", "review_reason",
    "uncertain_fields", "status",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commitments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file       TEXT,
    first_name        TEXT DEFAULT '',
    last_name         TEXT DEFAULT '',
    id_number         TEXT DEFAULT '',
    central_number    TEXT DEFAULT '',
    received_date     TEXT DEFAULT '',
    sending_entity    TEXT DEFAULT '',
    commitment_number TEXT DEFAULT '',
    needs_review      INTEGER NOT NULL DEFAULT 0,
    review_reason     TEXT DEFAULT '',
    uncertain_fields  TEXT DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'ממתין',
    created_at        TEXT DEFAULT (datetime('now'))
);
"""

_UPDATABLE = set(DATA_FIELDS) | {"needs_review", "review_reason", "uncertain_fields", "status"}


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ---- כתיבה ----
    def insert_many(self, rows: list[dict]) -> list[int]:
        ids: list[int] = []
        sql = (
            f"INSERT INTO commitments ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
        )
        with self._conn() as c:
            for row in rows:
                cur = c.execute(sql, [row.get(col, "") for col in _COLUMNS])
                ids.append(int(cur.lastrowid))
        return ids

    def update(self, record_id: int, values: dict) -> None:
        values = {k: v for k, v in values.items() if k in _UPDATABLE}
        if not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        with self._conn() as c:
            c.execute(f"UPDATE commitments SET {sets} WHERE id = ?", [*values.values(), record_id])

    def approve(self, record_id: int) -> None:
        self.update(record_id, {"status": STATUS_APPROVED, "needs_review": 0, "uncertain_fields": ""})

    def unapprove(self, record_id: int) -> None:
        self.update(record_id, {"status": STATUS_PENDING})

    def approve_all_clean(self) -> int:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE commitments SET status = ? WHERE needs_review = 0 AND status != ?",
                (STATUS_APPROVED, STATUS_APPROVED),
            )
            return cur.rowcount

    def delete(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conn() as c:
            c.executemany("DELETE FROM commitments WHERE id = ?", [(int(i),) for i in ids])

    def delete_all(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM commitments")

    # ---- קריאה ----
    def fetch_all(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM commitments ORDER BY id")]

    def counts(self) -> dict:
        with self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(status = ?), 0) AS approved, "
                "COALESCE(SUM(needs_review = 1 AND status != ?), 0) AS review "
                "FROM commitments",
                (STATUS_APPROVED, STATUS_APPROVED),
            ).fetchone()
        total, approved, review = r["total"], int(r["approved"]), int(r["review"])
        return {"total": total, "approved": approved, "review": review,
                "pending": total - approved - review}


__all__ = ["Database", "STATUS_APPROVED", "STATUS_PENDING", "STATUS_REVIEW"]
