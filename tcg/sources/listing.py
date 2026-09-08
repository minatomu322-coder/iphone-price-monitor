from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..models import Product


CONDITION_RE = re.compile(r"〔([^〕]+)〕")
STOCK_RE = re.compile(r"(?:在庫数|募集数)\s*(\d+)")

# 同じ型番でも別カード扱いになる「バリアント記号」。商品側と一覧行側で集合が一致しないと不一致とみなす。
# 例: ナミ OP01-016 は 通常/パラレル/和柄SP/漫画背景SP/スーパーパラレル が同じ型番で価格が桁違い。
VARIANT_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("super_parallel", re.compile(r"スーパーパラレル")),
    ("parallel", re.compile(r"(?<!スーパー)パラレル")),
    ("sp", re.compile(r"(?<![A-Za-z])SP(?![A-Za-z])")),
    ("spc", re.compile(r"(?<![A-Za-z])SPC(?![A-Za-z])")),
    ("wagara", re.compile(r"和柄")),
    ("kaizokudan", re.compile(r"海賊団")),
    ("manga_bg", re.compile(r"漫画背景|コミパラ")),
    ("fullart", re.compile(r"フルアート")),
    ("gold", re.compile(r"金文字|金箔|箔押し")),
    ("mono", re.compile(r"モノクロ")),
    ("star2", re.compile(r"☆☆")),
    ("star1", re.compile(r"(?<!☆)☆(?!☆)")),
    ("graded", re.compile(r"PSA|BGS|ARS|鑑定")),
]


@dataclass
class Listing:
    """一覧ページの1行（名前テキスト・価格・URL・在庫・状態）。"""

    text: str
    price: int
    url: str = ""
    stock: int | None = None
    condition: str = ""


def normalize(text: str) -> str:
    """全角英数・記号を半角に寄せ、★を☆に統一し、空白を潰して照合しやすくする。"""
    table = str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９／－（）★",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/-()☆",
    )
    return re.sub(r"\s+", " ", text.translate(table)).strip()


def markers_of(text: str) -> set[str]:
    norm = normalize(text)
    return {key for key, pattern in VARIANT_MARKERS if pattern.search(norm)}


def product_markers(product: Product) -> set[str]:
    markers = markers_of(" ".join([product.name, *product.must_keywords]))
    if product.graded:
        markers.add("graded")
    return markers


def matches_product(product: Product, text: str) -> bool:
    """一覧行がこの商品を指すか。
    1) 型番があれば型番の一致を必須
    2) バリアント記号の集合が商品側と一致（片方にだけ「和柄」「SP」「☆☆」等があれば別カード）
    3) must/exclude キーワードで最終確認
    """
    norm = normalize(text)
    if product.card_no and normalize(product.card_no).lower() not in norm.lower():
        return False
    if markers_of(norm) != product_markers(product):
        return False
    return product.matches(norm)


def condition_of(text: str) -> str:
    match = CONDITION_RE.search(text)
    return match.group(1).strip() if match else ""


def stock_of(text: str) -> int | None:
    match = STOCK_RE.search(text)
    return int(match.group(1)) if match else None


def condition_excluded(condition: str, excluded_words: list[str]) -> bool:
    return any(word in condition for word in excluded_words)


def debug_enabled() -> bool:
    return bool(os.getenv("TCG_DEBUG"))
