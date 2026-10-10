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
    conf: float = 1.0  # OCR confidence; text-layer words are exact


@dataclass
class Match:
    page: int
    bbox: list[float]
    text: str
    score: float  # 0..1
    conf: float = 1.0  # lowest OCR confidence among the matched words


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


def _conf(ws: list[Word]) -> float:
    return min(w.conf for w in ws)


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
                return Match(w.page, _union([w]), w.text, 1.0, w.conf)
        return None
    s = str(value)
    if _ISO.match(s):
        d = date.fromisoformat(s)
        for n in (1, 2, 3):
            for win in _windows(words, n):
                if as_date(" ".join(w.text for w in win)) == d:
                    return Match(win[0].page, _union(win), " ".join(w.text for w in win), 1.0, _conf(win))
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
    win = best[1]
    return Match(win[0].page, _union(win), " ".join(w.text for w in win), round(best[0] / 100, 3), _conf(win))


_ANCHOR = re.compile(r"<a id=['\"][^'\"]*['\"]></a>")
_ROW_END = re.compile(r"</tr>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def chunk_text(markdown: str) -> str:
    """A DPT-2 chunk's markdown as plain text: no anchors, table rows as lines, cells space-separated."""
    return _TAG.sub(" ", _ROW_END.sub("\n", _ANCHOR.sub("", markdown)))


def chunk_words(
    chunks: list[dict[str, Any]], grounding: dict[str, Any], sizes: dict[int, tuple[float, float]]
) -> list[Word]:
    """DPT-2 chunks -> word boxes in PDF points for the pages in `sizes` (1-based -> (width, height)).
    A word inside one of the chunk's `low_confidence_spans` carries that span's confidence.
    ponytail: a chunk only has one box, so it is split evenly by line and by character offset; boxes
    are approximate within a chunk. Use word-level grounding if ADE starts returning it."""
    out: list[Word] = []
    for ch in chunks:
        g = ch.get("grounding") or {}
        page = int(g.get("page") or 0) + 1
        if page not in sizes or not g.get("box"):
            continue
        b, (pw, ph) = g["box"], sizes[page]
        x0, y0, x1, y1 = b["left"] * pw, b["top"] * ph, b["right"] * pw, b["bottom"] * ph
        spans = (grounding.get(str(ch.get("id"))) or {}).get("low_confidence_spans") or []
        lines = [ln for ln in chunk_text(ch.get("markdown") or "").splitlines() if ln.strip()]
        lh = (y1 - y0) / max(len(lines), 1)
        for i, ln in enumerate(lines):
            top, per_char = y0 + i * lh, (x1 - x0) / max(len(ln), 1)
            for m in re.finditer(r"\S+", ln):
                low = [float(sp["confidence"]) for sp in spans if m.group() in str(sp.get("text"))]
                left, right = x0 + per_char * m.start(), x0 + per_char * m.end()
                out.append(Word(m.group(), left, top, right, top + lh, page, min(low, default=1.0)))
    return out
