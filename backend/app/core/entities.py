"""רשימה סגורה של גורמים שולחים והתאמת טקסט חופשי אליה."""
from __future__ import annotations

import difflib
import re
from pathlib import Path

# מילים כלליות שמורידים לפני השוואה ("בית חולים", "מרכז רפואי" וכו')
_GENERIC_PHRASES = [
    "המרכז הרפואי", "מרכז רפואי", "בית החולים", "בית חולים", "ביה ח",
    "קופת חולים", "שירותי בריאות", "המרכז", "ע ש", "עש",
]


def _clean(text: str) -> str:
    t = text.replace("״", '"').replace("׳", "'")
    t = re.sub(r"[\"'`]", "", t)  # רמב"ם -> רמבם
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split()).lower()


_GENERIC_CLEAN = sorted({_clean(p) for p in _GENERIC_PHRASES}, key=len, reverse=True)


def _norm(text: str) -> str:
    t = f" {_clean(text)} "
    for phrase in _GENERIC_CLEAN:
        t = t.replace(f" {phrase} ", " ")
    return " ".join(t.split())


class EntityList:
    """רשימת גורמים שולחים: שם קנוני + כינויים."""

    def __init__(self, entries: dict[str, list[str]]):
        self._entries = entries
        self._index: list[tuple[str, str]] = []  # (כינוי מנורמל, שם קנוני)
        for canonical, aliases in entries.items():
            for alias in [canonical, *aliases]:
                n = _norm(alias)
                if n:
                    self._index.append((n, canonical))

    @classmethod
    def from_file(cls, path: str | Path) -> "EntityList":
        entries: dict[str, list[str]] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if parts:
                entries[parts[0]] = parts[1:]
        return cls(entries)

    @property
    def names(self) -> list[str]:
        return list(self._entries.keys())

    def match(self, text: str) -> str | None:
        """מחזיר את השם הקנוני התואם, או None אם אין התאמה מספיק טובה."""
        n = _norm(text or "")
        if not n:
            return None
        # 1. התאמה מדויקת
        for alias, canonical in self._index:
            if alias == n:
                return canonical
        # 2. הכלה (הכינוי הארוך ביותר מנצח)
        hits = [
            (alias, canonical)
            for alias, canonical in self._index
            if len(alias) >= 4 and (alias in n or (len(n) >= 4 and n in alias))
        ]
        if hits:
            return max(hits, key=lambda h: len(h[0]))[1]
        # 3. התאמה מקורבת
        aliases = [a for a, _ in self._index]
        close = difflib.get_close_matches(n, aliases, n=1, cutoff=0.82)
        if close:
            return next(c for a, c in self._index if a == close[0])
        return None
