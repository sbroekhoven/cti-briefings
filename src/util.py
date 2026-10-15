from __future__ import annotations

import re
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", message=".*looks like a URL.*")
warnings.filterwarnings("ignore", message=".*looks like a filename.*")

_FORMATS = ("%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%B %d, %Y")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value) -> datetime | None:
    """Best-effort date parsing (ISO 8601, RFC 2822, a few human formats). Always returns UTC or None."""
    if value is None or value == "":
        return None
    dt = None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        if dt is None:
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                pass
        if dt is None:
            for fmt in _FORMATS:
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def clean_text(html: str | None, max_chars: int = 600) -> str:
    """Strip HTML, collapse whitespace, truncate. Used for summaries (never full articles)."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return truncate(re.sub(r"\s+", " ", text).strip(), max_chars)
