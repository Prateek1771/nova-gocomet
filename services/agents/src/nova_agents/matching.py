"""Value → bbox matching (ADR-016). The text model returns values only; evidence comes from finding each
value back among pdfplumber's word boxes. Models reformat (dates to ISO, "8,400.0 KGS" to 8400), so
dates and numbers are compared as values, everything else by fuzzy ratio over a sliding word window.

bbox = [x0, top, x1, bottom] in PDF points, top-left origin (ADR-022)."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from rapidfuzz import fuzz

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%b %d %Y",
    "%d %B %Y",
    "%B %d %Y",
    "%d.%m.%Y",
)
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    page: int  # 1-based


@dataclass
class Match:
    page: int
    bbox: list[float]
    text: str
    score: float  # 0..1


def norm(s: str) -> str:
    return _NON_ALNUM.sub(" ", s.upper()).strip()


def as_date(s: str) -> date | None:
    s = s.strip().strip(",.").replace(",", "")
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def as_number(s: str) -> float | None:
    t = re.sub(r"(?i)[a-z]+$", "", s.strip()).replace(",", "")  # "8,400.0KGS" -> 8400.0
    try:
        return float(t)
    except ValueError:
        return None


def _union(ws: list[Word]) -> list[float]:
    return [
        round(min(w.x0 for w in ws), 2),
        round(min(w.top for w in ws), 2),
        round(max(w.x1 for w in ws), 2),
        round(max(w.bottom for w in ws), 2),
    ]


def _windows(words: list[Word], n: int) -> Any:
    for i in range(len(words)):
        win = words[i : i + n]
        if len(win) == n and len({w.page for w in win}) == 1:
            yield win


def locate(value: Any, words: list[Word]) -> Match | None:
    """Best match for one scalar value, or None when nothing plausible is on the page."""
    if value is None or value == "" or not words:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        num = float(value)
        for w in words:
            n = as_number(w.text)
            if n is not None and abs(n - num) < 0.01:
                return Match(w.page, _union([w]), w.text, 1.0)
        return None
    s = str(value)
    if _ISO.match(s):
        d = date.fromisoformat(s)
        for n in (1, 2, 3):
            for win in _windows(words, n):
                if as_date(" ".join(w.text for w in win)) == d:
                    return Match(win[0].page, _union(win), " ".join(w.text for w in win), 1.0)
    target = norm(s)
    if not target:
        return None
    k = len(target.split())
    best: tuple[float, list[Word]] | None = None
    for n in {max(1, k - 1), k, k + 1}:
        for win in _windows(words, n):
            score = fuzz.ratio(norm(" ".join(w.text for w in win)), target)
            if best is None or score > best[0]:
                best = (score, win)
                if score == 100:
                    break
    if best is None or best[0] < 60:
        return None
    return Match(best[1][0].page, _union(best[1]), " ".join(w.text for w in best[1]), round(best[0] / 100, 3))
