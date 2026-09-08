"""候補ページの構造を把握するためのダンプ（Actions 上で1回実行し、ログを読んでパーサを書く）。

    python -m tcg.dump [URL ...]

各URLに1回だけ GET し、価格らしき文字列を含む要素のタグ/クラスの頻度、
表や繰り返し要素の先頭数件のテキストを表示する。回避策は行わない。
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter

from bs4 import BeautifulSoup

from .sources import build_session
from .sources.base import PRICE_RE


DEFAULT_URLS = [
    # 楽天 新ドメイン（キー無しの応答コードで到達確認）
    "https://openapi.rakuten.co.jp/services/api/IchibaItem/Search/20220601?format=json&keyword=test",
    # カードラッシュ 買取価格一覧（ラッシュメディア）
    "https://cardrush.media/pokemon/buying_prices",
    "https://cardrush.media/onepiece/buying_prices",
    "https://cardrush.media/dragon_ball/buying_prices",
    # カードラッシュ 通販（販売価格）
    "https://www.cardrush-pokemon.jp/product-list?keyword=%E3%83%AA%E3%82%B6%E3%83%BC%E3%83%89%E3%83%B3ex+SAR",
    "https://www.cardrush-op.jp/product-list?keyword=%E3%83%8A%E3%83%9F+%E3%83%91%E3%83%A9%E3%83%AC%E3%83%AB",
    "https://www.cardrush-db.jp/product-list?keyword=%E3%83%99%E3%82%B8%E3%83%83%E3%83%88+SCR",
    # C-labo 買取（弾ごとの一覧・単品）
    "https://www.c-labo-kaitori.jp/product-list/742",
    "https://www.c-labo-kaitori.jp/product/55856",
    # トレトク 買取価格表
    "https://kaitori-toretoku.jp/buypricelist/pokemon",
    "https://kaitori-toretoku.jp/buypricelist/onepiece",
    # PRICE BASE（販売/買取の集計）
    "https://price-base.com/pokemon/card/charizard-349-190-sar",
    "https://price-base.com/useful/onepiece-kaitorilist",
]

LABEL_RE = re.compile(r"買取価格|買取|販売価格|価格|在庫")


def describe(el) -> str:
    classes = ".".join(el.get("class", [])) if hasattr(el, "get") else ""
    return f"{el.name}.{classes}" if classes else el.name


def dump(html: str, url: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    print(f"\n##### {url}\n  {len(html)} bytes / text {len(text)} chars\n  先頭: {text[:300]!r}")

    # 価格らしき文字列を「直接」含む末端要素のタグ/クラス頻度
    price_holders = Counter()
    samples: dict[str, list[str]] = {}
    for el in soup.find_all(True):
        own = "".join(el.find_all(string=True, recursive=False)).strip()
        if own and PRICE_RE.search(own):
            key = describe(el)
            price_holders[key] += 1
            samples.setdefault(key, [])
            if len(samples[key]) < 2:
                samples[key].append(own[:60])
    print("  価格を含む要素 上位:")
    for key, n in price_holders.most_common(8):
        print(f"    {n:4d} {key}  例: {samples[key]}")

    # 価格要素の親を3階層たどって「1商品ブロック」候補を推定
    parents = Counter()
    for el in soup.find_all(True):
        own = "".join(el.find_all(string=True, recursive=False)).strip()
        if own and PRICE_RE.search(own):
            p = el
            for _ in range(3):
                p = p.parent
                if p is None or p.name in ("body", "html"):
                    break
                parents[describe(p)] += 1
    print("  価格要素の祖先 上位:")
    for key, n in parents.most_common(6):
        print(f"    {n:4d} {key}")

    # 表・繰り返し要素の先頭
    rows = soup.select("table tr")
    if rows:
        print(f"  table tr: {len(rows)}行")
        for tr in rows[:4]:
            print("    | " + " | ".join(td.get_text(' ', strip=True)[:40] for td in tr.find_all(["td", "th"]))[:200])
    for selector in ("li", "article", "div[class*=item]", "div[class*=product]", "div[class*=card]", "a[href*=product]"):
        found = soup.select(selector)
        hits = [e for e in found if PRICE_RE.search(e.get_text(" ", strip=True) or "") and len(e.get_text(" ", strip=True)) < 400]
        if hits:
            print(f"  {selector}: 価格を含むもの {len(hits)}件")
            for e in hits[:3]:
                link = e.get("href") if e.name == "a" else (e.find("a", href=True) or {}).get("href")
                print(f"    [{describe(e)}] {e.get_text(' ', strip=True)[:160]!r} href={link}")

    # ラベル周辺
    for match in list(LABEL_RE.finditer(text))[:6]:
        start = max(0, match.start() - 20)
        print(f"  …{text[start:match.end() + 50]}…")


def main() -> None:
    urls = sys.argv[1:] or DEFAULT_URLS
    session = build_session({"retry_total": 0})
    for url in urls:
        time.sleep(5)
        try:
            response = session.get(url, timeout=(15, 30))
            response.encoding = response.apparent_encoding or response.encoding
            if response.status_code != 200:
                print(f"\n##### {url}\n  HTTP {response.status_code}: {response.text[:300]!r}")
                continue
            dump(response.text, url)
        except Exception as exc:
            print(f"\n##### {url}\n  失敗: {exc}")


if __name__ == "__main__":
    main()
