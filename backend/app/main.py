"""שרת ה-API (FastAPI).

הפעלה מקומית (מתוך תיקיית backend):
    uvicorn app.main:app --reload --port 8000

כל הכתובות מתחת ל-/api דורשות התחברות, חוץ מ-/api/health ו-/api/auth/login.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from typing import Callable

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .auth import AuthService, LoginError
from .core.config import Settings, get_settings
from .core.entities import EntityList
from .core.exporter import build_workbook
from .core.extractor import FormExtractor, GeminiExtractor
from .core.schemas import STATUS_APPROVED
from .jobs import JobManager
from .records import EditError, apply_edit
from .repository import Repository

logging.basicConfig(level=logging.INFO)  # לא רושמים פרטים אישיים ביומן
log = logging.getLogger(__name__)

MAX_UPLOAD_MB = 200


class LoginBody(BaseModel):
    username: str
    password: str


class EditBody(BaseModel):
    fields: dict


class IdsBody(BaseModel):
    ids: list[int]


class Services:
    """כל מה שהשרת צריך, במקום אחד (נוח גם לבדיקות)."""

    def __init__(self, settings: Settings, repo: Repository,
                 extractor_factory: Callable[[], FormExtractor] | None = None):
        self.settings = settings
        self.repo = repo
        self.entities = EntityList.from_file(settings.entities_path)
        self.auth = AuthService(repo, settings)
        factory = extractor_factory or self._gemini_factory()
        self.jobs = JobManager(repo, settings, self.entities, factory)

    def _gemini_factory(self) -> Callable[[], FormExtractor]:
        @lru_cache(maxsize=1)
        def factory() -> FormExtractor:
            return GeminiExtractor(self.settings, self.entities.names)
        return factory


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services
        if svc is None:
            settings = get_settings()
            svc = Services(settings, Repository(settings.database_url))
        svc.repo.init_schema()
        svc.auth.bootstrap_user()
        app.state.svc = svc
        yield
        if services is None:
            svc.repo.close()

    app = FastAPI(title="טפסי התחייבות - API", lifespan=lifespan)

    # ------------------------------------------------------------ תלויות
    def svc(request: Request) -> Services:
        return request.app.state.svc

    def current_user(request: Request, authorization: str = Header(default="")) -> dict:
        token = authorization.removeprefix("Bearer ").strip()
        user = request.app.state.svc.auth.validate(token)
        if not user:
            raise HTTPException(401, "נדרשת התחברות מחדש")
        return user

    def token_of(authorization: str = Header(default="")) -> str:
        return authorization.removeprefix("Bearer ").strip()

    # ------------------------------------------------------------ כללי
    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ------------------------------------------------------------ כניסה
    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, s: Services = Depends(svc)):
        client = request.headers.get("x-forwarded-for", "") or (
            request.client.host if request.client else "")
        try:
            token = s.auth.login(body.username, body.password, client.split(",")[0].strip())
        except LoginError as exc:
            raise HTTPException(exc.status, str(exc))
        return {"token": token, "username": body.username.strip(),
                "session_minutes": s.settings.session_minutes}

    @app.post("/api/auth/logout")
    def logout(token: str = Depends(token_of), s: Services = Depends(svc)):
        s.auth.logout(token)
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: dict = Depends(current_user)):
        return {"username": user["username"]}

    # ------------------------------------------------------------ נתונים
    @app.get("/api/config")
    def config(user: dict = Depends(current_user), s: Services = Depends(svc)):
        return {"entities": s.entities.names,
                "api_ready": bool(s.settings.gemini_api_key),
                "model": s.settings.gemini_model}

    @app.get("/api/stats")
    def stats(user: dict = Depends(current_user), s: Services = Depends(svc)):
        return s.repo.counts()

    @app.get("/api/records")
    def records(view: str = "all", user: dict = Depends(current_user),
                s: Services = Depends(svc)):
        return s.repo.fetch_all(view)

    @app.patch("/api/records/{record_id}")
    def edit(record_id: int, body: EditBody, user: dict = Depends(current_user),
             s: Services = Depends(svc)):
        try:
            return apply_edit(s.repo, record_id, body.fields, s.entities, s.settings)
        except KeyError:
            raise HTTPException(404, "הרשומה לא נמצאה")
        except EditError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/records/{record_id}/approve")
    def approve(record_id: int, user: dict = Depends(current_user), s: Services = Depends(svc)):
        s.repo.approve(record_id)
        return {"ok": True}

    @app.post("/api/records/{record_id}/unapprove")
    def unapprove(record_id: int, user: dict = Depends(current_user), s: Services = Depends(svc)):
        s.repo.unapprove(record_id)
        return {"ok": True}

    @app.post("/api/records/approve-clean")
    def approve_clean(user: dict = Depends(current_user), s: Services = Depends(svc)):
        return {"approved": s.repo.approve_all_clean()}

    @app.post("/api/records/delete")
    def delete(body: IdsBody, user: dict = Depends(current_user), s: Services = Depends(svc)):
        return {"deleted": s.repo.delete(body.ids)}

    @app.post("/api/records/delete-all")
    def delete_all(user: dict = Depends(current_user), s: Services = Depends(svc)):
        return {"deleted": s.repo.delete_all()}

    # ------------------------------------------------------------ עיבוד
    @app.post("/api/jobs")
    async def start_job(files: list[UploadFile] = File(...),
                        user: dict = Depends(current_user), s: Services = Depends(svc)):
        if not s.settings.gemini_api_key:
            raise HTTPException(503, "לא הוגדר מפתח GEMINI_API_KEY בשרת")
        payload, total = [], 0
        for f in files:
            data = await f.read()
            total += len(data)
            if total > MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(413, f"הקבצים גדולים מדי (מקסימום {MAX_UPLOAD_MB}MB בהעלאה)")
            payload.append((f.filename or "קובץ", data))
        job = s.jobs.start(payload)
        return job.public()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, user: dict = Depends(current_user), s: Services = Depends(svc)):
        job = s.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "המשימה לא נמצאה")
        return job.public()

    # ------------------------------------------------------------ ייצוא
    @app.get("/api/export")
    def export(scope: str = "approved", include_source: bool = False,
               user: dict = Depends(current_user), s: Services = Depends(svc)):
        rows = s.repo.fetch_all("approved" if scope == "approved" else "all")
        data = build_workbook(rows, include_source=include_source,
                              mark_review=scope != "approved")
        name = f"commitments_{datetime.now():%Y%m%d_%H%M}.xlsx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    return app


app = create_app()

__all__ = ["app", "create_app", "Services", "STATUS_APPROVED"]
