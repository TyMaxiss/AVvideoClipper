"""Human-friendly time formatting and parsing (h:mm:ss.s)."""
from __future__ import annotations

import re


def format_time(seconds: float, decimals: int = 1) -> str:
    """12.34 -> '0:12.3', 3725.5 -> '1:02:05.5'."""
    seconds = max(0.0, float(seconds))
    scale = 10 ** decimals
    total = int(round(seconds * scale))
    whole, frac = divmod(total, scale)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    tail = f".{frac:0{decimals}d}" if decimals else ""
    if h:
        return f"{h}:{m:02d}:{s:02d}{tail}"
    return f"{m}:{s:02d}{tail}"


_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d*)?)\s*$")


def parse_time(text: str) -> float:
    """Inverse of format_time; also accepts plain seconds ('75.5') and commas."""
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(f"неверный формат времени: {text!r}")
    a, b, c = m.groups()
    sec = float(c.replace(",", "."))
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + sec
    if a is not None:
        return int(a) * 60 + sec
    return sec
