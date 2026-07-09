from __future__ import annotations

import pytest

from rakuten_finder.config import Assumptions, ScoringWeights, Thresholds
from rakuten_finder.models import MercariStats, RakutenItem


@pytest.fixture
def item() -> RakutenItem:
    return RakutenItem(
        item_code="shop:10001234",
        name="Nintendo Switch 有機ELモデル ホワイト 4902370550733",
        price=30000,
        url="https://item.rakuten.co.jp/shop/10001234/",
        shop_name="テストショップ",
        keyword="Nintendo Switch 有機EL",
        shipping_included=True,
        point_rate=1.0,
        in_stock=True,
        image_url="https://example.com/image.jpg",
        jan="4902370550733",
    )


@pytest.fixture
def stats() -> MercariStats:
    return MercariStats(
        query="4902370550733",
        sold_median=36500,
        sold_min=34000,
        sold_avg=36200.0,
        sold_count=25,
        active_count=6,
        active_min=35800,
        stability=0.9,
    )


@pytest.fixture
def assumptions() -> Assumptions:
    return Assumptions(
        spu_rate=5.0,
        campaign_rate=0.0,
        point_cap=0,
        mercari_fee_rate=0.10,
        shipping_out=200,
        default_shipping_in=0,
        sell_percentile="median",
    )


@pytest.fixture
def weights() -> ScoringWeights:
    return ScoringWeights()


@pytest.fixture
def thresholds() -> Thresholds:
    return Thresholds()
