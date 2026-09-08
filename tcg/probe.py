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
    # 既存ソース（403確認用）
    "https://www.suruga-ya.jp/kaitori/kaitori_detail/GN285556",
    "https://www.suruga-ya.jp/product/detail/GN285556",
    # 公式API（キー無しでも「到達できるか」は分かる。400/401が返れば到達OK）
    "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601?format=json&keyword=test",
    "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch?query=test",
    # 買取価格を公開している専門店・買取店
    "https://buy.dorasuta.jp/pokemon-card/product?pid=489979",
    "https://www.c-labo-kaitori.jp/product/55856",
    "https://duke-kaitori.jp/product/349-190/",
    "https://kaitori-toretoku.jp/buypricelist/pokemon",
    "https://yuyu-tei.jp/",
    "https://www.cardrush-pokemon.jp/",
    # 相場アグリゲータ（販売/買取の推移）
    "https://price-base.com/pokemon/card/charizard-349-190-sar",
    "https://pokecazilla.com/products/detail/21249",
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
