from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

# urllib3 は standalone / requests同梱 どちらの環境でも動くようフォールバック取得。
try:  # 通常（新しめのrequests＝standalone urllib3）
    import urllib3.util.connection as _urllib3_conn
    from urllib3.util.retry import Retry
except Exception:  # 古い環境（requests同梱のurllib3）
    from requests.packages.urllib3.util import connection as _urllib3_conn  # type: ignore
    from requests.packages.urllib3.util.retry import Retry  # type: ignore


# 実在ブラウザ相当のヘッダ（レート制限・簡易ボット判定の回避用）。
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _build_session(scraping: dict[str, Any]) -> requests.Session:
    """リトライ＋バックオフ付きの requests.Session を生成。"""
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
    return session


PRICE_RE = re.compile(r"(?:¥|￥)?\s*([1-9]\d{2,3}(?:,\d{3})+|[1-9]\d{5,6})\s*円?")
DATE_RE = re.compile(r"(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?(?:\s*\d{1,2}:\d{2})?)")
GOOD_PRICE_LABELS = ["新品買取価格", "未開封買取価格", "新品（未開封）", "新品(未開封)", "買取上限額"]
BAD_PRICE_LABELS = ["中古", "開封済", "画面割れ", "ジャンク", "販売価格", "通常価格", "税込"]

# 容量トークン（1000GB/1024GB は 1TB に正規化）。将来 1TB/2TB が来ても機能する。
CAPACITY_RE = re.compile(r"(2\s?TB|1\s?TB|1024GB|1000GB|512GB|256GB|128GB)", re.IGNORECASE)
# キャリア版（除外対象）。Apple Store / SIMフリー版を優先する。
CARRIER_RE = re.compile(r"(softbank|ソフトバンク|docomo|ドコモ|楽天モバイル|rakuten|\bau\b)", re.IGNORECASE)
# Apple Store / SIMフリー / 国内版（加点対象）。
APPLE_RE = re.compile(r"(apple\s*store|sim\s*フリー|国内版)", re.IGNORECASE)


def normalize_capacity(value: str) -> str:
    token = str(value).upper().replace(" ", "")
    if token in ("1024GB", "1000GB"):
        return "1TB"
    return token


def capacity_positions(text: str) -> list[tuple[int, str]]:
    return [(m.start(), normalize_capacity(m.group(1))) for m in CAPACITY_RE.finditer(text)]


def nearest_capacity_before(caps: list[tuple[int, str]], pos: int) -> str | None:
    current: str | None = None
    for start, cap in caps:
        if start <= pos:
            current = cap
        else:
            break
    return current


def nearest_color_before(text: str, pos: int, colors: list[dict[str, Any]], max_distance: int = 200) -> str | None:
    lower = text.lower()
    best_key: str | None = None
    best_distance: int | None = None
    for color in colors:
        keywords = color.get("keywords") or [color.get("label", ""), color.get("key", "")]
        for keyword in keywords:
            if not keyword:
                continue
            needle = str(keyword).lower()
            start = 0
            while True:
                idx = lower.find(needle, start)
                if idx < 0 or idx > pos:
                    break
                distance = pos - idx
                if distance <= max_distance and (best_distance is None or distance < best_distance):
                    best_distance = distance
                    best_key = color.get("key")
                start = idx + len(needle)
    return best_key


def variant_score(context: str) -> int | None:
    """Apple Store/SIMフリー版なら加点。キャリア版のみなら None（除外）。"""
    has_carrier = bool(CARRIER_RE.search(context))
    has_apple = bool(APPLE_RE.search(context))
    if has_carrier and not has_apple:
        return None
    score = 0
    if has_apple:
        score += 150
    if has_carrier:
        score -= 150
    return score


@dataclass(frozen=True)
class ScrapedOffer:
    shop_name: str
    color_key: str
    color_label: str
    capacity: str
    state: str
    price: int
    source_updated_at: str | None
    url: str
    raw_text: str


def scrape_site(site: dict[str, Any], item: dict[str, Any], scraping: dict[str, Any]) -> list[ScrapedOffer]:
    html = fetch(site["url"], scraping, site)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    source_updated_at = find_updated_at(soup)
    blocks = extract_text_blocks(soup)
    candidates = candidate_blocks(blocks, item, scraping)
    offers = offers_from_candidates(site, item, candidates, source_updated_at)
    return dedupe_offers(offers)


def fetch(url: str, scraping: dict[str, Any], site: dict[str, Any] | None = None) -> str:
    """HTMLを取得。site単位で IPv4強制・待機を切替可能。

    「他サイトは繋がるが特定ホストだけ connect timeout」への対処として、
    site.force_ipv4=true のホストは A(IPv4) のみで接続する（AAAA/IPv6を使わない）。
    失敗時はどの段階で落ちたか（DNS解決IP・接続先・例外種別）をログに残す。
    """
    site = site or {}
    host = urlparse(url).hostname or ""

    # ヘッダ（ブラウザ風）。config で user_agent 指定があればそれを優先。
    headers = dict(BROWSER_HEADERS)
    if scraping.get("user_agent"):
        headers["User-Agent"] = str(scraping["user_agent"])

    connect_timeout = float(scraping.get("connect_timeout", 40))
    read_timeout = float(scraping.get("timeout_seconds", 20))
    force_ipv4 = bool(site.get("force_ipv4", False))

    # ポライトアクセス：リクエスト前に待機（レート制限回避）。
    delay = float(site.get("request_delay_seconds", scraping.get("request_delay_seconds", 0)))
    if delay > 0:
        time.sleep(delay)

    prev_has_ipv6 = _urllib3_conn.HAS_IPV6
    session = _build_session(scraping)
    try:
        if force_ipv4:
            # 診断ログ：IPv4(A)の解決結果を出す。
            try:
                infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
                ips = sorted({info[4][0] for info in infos})
                print(f"[fetch] {host} IPv4(A)解決 -> {', '.join(ips) if ips else '(なし)'}", flush=True)
            except Exception as exc:  # DNS段階の失敗を明示
                print(f"[fetch] {host} IPv4 DNS解決に失敗: {type(exc).__name__}: {exc}", flush=True)
            _urllib3_conn.HAS_IPV6 = False  # 以降の接続を IPv4 のみに強制

        response = session.get(
            url,
            headers=headers,
            timeout=(connect_timeout, read_timeout),
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text
    except requests.exceptions.RequestException as exc:
        # どの段階で落ちたかを残す（connect/read/HTTP）。
        stage = "接続(connect)" if "ConnectTimeout" in type(exc).__name__ or "ConnectionError" in type(exc).__name__ else "取得"
        print(
            f"[fetch] {host} {stage}失敗 "
            f"(force_ipv4={force_ipv4}, connect_timeout={connect_timeout}s, read_timeout={read_timeout}s): "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        raise
    finally:
        _urllib3_conn.HAS_IPV6 = prev_has_ipv6  # 全体設定を元に戻す
        session.close()


def extract_text_blocks(soup: BeautifulSoup) -> list[str]:
    selectors = ["tr", "li", "p", "div", "section", "article"]
    blocks: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        for node in soup.select(selector):
            text = normalize_text(node.get_text(" ", strip=True))
            if len(text) < 10 or text in seen:
                continue
            seen.add(text)
            blocks.append(text)
    body_text = normalize_text(soup.get_text(" ", strip=True))
    if body_text:
        blocks.append(body_text)
    return blocks


def candidate_blocks(blocks: list[str], item: dict[str, Any], scraping: dict[str, Any]) -> list[str]:
    min_price = int(scraping.get("min_price", 100000))
    max_price = int(scraping.get("max_price", 300000))
    item_terms = ["iphone", "17", "pro", "max"]
    target_capacity = normalize_capacity(item.get("capacity", "256GB"))
    state_keywords = [str(k).lower() for k in item.get("state_keywords", [])]
    candidates: list[str] = []

    for block in blocks:
        lower = block.lower().replace(" ", "")
        prices = extract_prices(block, min_price, max_price)
        if not prices:
            continue
        item_score = sum(1 for term in item_terms if term in lower)
        # 容量トークンを正規化して判定（1000GB/1024GB→1TB などの表記ゆれに対応）
        has_capacity = any(cap == target_capacity for _, cap in capacity_positions(block))
        state_score = sum(1 for term in state_keywords if term.replace(" ", "") in lower)
        if item_score >= 3 and has_capacity:
            candidates.append(block)
        elif has_capacity and state_score >= 1:
            candidates.append(block)
    return candidates


def offers_from_candidates(
    site: dict[str, Any],
    item: dict[str, Any],
    candidates: list[str],
    source_updated_at: str | None,
) -> list[ScrapedOffer]:
    offers: list[ScrapedOffer] = []
    colors = item.get("colors", [])
    for block in candidates:
        matched_colors = colors_in_text(block, colors)
        if not matched_colors:
            matched_colors = colors
        for color in matched_colors:
            price = select_price(block, color, item)
            if price is None:
                continue
            price += color_adjustment(site, item, color)
            offers.append(
                ScrapedOffer(
                    shop_name=site["name"],
                    color_key=color["key"],
                    color_label=color["label"],
                    capacity=item.get("capacity", "256GB"),
                    state=state_from_text(block),
                    price=price,
                    source_updated_at=source_updated_at,
                    url=site["url"],
                    raw_text=block[:1000],
                )
            )
    return offers


def color_adjustment(site: dict[str, Any], item: dict[str, Any], color: dict[str, Any]) -> int:
    capacity = str(item.get("capacity", ""))
    by_capacity = site.get("color_adjustments_by_capacity", {})
    if capacity in by_capacity:
        return int(by_capacity.get(capacity, {}).get(color["key"], 0))
    return int(site.get("color_adjustments", {}).get(color["key"], 0))


def select_price(text: str, color: dict[str, Any], item: dict[str, Any]) -> int | None:
    """価格を「最も近い容量トークン・色・版(SIMフリー/Apple Store)」に紐付けて選ぶ。

    ブロック内に複数容量・複数キャリアの価格が混在していても、対象容量／対象色／
    SIMフリー版のみを採用する。これにより 256GB と 512GB が同じ値になる不具合を防ぐ。
    """
    target_capacity = normalize_capacity(item.get("capacity", "256GB"))
    color_key = color.get("key")
    colors = item.get("colors", [])
    caps = capacity_positions(text)

    scored: list[tuple[int, int, int]] = []
    for match in PRICE_RE.finditer(text):
        pos = match.start()
        try:
            price = int(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if not 100000 <= price <= 400000:
            continue

        # 1) 容量バインド: 価格の直前にある容量トークンが対象容量でなければ除外
        if nearest_capacity_before(caps, pos) != target_capacity:
            continue

        context = text[max(0, pos - 160) : pos]

        # 2) 版バインド: キャリア版のみの文脈なら除外、SIMフリー/Apple Storeは加点
        variant = variant_score(context)
        if variant is None:
            continue

        # 3) 色バインド: 直前に別の色があるならその色に属する価格とみなして除外
        near_color = nearest_color_before(text, pos, colors)
        if near_color is not None and near_color != color_key:
            continue

        # 4) 新品/中古などのラベルでスコアリング
        before = context.lower().replace(" ", "")
        score = variant
        for label in GOOD_PRICE_LABELS:
            if label.lower().replace(" ", "") in before:
                score += 200
        for label in BAD_PRICE_LABELS:
            if label.lower().replace(" ", "") in before[-40:]:
                score -= 300

        scored.append((score, -pos, price))

    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def find_keyword_positions(text: str, keywords: list[str]) -> list[int]:
    positions: list[int] = []
    lower = text.lower()
    for keyword in keywords:
        if not keyword:
            continue
        start = 0
        keyword_lower = str(keyword).lower()
        while True:
            index = lower.find(keyword_lower, start)
            if index < 0:
                break
            positions.append(index)
            start = index + len(keyword_lower)
    return positions


def colors_in_text(text: str, colors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lower = text.lower()
    matched = []
    for color in colors:
        keywords = color.get("keywords") or [color.get("label", ""), color.get("key", "")]
        if any(str(keyword).lower() in lower for keyword in keywords):
            matched.append(color)
    return matched


def extract_prices(text: str, min_price: int = 100000, max_price: int = 400000) -> list[int]:
    prices: list[int] = []
    for match in PRICE_RE.finditer(text):
        price = int(match.group(1).replace(",", ""))
        if min_price <= price <= max_price:
            prices.append(price)
    return prices


def find_updated_at(soup: BeautifulSoup) -> str | None:
    text = normalize_text(soup.get_text(" ", strip=True))
    for marker in ["更新", "最終更新", "価格更新", "更新日"]:
        index = text.find(marker)
        if index >= 0:
            window = text[index : index + 80]
            match = DATE_RE.search(window)
            if match:
                return match.group(1)
    time_tag = soup.find("time")
    if time_tag:
        value = time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
        return normalize_text(value) or None
    return None


def state_from_text(text: str) -> str:
    labels = []
    for keyword in ["国内版", "SIMフリー", "Apple Store", "新品", "未開封"]:
        if keyword.lower().replace(" ", "") in text.lower().replace(" ", ""):
            labels.append(keyword)
    return " / ".join(labels) if labels else "新品未開封"


def dedupe_offers(offers: list[ScrapedOffer]) -> list[ScrapedOffer]:
    best: dict[tuple[str, str, str], ScrapedOffer] = {}
    for offer in offers:
        key = (offer.shop_name, offer.capacity, offer.color_key)
        current = best.get(key)
        if current is None or offer.price > current.price:
            best[key] = offer
    return list(best.values())


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
