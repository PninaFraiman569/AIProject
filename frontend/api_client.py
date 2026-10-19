"""כל הפניות מהממשק לבקאנד עוברות דרך הקובץ הזה.

הממשק לא ניגש למסד ולא ל-Gemini - רק לבקאנד, דרך BACKEND_URL.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

try:  # קובץ .env בתיקייה הראשית של הפרויקט (מקומית בלבד)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # pragma: no cover
    pass

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = 90  # בתוכנית החינמית של Render השרת "מתעורר" בפנייה הראשונה, וזה לוקח זמן


class ApiError(Exception):
    """שגיאה מהבקאנד. ההודעה בעברית ומוצגת כמו שהיא."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class AuthExpired(ApiError):
    """ההתחברות פגה - צריך להיכנס מחדש."""


class Api:
    def __init__(self, token: str | None = None):
        self.token = token

    # ------------------------------------------------------------------ בסיס
    def _request(self, method: str, path: str, **kw):
        headers = kw.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = requests.request(method, f"{BACKEND_URL}{path}", headers=headers,
                                 timeout=kw.pop("timeout", TIMEOUT), **kw)
        except requests.RequestException:
            raise ApiError("אין חיבור לשרת. נסי שוב בעוד רגע.")
        if r.status_code == 401 and path != "/api/auth/login":
            raise AuthExpired("ההתחברות פגה. יש להיכנס מחדש.", 401)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = None
            if not isinstance(detail, str):
                detail = f"שגיאת שרת ({r.status_code})"
            raise ApiError(detail, r.status_code)
        return r

    def _json(self, method: str, path: str, **kw):
        return self._request(method, path, **kw).json()

    # ------------------------------------------------------------------ כניסה
    def login(self, username: str, password: str) -> str:
        data = self._json("POST", "/api/auth/login",
                          json={"username": username, "password": password})
        self.token = data["token"]
        return self.token

    def logout(self) -> None:
        try:
            self._request("POST", "/api/auth/logout")
        except ApiError:
            pass
        self.token = None

    # ------------------------------------------------------------------ נתונים
    def config(self) -> dict:
        return self._json("GET", "/api/config")

    def stats(self) -> dict:
        return self._json("GET", "/api/stats")

    def records(self, view: str = "all") -> list[dict]:
        return self._json("GET", "/api/records", params={"view": view})

    def edit(self, record_id: int, fields: dict) -> dict:
        return self._json("PATCH", f"/api/records/{record_id}", json={"fields": fields})

    def approve(self, record_id: int) -> None:
        self._request("POST", f"/api/records/{record_id}/approve")

    def unapprove(self, record_id: int) -> None:
        self._request("POST", f"/api/records/{record_id}/unapprove")

    def approve_clean(self) -> int:
        return self._json("POST", "/api/records/approve-clean")["approved"]

    def delete(self, ids: list[int]) -> int:
        return self._json("POST", "/api/records/delete", json={"ids": ids})["deleted"]

    def delete_all(self) -> int:
        return self._json("POST", "/api/records/delete-all")["deleted"]

    # ------------------------------------------------------------------ עיבוד
    def start_job(self, files: list[tuple[str, bytes, str]]) -> dict:
        payload = [("files", (name, data, mime or "application/octet-stream"))
                   for name, data, mime in files]
        return self._json("POST", "/api/jobs", files=payload, timeout=300)

    def job(self, job_id: str) -> dict:
        return self._json("GET", f"/api/jobs/{job_id}")

    # ------------------------------------------------------------------ ייצוא
    def export(self, scope: str, include_source: bool) -> bytes:
        r = self._request("GET", "/api/export",
                          params={"scope": scope, "include_source": include_source})
        return r.content
