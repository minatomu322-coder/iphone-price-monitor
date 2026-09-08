from __future__ import annotations

from tcg.sources.base import parse_price
from tcg.sources.manual_sheet import ManualSheetSource
from tcg.sources.rakuten import parse_items
from tcg.sources.surugaya import labeled_price, parse_kaitori_page, parse_product_page, parse_search_page


def test_parse_price_variants():
    assert parse_price("¥3,980（税込）") == 3980
    assert parse_price("販売価格 12,800円") == 12800
    assert parse_price("980円") == 980
    assert parse_price("在庫 3") is None


def test_rakuten_parse_items_filters_and_adds_postage(single, config):
    payload = {
        "Items": [
            {"Item": {"itemName": "テストex SAR 100/100 美品", "itemPrice": 12000, "itemUrl": "u1", "shopName": "A", "postageFlag": 0}},
            {"Item": {"itemName": "テストex SAR PSA10", "itemPrice": 9000, "itemUrl": "u2", "shopName": "B", "postageFlag": 0}},
            {"Item": {"itemName": "テストex SAR 100/100", "itemPrice": 11800, "itemUrl": "u3", "shopName": "C", "postageFlag": 1}},
            {"Item": {"itemName": "別のカード SR", "itemPrice": 100, "itemUrl": "u4", "shopName": "D", "postageFlag": 0}},
        ]
    }
    found = parse_items(payload, single, config["sources"]["rakuten"])
    assert [o.price for o in found] == [12000, 12100]   # PSA除外・送料別は+300
    assert found[0].side == "buy" and found[0].channel == "rakuten"


PRODUCT_HTML = """
<html><body><h1>テストex SAR</h1>
<div class="box"><span>販売価格</span> <span class="p">¥12,800</span> (税込)</div>
<div>在庫：3</div></body></html>
"""

KAITORI_HTML = """
<html><body><h1>テストex SAR</h1><script>var x = "買取価格 999999";</script>
<table><tr><td>買取価格</td><td>¥9,500</td></tr></table></body></html>
"""

SEARCH_HTML = """
<html><body>
<div class="item"><a href="/product/detail/1"><p class="title">テストex SAR 100/100</p></a><p class="price">¥13,000</p></div>
<div class="item"><a href="/product/detail/2"><p class="title">テストex SAR 100/100 PSA10</p></a><p class="price">¥30,000</p></div>
<div class="item"><a href="/product/detail/3"><p class="title">テストex SAR 100/100</p></a><p class="price">¥12,500</p><span>品切れ</span></div>
</body></html>
"""


def test_surugaya_product_page(single):
    obs = parse_product_page(PRODUCT_HTML, single, "https://x/product")
    assert obs is not None
    assert obs.price == 12800 and obs.stock == 3 and obs.side == "buy"


def test_surugaya_kaitori_page_ignores_script(single):
    obs = parse_kaitori_page(KAITORI_HTML, single, "https://x/kaitori")
    assert obs is not None
    assert obs.price == 9500 and obs.side == "sell" and obs.channel == "kaitori"


def test_surugaya_search_page(single, config):
    found = parse_search_page(SEARCH_HTML, single, "https://x/search", config["sources"]["surugaya"])
    assert [o.price for o in found] == [12500, 13000]
    assert found[0].stock == 0
    assert found[1].url == "https://www.suruga-ya.jp/product/detail/1"


def test_labeled_price_none_when_missing():
    assert labeled_price("特に価格なし", ["販売価格"]) is None


def test_manual_sheet(tmp_path, single):
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(
        "product_id,channel,price_median,sold_count_30d,condition,updated_on\n"
        f"{single.product_id},mercari,15000,12,美品,2026-09-07\n"
        f"{single.product_id},yahoo,未入力,,,\n"
        "other,mercari,1,1,,\n",
        encoding="utf-8",
    )
    found = ManualSheetSource(sheet).fetch(single)
    assert len(found) == 1
    assert found[0].price == 15000 and found[0].sold_count_30d == 12
    assert found[0].observed_at == "2026-09-07"
    assert ManualSheetSource(tmp_path / "missing.csv").fetch(single) == []
