"""候補データソースの到達性と robots.txt を確認する（Actions 上で実行して判断材料にする）。

    python -m tcg.probe

各URLについて 1回だけ通常の GET を行い、HTTPステータス・サイズ・価格ラベルの有無と、
そのホストの robots.txt の Disallow 行を表示する。回避策は一切行わない。
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from .sources import build_session


CANDIDATES = [
    # 楽天 新基盤（2026-02〜）のパス候補。キー無しで 400/401 が返るものが正しいパス（404は不在）
    "https://openapi.rakuten.co.jp/services/api/IchibaItem/Search/20220601?format=json&keyword=test",
    "https://openapi.rakuten.co.jp/ichiba/item/search/20220601?format=json&keyword=test",
    "https://openapi.rakuten.co.jp/api/IchibaItem/Search/20220601?format=json&keyword=test",
    "https://openapi.rakuten.co.jp/ichibaitem/search/20220601?format=json&keyword=test",
    # Yahoo!ショッピングAPI（401＝到達OK）
    "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch?query=test",
    # 採用ソースの一覧ページ（URLの存在確認）
    "https://price-base.com/useful/pokemon-kaitorilist",
    "https://price-base.com/useful/fusionworld-kaitorilist",
    "https://kaitori-toretoku.jp/buypricelist/onepiece",
    "https://www.cardrush-op.jp/product-list?keyword=%E3%83%8A%E3%83%9F",
    # 参考: 403 だったソース（再確認用）
    "https://www.suruga-ya.jp/product/detail/GN285556",
    "https://cardrush.media/pokemon/buying_prices",
]

LABELS = ["買取価格", "買取", "販売価格", "価格"]


def robots_summary(session, host: str) -> str:
    try:
        response = session.get(f"https://{host}/robots.txt", timeout=(15, 20))
    except Exception as exc:
        return f"robots.txt 取得失敗: {exc}"
    if response.status_code != 200:
        return f"robots.txt HTTP {response.status_code}"
    lines = [line.strip() for line in response.text.splitlines() if line.strip()]
    interesting = [line for line in lines if line.lower().startswith(("user-agent", "disallow", "allow", "crawl-delay"))]
    return " | ".join(interesting[:25]) or "(ルール無し)"


def main() -> None:
    session = build_session({"retry_total": 0})
    seen_hosts: dict[str, str] = {}
    for url in CANDIDATES:
        host = urlparse(url).netloc
        if host not in seen_hosts:
            seen_hosts[host] = robots_summary(session, host)
            print(f"\n=== {host}\n  robots: {seen_hosts[host]}")
        time.sleep(2)
        try:
            response = session.get(url, timeout=(15, 20))
            text = response.text
            labels = [label for label in LABELS if label in text]
            print(f"  GET {url}\n    HTTP {response.status_code} / {len(text)} chars / ラベル: {labels or 'なし'}")
            if response.status_code != 200:
                print(f"    本文先頭: {text[:160]!r}")
        except Exception as exc:
            print(f"  GET {url}\n    失敗: {exc}")


if __name__ == "__main__":
    main()
