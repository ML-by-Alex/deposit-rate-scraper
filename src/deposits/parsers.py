from __future__ import annotations

import json
import re
from collections import deque
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .http import fetch
from .models import Deposit
from .utils import domain_of, parse_percent

MAX_USD_RATE = 0.35

USD_POS = ("usd", "us dollar", "dollar", "aqsh dollar", "aqsh doll", "$", "доллар", "долл")
USD_NEG = ("uzs", "so'm", "som", "сум", "sum", "сўм")

DEPOSIT_LINK_HINTS = (
    "deposit", "deposits", "omonat", "omonatlar", "vklad", "vklady", "депозит", "вклад",
    "savings", "saving", "term-deposit", "time-deposit",
)

RATE_HINTS = ("rate", "annual", "stavka", "foiz", "процент", "yillik", "годовых", "%")

PCT_RE = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*%")
NUM_RE = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)")

JSON_URL_RE = re.compile(
    r"""(?P<u>https?://[^\s"'<>]+(?:\.json|/api/[^\s"'<>]+|get_list_pages/[^\s"'<>]+))""",
    re.IGNORECASE,
)

NOISE_RE = re.compile(
    r"(\{|\}|@font-face|/\*|\*/|px|rem|vh|vw|var\(|normalize\.css)",
    re.IGNORECASE,
)

XB_API_URL = (
    "https://data.egov.uz/apiData/MainData/GetByFile"
    "?fileType=1&id=61121d80db32b99538e0833c&lang=1&tableType=2"
)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _is_noise(s: str) -> bool:
    t = _norm(s)
    if len(t) < 2:
        return True
    return bool(NOISE_RE.search(t))


def _is_usd_context(text: str) -> bool:
    t = (text or "").lower()
    if any(x in t for x in USD_NEG) and not any(x in t for x in USD_POS):
        return False
    return any(x in t for x in USD_POS)


def _page_forced_usd(url: str, soup_text: str) -> bool:
    u = url.lower()
    if "currency=usd" in u or "valyuta=usd" in u or "usd" in u:
        return True
    q = parse_qs(urlparse(url).query)
    for k, v in q.items():
        if k.lower() in ("currency", "valyuta") and any(str(x).lower() == "usd" for x in v):
            return True
    t = (soup_text or "").lower()
    if " usd " in f" {t} " and not any(x in t for x in USD_NEG):
        return True
    return False


def _clean_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup


def _best_bank_name(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        v = _norm(h1.get_text(" ", strip=True))
        if v:
            return v[:80]
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = _norm(title)
    return title[:80] if title else "Unknown Bank"


def _same_domain(a: str, b: str) -> bool:
    return domain_of(a) == domain_of(b)


def _collect_links(base_url: str, soup: BeautifulSoup, limit: int = 200) -> list[str]:
    out: list[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        u = urljoin(base_url, href)
        if not u.startswith(("http://", "https://")):
            continue
        if not _same_domain(base_url, u):
            continue
        if u in seen:
            continue
        seen.add(u)

        txt = _norm(a.get_text(" ", strip=True)).lower()
        lu = u.lower()

        if any(h in lu for h in DEPOSIT_LINK_HINTS) or any(h in txt for h in DEPOSIT_LINK_HINTS):
            out.append(u)
        elif "usd" in lu or "currency=usd" in lu or "valyuta=usd" in lu or "$" in lu:
            out.append(u)

        if len(out) >= limit:
            break
    return out


def _pick_name_from_block(block) -> str:
    for tag in ("h1", "h2", "h3", "h4", "strong", "b", "a"):
        n = block.find(tag)
        if n:
            s = _norm(n.get_text(" ", strip=True))
            if s and not _is_noise(s):
                return s
    s = _norm(block.get_text(" ", strip=True))
    return s[:120] if s else ""


def _extract_rate_from_text(text: str) -> Optional[float]:
    t = _norm(text)
    if not t:
        return None
    m = PCT_RE.search(t)
    if m:
        rate = parse_percent(m.group(0))
        if 0.0 < rate <= MAX_USD_RATE:
            return rate

    nums = [x.group("num") for x in NUM_RE.finditer(t)]
    if not nums:
        return None

    for raw in nums[:4]:
        rate = parse_percent(raw)
        if 0.0 < rate <= MAX_USD_RATE:
            return rate

    return None


def _extract_from_tables(soup: BeautifulSoup, url: str, bank: str, forced_usd: bool) -> list[Deposit]:
    out: list[Deposit] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [_norm(x.get_text(" ", strip=True)).lower() for x in rows[0].find_all(["th", "td"])]
        cur_idx = None
        rate_idx = None
        name_idx = None

        for i, h in enumerate(headers):
            if cur_idx is None and ("currency" in h or "валюта" in h or "valyuta" in h or "usd" in h):
                cur_idx = i
            if rate_idx is None and any(k in h for k in RATE_HINTS):
                rate_idx = i
            if name_idx is None and ("deposit" in h or "вклад" in h or "депозит" in h or "omonat" in h):
                name_idx = i

        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue

            row_text = _norm(tr.get_text(" ", strip=True))
            if _is_noise(row_text):
                continue

            cur_text = ""
            if cur_idx is not None and cur_idx < len(cells):
                cur_text = _norm(cells[cur_idx].get_text(" ", strip=True))

            row_usd = forced_usd or _is_usd_context(row_text) or _is_usd_context(cur_text)
            if not row_usd:
                continue

            if any(x in row_text.lower() for x in USD_NEG) and not any(x in row_text.lower() for x in USD_POS):
                continue

            rate_text = ""
            if rate_idx is not None and rate_idx < len(cells):
                rate_text = _norm(cells[rate_idx].get_text(" ", strip=True))
            else:
                rate_text = row_text

            rate = _extract_rate_from_text(rate_text)
            if rate is None:
                continue

            name = ""
            if name_idx is not None and name_idx < len(cells):
                name = _norm(cells[name_idx].get_text(" ", strip=True))
            if not name:
                name = _pick_name_from_block(tr)

            if not name or _is_noise(name):
                continue

            out.append(Deposit(bank=bank, site=domain_of(url), name=name, rate=rate, currency="USD", url=url))

    return out


def _extract_from_blocks(soup: BeautifulSoup, url: str, bank: str, forced_usd: bool) -> list[Deposit]:
    out: list[Deposit] = []

    candidates = soup.find_all(["tr", "article", "li", "section", "div"])
    for block in candidates:
        bt = _norm(block.get_text(" ", strip=True))
        if not bt or _is_noise(bt):
            continue

        if not any(ch.isdigit() for ch in bt):
            continue

        block_has_rate_hint = any(k in bt.lower() for k in RATE_HINTS)
        block_has_percent = "%" in bt
        if not (block_has_percent or block_has_rate_hint):
            continue

        if not (forced_usd or _is_usd_context(bt)):
            continue

        if any(x in bt.lower() for x in USD_NEG) and not any(x in bt.lower() for x in USD_POS):
            continue

        rate = _extract_rate_from_text(bt)
        if rate is None:
            continue

        name = _pick_name_from_block(block)
        if not name:
            continue
        if _is_noise(name):
            continue

        low = name.lower()
        if any(x in low for x in ("cookie", "privacy", "policy", "search", "subscribe")):
            continue

        out.append(Deposit(bank=bank, site=domain_of(url), name=name, rate=rate, currency="USD", url=url))

    return out


def _discover_json_urls(base_url: str, html: str) -> list[str]:
    found = set()

    for m in JSON_URL_RE.finditer(html or ""):
        found.add(m.group("u"))

    soup = BeautifulSoup(html or "", "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        h = href.lower()
        if "/api/" in h or "get_list_pages" in h or h.endswith(".json"):
            found.add(urljoin(base_url, href))

    out = [u for u in found if u.startswith(("http://", "https://")) and _same_domain(base_url, u)]
    return out[:40]


def _walk_json(obj: Any, url: str, bank: str, out: list[Deposit]) -> None:
    if isinstance(obj, dict):
        t = " ".join(_norm(str(v)) for v in obj.values() if isinstance(v, (str, int, float)))
        lt = t.lower()

        currency = ""
        for k in ("currency", "valyuta", "валюта"):
            if k in obj and isinstance(obj[k], str):
                currency = obj[k].strip().upper()
                break

        if (currency == "USD") or (_is_usd_context(t) and "usd" in lt):
            rate_raw = ""
            for k in ("percent", "rate", "stavka", "foiz"):
                if k in obj and isinstance(obj[k], (str, int, float)):
                    rate_raw = str(obj[k])
                    break

            rate = _extract_rate_from_text(rate_raw or t)
            if rate is not None:
                name = ""
                for k in ("name", "title", "deposit", "product", "caption"):
                    if k in obj and isinstance(obj[k], str):
                        name = _norm(obj[k])
                        break
                if not name:
                    name = bank

                out.append(Deposit(bank=bank, site=domain_of(url), name=name[:120], rate=rate, currency="USD", url=url))

        for v in obj.values():
            _walk_json(v, url, bank, out)

    elif isinstance(obj, list):
        for it in obj:
            _walk_json(it, url, bank, out)


def _extract_from_json_endpoints(session: requests.Session, page_url: str, bank: str, html: str) -> list[Deposit]:
    out: list[Deposit] = []
    for jurl in _discover_json_urls(page_url, html):
        try:
            r = fetch(session, jurl)
            ct = (r.headers.get("Content-Type") or "").lower()
            txt = r.text or ""
            if "json" not in ct and not txt.strip().startswith(("{", "[")):
                continue
            data = r.json()
            _walk_json(data, jurl, bank, out)
        except Exception:
            continue
    return out


def _dedup(items: Iterable[Deposit]) -> list[Deposit]:
    uniq: dict[tuple[str, str, float], Deposit] = {}
    for d in items:
        key = (d.site, d.name.strip().lower(), d.rate)
        uniq.setdefault(key, d)
    return list(uniq.values())


def _parse_xb_open_data(session: requests.Session, source_url: str) -> list[Deposit]:
    data = fetch(session, XB_API_URL).json()

    out: dict[str, Deposit] = {}
    for item in data:
        name = str(item.get("Omonat nomi", "")).strip()
        rate_raw = str(item.get("Yillik foiz", "")).strip()
        initial = str(item.get("Boshlang'ich badal miqdori", "")).strip()
        other = str(item.get("Boshqa shartlar", "")).strip()
        blob = " ".join([name, rate_raw, initial, other])

        if not name or not _is_usd_context(blob):
            continue

        rate = parse_percent(rate_raw)
        if not (0.0 <= rate <= MAX_USD_RATE):
            continue

        out.setdefault(
            name.lower(),
            Deposit(bank="Xalq banki", site="xb.uz", name=name, rate=rate, currency="USD", url=source_url),
        )

    return list(out.values())


def _parse_universal(session: requests.Session, url: str) -> list[Deposit]:
    visited = set()
    q = deque([(url, 0)])
    all_deps: list[Deposit] = []

    while q and len(visited) < 20:
        u, depth = q.popleft()
        if u in visited:
            continue
        visited.add(u)

        r = fetch(session, u)
        html = r.text or ""
        soup = _clean_soup(html)

        bank = _best_bank_name(soup)
        page_text = _norm(soup.get_text(" ", strip=True))
        forced_usd = _page_forced_usd(u, page_text)

        deps: list[Deposit] = []
        deps.extend(_extract_from_tables(soup, u, bank, forced_usd))
        deps.extend(_extract_from_blocks(soup, u, bank, forced_usd))

        if not deps:
            deps.extend(_extract_from_json_endpoints(session, u, bank, html))

        all_deps.extend(deps)

        if depth < 1:
            for link in _collect_links(u, soup, limit=200):
                if link not in visited:
                    q.append((link, depth + 1))

    return _dedup(all_deps)


def parse_url(session: requests.Session, url: str) -> list[Deposit]:
    d = domain_of(url)
    if d.endswith("xb.uz"):
        return _parse_xb_open_data(session, url)
    return _parse_universal(session, url)
