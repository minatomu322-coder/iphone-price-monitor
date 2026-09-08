"""駿河屋ページの価格抽出を目視確認するための補助コマンド。

    python -m tcg.inspect <URL>            # 販売ページ／検索ページ
    python -m tcg.inspect <URL> --kaitori  # 買取ページ

抽出された価格・在庫と、ラベル周辺のテキストを表示する。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from .main import PACKAGE_DIR, load_config
from .models import Product
from .sources import build_session
from .sources.surugaya import (
    KAITORI_LABELS,
    SELL_LABELS,
    _flatten,
    detect_stock,
    labeled_price,
    parse_search_page,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--kaitori", action="store_true")
    parser.add_argument("--must", default="", help="検索ページ用: 必須キーワード（|区切り）")
    parser.add_argument("--config", default=str(PACKAGE_DIR / "config.yaml"))
    args = parser.parse_args()

    config = load_config(Path(args.config))
    session = build_session(config.get("scraping", {}))
    response = session.get(args.url, timeout=30)
    response.encoding = response.apparent_encoding or response.encoding
    html = response.text
    text = _flatten(html)
    labels = KAITORI_LABELS if args.kaitori else SELL_LABELS

    print(f"HTTP {response.status_code} / {len(html)} bytes")
    print(f"抽出価格: {labeled_price(text, labels)}  在庫: {detect_stock(text)}")
    for label in labels:
        for match in list(re.finditer(re.escape(label), text))[:3]:
            start = max(0, match.start() - 30)
            print(f"  [{label}] …{text[start:match.end() + 60]}…")

    if "search" in args.url:
        product = Product(
            product_id="inspect", title="pokemon", name="inspect",
            must_keywords=tuple(k for k in args.must.split("|") if k),
        )
        found = parse_search_page(html, product, args.url, config.get("sources", {}).get("surugaya", {}))
        print(f"検索ページ抽出: {len(found)}件")
        for obs in found:
            print(f"  {obs.price:,}円 stock={obs.stock} {obs.raw_text[:60]} {obs.url}")


if __name__ == "__main__":
    main()
