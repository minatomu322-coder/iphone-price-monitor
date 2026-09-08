from __future__ import annotations

from datetime import date

from tcg.models import Product
from tcg.sources.cardrush import parse_search_results, to_observations
from tcg.sources.kaitori_lists import parse_pricebase, parse_toretoku
from tcg.sources.listing import matches_product, normalize


# ---- 2026-09-08 の Actions 上ダンプに基づく最小フィクスチャ ----
CARDRUSH_HTML = """
<ul>
<li class="list_item_cell list_item_51078"><div class="item_data"><a class="item_data_link" href="/product/51078">
  <p class="item_name"><span class="goods_name">リザードンex 【 SAR 】{349/190} [ SV4a ]</span></p></a>
  <div class="item_info"><div class="price"><p class="selling_price"><span class="figure">37,800円</span> (税込)</p></div>
  <p class="stock">在庫数 55枚</p></div></div></li>
<li class="list_item_cell list_item_45925"><div class="item_data"><a class="item_data_link" href="https://www.cardrush-pokemon.jp/product/45925">
  <p class="item_name"><span class="goods_name">リザードンex 【 SAR 】{134/108} [ SV3 ]</span></p></a>
  <div class="item_info"><div class="price"><p class="selling_price"><span class="figure">49,800円</span></p></div><p>在庫数 39枚</p></div></div></li>
<li class="list_item_cell list_item_99"><div class="item_data"><a class="item_data_link" href="/product/99">
  <p class="item_name"><span class="goods_name">〔状態B〕 リザードンex 【 SAR 】{349/190} [ SV4a ]</span></p></a>
  <div class="item_info"><div class="price"><p class="selling_price"><span class="figure">29,800円</span></p></div><p>在庫数 3枚</p></div></div></li>
<li class="list_item_cell list_item_98"><div class="item_data"><a class="item_data_link" href="/product/98">
  <p class="item_name"><span class="goods_name">〔PSA10鑑定済〕 リザードンex 【 SAR 】{349/190} [ SV4a ]</span></p></a>
  <div class="item_info"><div class="price"><p class="selling_price"><span class="figure">79,800円</span></p></div><p>在庫数 1枚</p></div></div></li>
<li class="list_item_cell list_item_97"><div class="item_data"><a class="item_data_link" href="/product/97">
  <p class="item_name"><span class="goods_name">〔状態A-〕 リザードンex 【 SAR 】{349/190} [ SV4a ]</span></p></a>
  <div class="item_info"><div class="price"><p class="selling_price"><span class="figure">31,800円</span></p></div><p>在庫数 0枚</p></div></div></li>
</ul>
"""

TORETOKU_HTML = """
<div class="list_wrap js_list_wrap onepiece"><ul>
<li><div class="item"><p class="item__price">買取価格 <span class="price">￥21,900</span></p><p class="item__name">モンキー・D・ルフィ (パラレル) OP01-003 L</p></div></li>
<li><div class="item"><p class="item__price">買取価格 <span class="price">￥7,200</span></p><p class="item__name">ナミ (パラレル) OP01-016 R</p></div></li>
<li><div class="item"><p class="item__price">買取価格 <span class="price">￥260,000</span></p><p class="item__name">ナミ (スーパーパラレル) OP01-016 R</p></div></li>
</ul></div>
"""

PRICEBASE_HTML = """
<div class="pack-section"><ul class="price-list price-list-all">
<li><p class="price-list-title">モンキー・Ｄ・ルフィ(パラレル/illust:tatsuya)【SEC】{OP11-118}</p><p class="price-list-price">￥58,000</p><p class="price-list-date">2026年9月08日現在</p></li>
<li><p class="price-list-title">ナミ（パラレル）</p><p class="price-list-meta">R OP01-016</p><p class="price-list-price">￥6,500</p><p class="price-list-date">2026年9月08日現在</p></li>
<li><p class="price-list-title">ナミ（スーパーパラレル）</p><p class="price-list-meta">SP OP01-016</p><p class="price-list-price">￥250,000</p><p class="price-list-date">2026年9月08日現在</p></li>
</ul></div>
"""


def lizardon() -> Product:
    return Product(
        product_id="pkm", title="pokemon", name="リザードンex SAR", set_code="SV4a", card_no="349/190",
        must_keywords=("リザードン", "SAR"), exclude_keywords=("プロキシ",),
    )


def nami() -> Product:
    return Product(
        product_id="op", title="onepiece", name="ナミ パラレル", set_code="OP01", card_no="OP01-016",
        must_keywords=("ナミ", "パラレル"), exclude_keywords=("スーパーパラレル",), release_date=date(2022, 7, 8),
    )


def test_cardrush_parse_and_filter():
    listings = parse_search_results(CARDRUSH_HTML, "https://www.cardrush-pokemon.jp/product-list?keyword=x")
    assert len(listings) == 5
    assert listings[0].url == "https://www.cardrush-pokemon.jp/product/51078"
    assert listings[0].stock == 55 and listings[2].condition == "状態B"
    found = to_observations(lizardon(), listings, {"max_listings": 5})
    # 型番一致(349/190)・状態B/PSA除外・在庫0除外 → 37,800 のみ
    assert [o.price for o in found] == [37800]
    assert found[0].side == "buy" and found[0].source == "cardrush"


def test_toretoku_parse_and_match():
    listings = parse_toretoku(TORETOKU_HTML)
    assert [l.price for l in listings] == [21900, 7200, 260000]
    matched = [l for l in listings if matches_product(nami(), l.text)]
    assert [l.price for l in matched] == [7200]      # スーパーパラレルは除外


def test_pricebase_parse_and_match_fullwidth():
    listings = parse_pricebase(PRICEBASE_HTML)
    assert [l.price for l in listings] == [58000, 6500, 250000]
    matched = [l for l in listings if matches_product(nami(), l.text)]
    assert [l.price for l in matched] == [6500]
    luffy = Product(product_id="l", title="onepiece", name="ルフィ", card_no="OP11-118", must_keywords=("ルフィ",))
    assert matches_product(luffy, listings[0].text)   # 全角Ｄ・全角括弧でも一致


def test_normalize_and_psa_guard():
    assert normalize("モンキー・Ｄ・ルフィ（パラレル）ＯＰ０１－００３") == "モンキー・D・ルフィ(パラレル)OP01-003"
    raw = Product(product_id="x", title="pokemon", name="n", card_no="349/190", must_keywords=("リザードン",))
    assert not matches_product(raw, "リザードンex PSA10 {349/190}")
    graded = Product(product_id="x", title="pokemon", name="n", card_no="349/190", must_keywords=("リザードン",), graded=True)
    assert matches_product(graded, "リザードンex PSA10 {349/190}")
