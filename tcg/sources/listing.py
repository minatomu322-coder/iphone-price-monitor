from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..models import Product


CONDITION_RE = re.compile(r"〔([^〕]+)〕")
STOCK_RE = re.compile(r"(?:在庫数|募集数)\s*(\d+)")


@dataclass
class Listing:
    """一覧ページの1行（名前テキスト・価格・URL・在庫・状態）。"""

    text: str
    price: int
    url: str = ""
    stock: int | None = None
    condition: str = ""


def normalize(text: str) -> str:
    """全角英数・記号を半角に寄せ、空白を潰して照合しやすくする。"""
    table = str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９／－（）",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/-()",
    )
    return re.sub(r"\s+", " ", text.translate(table)).strip()


def matches_product(product: Product, text: str) -> bool:
    """一覧行がこの商品を指すか。型番があれば型番の一致を必須にし、加えて must/exclude キーワードで絞る。"""
    norm = normalize(text)
    if product.card_no and normalize(product.card_no).lower() not in norm.lower():
        return False
    if not product.graded and re.search(r"PSA|BGS|ARS|鑑定", norm):
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
