from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from .models import Deposit

USD_HINTS = ("usd", "aqsh doll", "aqsh dollar", "dollar", "доллар", "$")
PCT_RE = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*%")


def load_urls(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)
    return urls


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def has_usd(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in USD_HINTS)


def parse_percent(raw: str) -> float:
    s = (raw or "").strip()
    if not s:
        return 0.0

    m = PCT_RE.search(s)
    if m:
        v = m.group("num").replace(",", ".")
        try:
            return float(v) / 100.0
        except ValueError:
            return 0.0

    try:
        v = float(s.replace(",", "."))
    except ValueError:
        return 0.0

    return v / 100.0 if v > 1.0 else v


def to_dataframe(rows: list[Deposit]) -> pd.DataFrame:
    df = pd.DataFrame([vars(r) for r in rows])
    if df.empty:
        return df

    df = df.rename(
        columns={
            "bank": "Bank",
            "site": "Site",
            "name": "Deposit",
            "rate": "AnnualRate",
            "currency": "Currency",
            "url": "SourceURL",
        }
    )
    return df.sort_values(["Bank", "AnnualRate", "Deposit"], ascending=[True, False, True], kind="stable").reset_index(
        drop=True
    )
