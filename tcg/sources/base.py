from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # 古い環境（requests同梱のurllib3）
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

from ..models import Observation, Product


# 実在ブラウザ相当のヘッダ（scraper.py と同等）
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# 「¥3,980」「3980円」「3,980」などを拾う。3桁以上。
PRICE_RE = re.compile(r"(?:¥|￥)?\s*([1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,7})\s*円?")


class Source(Protocol):
    name: str

    def fetch(self, product: Product) -> list[Observation]:
        ...


def build_session(scraping: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    total = int(scraping.get("retry_total", 3))
    retry = Retry(
        total=total,
        connect=total,
        read=total,
        backoff_factor=float(scraping.get("retry_backoff", 3)),
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(BROWSER_HEADERS)
    return session


def timeouts(scraping: dict[str, Any]) -> tuple[float, float]:
    return (float(scraping.get("connect_timeout", 30)), float(scraping.get("timeout_seconds", 20)))


def polite_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def parse_price(text: str) -> int | None:
    match = PRICE_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def make_observed_on(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
