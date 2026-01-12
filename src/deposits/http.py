from __future__ import annotations

import re
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

ANTI_COOKIES_RE = re.compile(r"(cf_clearance|__cf_bm|_abck|bm_sz|ak_bmsc)", re.I)
ANTI_BODY_RE = re.compile(
    r"(captcha|hcaptcha|recaptcha|checking your browser|just a moment|access denied|request blocked|"
    r"cf-challenge|challenge-platform|enable javascript|javascript required)",
    re.I,
)

JS_SHELL_RE = re.compile(r"(<div[^>]+id=['\"]app['\"][^>]*>|<div[^>]+id=['\"]root['\"][^>]*>)", re.I)


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
    )
    return s


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def fetch(session: requests.Session, url: str) -> requests.Response:
    r = session.get(url, timeout=25, allow_redirects=True)
    r.raise_for_status()
    return r


def looks_js_empty(html: str) -> bool:
    t = (html or "").strip()
    if len(t) < 1500:
        return True
    if JS_SHELL_RE.search(t) and ("%" not in t) and ("usd" not in t.lower()):
        return True
    return False


def diagnose_response(url: str, r: requests.Response) -> tuple[int, str]:
    hdr = {k.lower(): v for k, v in r.headers.items()}
    body = (r.text or "")[:20000]
    cookie_blob = r.headers.get("Set-Cookie", "")

    signals: list[str] = []

    if r.status_code in (401, 403, 429, 503):
        signals.append(f"status={r.status_code}")

    if "cf-ray" in hdr or "cloudflare" in hdr.get("server", "").lower():
        signals.append("cloudflare")

    if "x-sucuri-id" in hdr or "x-sucuri-cache" in hdr:
        signals.append("sucuri")

    if ANTI_COOKIES_RE.search(cookie_blob or ""):
        signals.append("antibot_cookie")

    if ANTI_BODY_RE.search(body or ""):
        signals.append("antibot_page")

    if r.status_code == 200 and looks_js_empty(r.text or ""):
        signals.append("thin_html_or_js_shell")

    return r.status_code, ",".join(signals)
