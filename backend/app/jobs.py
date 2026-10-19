"""משימות עיבוד ברקע.

עיבוד של 30 טפסים לוקח זמן, ושרתי ענן מנתקים בקשה ארוכה. לכן:
1. הפרונטאנד שולח את הקבצים ומקבל מיד מספר משימה.
2. העיבוד רץ ברקע ושומר כל קובץ למסד ברגע שהוא מסתיים.
3. הפרונטאנד שואל כל שנייה מה ההתקדמות (GET /api/jobs/{id}).

הקבצים נשמרים בזיכרון רק עד סוף העיבוד, ולא נכתבים לדיסק או למסד.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

from .core.config import Settings
from .core.entities import EntityList
from .core.extractor import FormExtractor
from .core.image_utils import UnsupportedFile
from .core.pipeline import process_upload
from .repository import Repository

log = logging.getLogger(__name__)

JOB_TTL_SECONDS = 60 * 60  # משימה שהסתיימה נשמרת שעה ואז נמחקת מהזיכרון


@dataclass
class Job:
    id: str
    files_total: int
    status: str = "running"          # running / done / error
    file_index: int = 0               # הקובץ שבעיבוד כעת (1..files_total)
    file_name: str = ""
    page_done: int = 0
    page_total: int = 0
    progress: float = 0.0             # 0..1
    created: int = 0                  # כמה רשומות נוספו
    failed: list = field(default_factory=list)
    error: str = ""
    finished_at: float = 0.0

    def public(self) -> dict:
        d = asdict(self)
        d.pop("finished_at", None)
        return d


class JobManager:
    def __init__(self, repo: Repository, settings: Settings, entities: EntityList,
                 extractor_factory: Callable[[], FormExtractor]):
        self.repo = repo
        self.settings = settings
        self.entities = entities
        self.extractor_factory = extractor_factory
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, files: list[tuple[str, bytes]]) -> Job:
        self._cleanup()
        job = Job(id=uuid.uuid4().hex, files_total=len(files))
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, files), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    # ------------------------------------------------------------------
    def _run(self, job: Job, files: list[tuple[str, bytes]]) -> None:
        try:
            extractor = self.extractor_factory()
        except Exception as exc:
            job.status, job.error = "error", str(exc)
            job.finished_at = time.time()
            return

        n = len(files)
        for i, (name, data) in enumerate(files):
            job.file_index, job.file_name = i + 1, name
            job.page_done = job.page_total = 0

            def on_progress(done: int, total: int, _i=i) -> None:
                job.page_done, job.page_total = done, total
                job.progress = min((_i + done / max(total, 1)) / n, 1.0)

            try:
                records = process_upload(name, data, extractor, self.entities,
                                         self.settings, on_progress=on_progress)
                ids = self.repo.insert_many([r.to_row() for r in records])
                job.created += len(ids)
            except UnsupportedFile as exc:
                job.failed.append(f"{name}: {exc}")
            except Exception as exc:  # קובץ בעייתי לא מפיל את שאר האצווה
                log.exception("file failed")
                job.failed.append(f"{name}: שגיאה לא צפויה ({type(exc).__name__})")
            files[i] = (name, b"")  # משחררים את הקובץ מהזיכרון
            job.progress = (i + 1) / n

        job.status = "done"
        job.finished_at = time.time()

    def _cleanup(self) -> None:
        now = time.time()
        with self._lock:
            for jid in [j for j, job in self._jobs.items()
                        if job.finished_at and now - job.finished_at > JOB_TTL_SECONDS]:
                self._jobs.pop(jid, None)
