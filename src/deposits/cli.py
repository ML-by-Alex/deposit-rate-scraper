from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
from tenacity import RetryError

from .export import write_csv, write_excel_report
from .http import build_session, diagnose_response, looks_js_empty
from .models import Deposit, SiteStatus
from .parsers import parse_url
from .utils import domain_of, load_urls, to_dataframe

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO, datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

USD_TOKENS = ("usd", "$", "dollar", "aqsh")
PCT_TOKEN = "%"


def _simple_has_usd(html: str) -> bool:
    h = (html or "").lower()
    return any(t in h for t in USD_TOKENS)


def _simple_has_percent(html: str) -> bool:
    return PCT_TOKEN in (html or "")


def _unwrap_retry_error(e: RetryError) -> Exception:
    last = e.last_attempt.exception()
    return last if isinstance(last, Exception) else e


def _is_hard_block(status: int | None, signals: str) -> bool:
    if status in (401, 403, 429, 503):
        return True

    s = (signals or "").lower()
    has_waf = ("cloudflare" in s) or ("sucuri" in s)
    has_antibot = ("antibot_cookie" in s) or ("antibot_page" in s)

    return has_waf and has_antibot


def main() -> int:
    parser = argparse.ArgumentParser(prog="deposits")
    parser.add_argument("file", nargs="?", default="banks_urls.txt")
    parser.add_argument("--xlsx", default="result.xlsx")
    parser.add_argument("--csv", default="result.csv")
    parser.add_argument("--sites-csv", default="sites_status.csv")
    args = parser.parse_args()

    urls = load_urls(Path(args.file))
    session = build_session()

    deposits_all: list[Deposit] = []
    sites: list[SiteStatus] = []

    for url in urls:
        dom = domain_of(url)
        http_status: int | None = None
        signals = ""
        note = ""
        result = ""
        rows_found = 0
        html_probe = ""

        try:
            r = session.get(url, timeout=25, allow_redirects=True)
            http_status = r.status_code
            html_probe = r.text or ""
            _, signals = diagnose_response(url, r)

            if _is_hard_block(http_status, signals):
                result = "BLOCKED"
                note = signals or f"status={http_status}"
                sites.append(SiteStatus(url, dom, http_status, signals, result, note, 0))
                logger.error("BLOCKED %s: %s", dom, note)
                continue

            deps = parse_url(session, url)
            deposits_all.extend(deps)
            rows_found = len(deps)

            if rows_found > 0:
                result = "OK"
                note = ""
                logger.info("OK %s rows from %s", rows_found, dom)
            else:
                if looks_js_empty(html_probe):
                    result = "JS_RENDER_REQUIRED"
                    note = "HTML looks like a JS shell or too thin"
                elif not _simple_has_usd(html_probe):
                    result = "NO_USD_MATCH"
                    note = "No USD markers found"
                elif not _simple_has_percent(html_probe):
                    result = "NO_RATES_FOUND"
                    note = "No percent values found"
                else:
                    result = "NO_MATCHING_DEPOSITS"
                    note = "USD markers exist but no valid deposit/rate pairs detected"

                logger.warning("0 rows from %s: %s", dom, note)

            sites.append(SiteStatus(url, dom, http_status, signals, result, note, rows_found))

        except RetryError as e:
            ex = _unwrap_retry_error(e)
            result = "ERROR"
            note = f"{type(ex).__name__}: {str(ex)[:180]}"
            sites.append(SiteStatus(url, dom, http_status, signals, result, note, 0))
            logger.error("ERROR %s: %s", dom, note)

        except Exception as e:
            result = "ERROR"
            note = f"{type(e).__name__}: {str(e)[:180]}"
            sites.append(SiteStatus(url, dom, http_status, signals, result, note, 0))
            logger.error("ERROR %s: %s", dom, note)

    deposits_df = to_dataframe(deposits_all)
    sites_df = pd.DataFrame([vars(s) for s in sites]).rename(
        columns={
            "input_url": "InputURL",
            "domain": "Domain",
            "http_status": "HTTPStatus",
            "signals": "Signals",
            "result": "Result",
            "note": "Note",
            "rows_found": "RowsFound",
        }
    )

    xlsx_path = write_excel_report(deposits_df, sites_df, args.xlsx)

    if not deposits_df.empty:
        df_csv = deposits_df.copy()
        df_csv["AnnualRate"] = (df_csv["AnnualRate"] * 100).map(lambda x: f"{x:g}%")
        csv_path = write_csv(df_csv, args.csv)
    else:
        csv_path = write_csv(pd.DataFrame(), args.csv)

    sites_csv_path = write_csv(sites_df, args.sites_csv)

    ok_sites = sum(1 for s in sites if s.rows_found > 0)
    print(f"Excel report: {xlsx_path}")
    print(f"CSV deposits: {csv_path}")
    print(f"CSV sites: {sites_csv_path}")
    print(f"Total: {len(deposits_all)} deposits from {ok_sites}/{len(sites)} sites")
    return 0
