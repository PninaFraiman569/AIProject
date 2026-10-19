"""כניסה למערכת: שם משתמש וסיסמה.

- הסיסמה נשמרת במסד מוצפנת ב-scrypt (ספריית התקן של Python), לא כטקסט גלוי.
- אחרי כניסה מוצלחת נוצר אסימון (token) אקראי. במסד נשמר רק ה-hash שלו.
- התחברות פגה אחרי SESSION_MINUTES דקות ללא פעילות.
- אחרי MAX_LOGIN_ATTEMPTS ניסיונות שגויים - חסימה זמנית של 5 דקות.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time

from .core.config import Settings
from .repository import Repository

log = logging.getLogger(__name__)

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
LOCKOUT_SECONDS = 5 * 60


# ------------------------------------------------------------------ סיסמאות
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ חסימת ניחושים
class LoginThrottle:
    """סופר ניסיונות כושלים לפי משתמש+כתובת, וחוסם זמנית אחרי מספר ניסיונות."""

    def __init__(self, max_attempts: int):
        self._max = max(1, max_attempts)
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked_for(self, key: str) -> int:
        """כמה שניות נותרו לחסימה (0 = לא חסום)."""
        now = time.time()
        with self._lock:
            fails = [t for t in self._fails.get(key, []) if now - t < LOCKOUT_SECONDS]
            self._fails[key] = fails
            if len(fails) >= self._max:
                return int(LOCKOUT_SECONDS - (now - fails[0])) + 1
        return 0

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


# ------------------------------------------------------------------ שירות הכניסה
class AuthService:
    def __init__(self, repo: Repository, settings: Settings):
        self.repo = repo
        self.settings = settings
        self.throttle = LoginThrottle(settings.max_login_attempts)

    def bootstrap_user(self) -> None:
        """יוצר / מעדכן את המשתמש המשותף מתוך APP_USERNAME / APP_PASSWORD."""
        s = self.settings
        if not s.app_password:
            log.warning("APP_PASSWORD לא הוגדר - לא ניתן יהיה להיכנס למערכת")
            return
        existing = self.repo.get_user(s.app_username)
        if existing and verify_password(s.app_password, existing["password_hash"]):
            return  # הסיסמה לא השתנתה
        self.repo.upsert_user(s.app_username, hash_password(s.app_password))
        if existing:  # הסיסמה הוחלפה - מנתקים את כל ההתחברויות הקיימות
            self.repo.delete_user_sessions(s.app_username)
        log.info("user '%s' %s", s.app_username, "updated" if existing else "created")

    def login(self, username: str, password: str, client_key: str) -> str:
        """מחזיר אסימון התחברות. זורק LoginError עם הודעה בעברית."""
        key = f"{username.strip().lower()}|{client_key}"
        wait = self.throttle.blocked_for(key)
        if wait:
            raise LoginError(f"יותר מדי ניסיונות כניסה. נסי שוב בעוד {wait // 60 + 1} דקות.", 429)
        user = self.repo.get_user(username.strip())
        if not user or not verify_password(password, user["password_hash"]):
            self.throttle.fail(key)
            raise LoginError("שם המשתמש או הסיסמה שגויים", 401)
        self.throttle.reset(key)
        token = secrets.token_urlsafe(32)
        self.repo.create_session(_token_hash(token), user["id"])
        return token

    def validate(self, token: str) -> dict | None:
        if not token:
            return None
        return self.repo.touch_session(_token_hash(token), self.settings.session_minutes)

    def logout(self, token: str) -> None:
        if token:
            self.repo.delete_session(_token_hash(token))


class LoginError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status
