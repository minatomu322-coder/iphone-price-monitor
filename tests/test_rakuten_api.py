from __future__ import annotations

from rakuten_finder.models import RakutenItem
from rakuten_finder.rakuten_api import parse_item, _matches_ng

RAW = {
    "itemCode": "shop:10001234",
    "itemName": "Nintendo Switch 有機ELモデル ホワイト JAN:4902370550733",
    "itemPrice": 33000,
    "itemUrl": "https://item.rakuten.co.jp/shop/10001234/",
    "shopName": "テストショップ",
    "postageFlag": 0,
    "pointRate": 2,
    "availability": 1,
    "genreId": "101205",
    "catchcopy": "新品未開封",
    "mediumImageUrls": [{"imageUrl": "https://thumbnail.image.rakuten.co.jp/x.jpg"}],
    "reviewCount": 12,
    "reviewAverage": 4.5,
}


def test_parse_item_full():
    item = parse_item(RAW, "Nintendo Switch 有機EL")
    assert item is not None
    assert item.item_code == "shop:10001234"
    assert item.price == 33000
    assert item.shipping_included is True     # postageFlag=0 → 送料込み
    assert item.point_rate == 2.0
    assert item.jan == "4902370550733"        # 商品名から JAN 抽出
    assert item.image_url == "https://thumbnail.image.rakuten.co.jp/x.jpg"
    assert item.in_stock is True


def test_parse_item_format_version2_image_list():
    raw = dict(RAW, mediumImageUrls=["https://example.com/direct.jpg"])
    item = parse_item(raw, "kw")
    assert item is not None
    assert item.image_url == "https://example.com/direct.jpg"


def test_parse_item_broken_returns_none():
    assert parse_item({"itemName": "価格なし"}, "kw") is None


def test_extract_jan():
    assert RakutenItem.extract_jan("商品 4902370550733 です") == "4902370550733"
    assert RakutenItem.extract_jan("JANなし", "こっちも無し") is None


def test_ng_keywords():
    assert _matches_ng("Switch 中古 美品", ["中古", "ジャンク"]) is True
    assert _matches_ng("Switch 新品", ["中古"]) is False
