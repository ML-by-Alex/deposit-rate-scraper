from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Deposit:
    bank: str
    site: str
    name: str
    rate: float
    currency: str
    url: str


@dataclass(frozen=True)
class SiteStatus:
    input_url: str
    domain: str
    http_status: int | None
    signals: str
    result: str
    note: str
    rows_found: int
