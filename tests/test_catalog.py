from __future__ import annotations

from pathlib import Path

import pytest

from tcg.catalog import load_products, row_to_product
from tcg.models import Product


ROOT = Path(__file__).resolve().parents[1]


def test_seed_products_load_and_are_unique():
    products = load_products(ROOT / "data" / "products.csv")
    assert products, "商品マスタが空"
    ids = [p.product_id for p in products]
    assert len(ids) == len(set(ids))
    assert {p.title for p in products} == {"pokemon", "onepiece", "fusionworld"}


def test_matches_uses_must_and_exclude_keywords():
    p = Product(
        product_id="x", title="pokemon", name="n",
        must_keywords=("リザードン", "SAR"), exclude_keywords=("PSA", "プロキシ"),
    )
    assert p.matches("ポケモンカード リザードンex SAR 349/190 美品")
    assert not p.matches("リザードンex SAR PSA10 鑑定品")
    assert not p.matches("リザードンex SR")


def test_row_to_product_rejects_unknown_title():
    with pytest.raises(ValueError):
        row_to_product({"product_id": "a", "title": "yugioh", "name": "n"})


def test_display_no_and_keyword_fallback():
    p = Product(product_id="x", title="onepiece", name="ナミ", set_code="OP01", card_no="OP01-016")
    assert p.display_no == "OP01 OP01-016"
    assert p.keyword == "ナミ"
