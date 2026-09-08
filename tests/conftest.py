from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from tcg.models import Metrics, Product


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config() -> dict:
    with (ROOT / "tcg" / "config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def single() -> Product:
    return Product(
        product_id="pkm_test_sar",
        title="pokemon",
        name="テストex SAR",
        set_code="SV9",
        card_no="100/100",
        rarity="SAR",
        form="single",
        release_date=date(2026, 6, 1),
        search_keyword="テストex SAR 100/100",
        must_keywords=("テスト", "SAR"),
        exclude_keywords=("PSA", "プロキシ"),
        supply_status="active",
    )


@pytest.fixture
def box() -> Product:
    return Product(
        product_id="op_test_box",
        title="onepiece",
        name="テスト弾 BOX",
        set_code="OP99",
        form="box",
        release_date=date(2025, 1, 1),
        supply_status="discontinued",
    )


def make_metrics(product: Product, as_of: date = date(2026, 9, 8), **kw) -> Metrics:
    m = Metrics(product_id=product.product_id, as_of=as_of)
    for key, value in kw.items():
        setattr(m, key, value)
    return m
