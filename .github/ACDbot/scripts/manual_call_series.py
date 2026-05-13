from __future__ import annotations

import re
from pathlib import Path


BREAKOUT_DISPLAY_NAMES = {
    "epbs": "ePBS breakout",
    "bal": "BAL breakout",
    "focil": "FOCIL breakout",
}

DATE_SUFFIX_RE = re.compile(r",\s*[A-Z][a-z]+\s+\d{1,2},\s+\d{4}$")


def breakout_display_name(series: str) -> str:
    return BREAKOUT_DISPLAY_NAMES[series]


def infer_breakout_series(source_dir: Path) -> str | None:
    source_name = source_dir.name.lower()
    patterns = {
        "epbs": ("epbs", "eip-7732"),
        "bal": ("bal", "block-level access list", "block level access list", "eip-7928"),
        "focil": ("focil",),
    }

    for series, aliases in patterns.items():
        if any(alias in source_name for alias in aliases):
            return series
    return None


def strip_date_suffix(title: str) -> str:
    return DATE_SUFFIX_RE.sub("", title).strip()
