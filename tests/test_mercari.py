from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercari.db import MercariDatabase
from mercari.decision import primary_judgement
from mercari.exports import build_payload, render, sales_payload, sourcing_payload
from mercari.importer import import_listing_json
from mercari.profit import breakeven_price, estimate_profit, price_ladder, sales_fee


CONFIG = {
    "fees": {"default_rate": 0.10},
    "pricing": {"quick_ratio": 0.90, "standard_ratio": 1.00, "strong_ratio": 1.08, "round_to": 100},
    "thresholds": {
        "min_profit": 1000,
        "min_roi": 0.15,
        "min_sold_count": 3,
        "max_active_ratio": 3.0,
        "stale_days": 14,
        "long_stock_days": 30,
    },
}


class ProfitTest(unittest.TestCase):
    def test_sales_fee(self) -> None:
        self.assertEqual(sales_fee(10000), 1000)
        self.assertEqual(sales_fee(9999), 999)

    def test_estimate_profit(self) -> None:
        est = estimate_profit(10000, purchase_price=5000, purchase_shipping=500, sell_shipping=700)
        # 10000 - 1000(手数料) - 700(送料) - 5500(原価) = 2800
        self.assertEqual(est.profit, 2800)
        self.assertAlmostEqual(est.roi, round(2800 / 5500, 3))

    def test_price_ladder(self) -> None:
        ladder = price_ladder(10000, CONFIG["pricing"])
        self.assertEqual(ladder["quick"], 9000)
        self.assertEqual(ladder["standard"], 10000)
        self.assertEqual(ladder["strong"], 10800)

    def test_breakeven(self) -> None:
        price = breakeven_price(5000, 500, 700)
        est = estimate_profit(price, purchase_price=5000, purchase_shipping=500, sell_shipping=700)
        self.assertGreaterEqual(est.profit, 0)
        below = estimate_profit(price - 100, purchase_price=5000, purchase_shipping=500, sell_shipping=700)
        self.assertLess(below.profit, 0)


class DbAndDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_item(self, **overrides) -> int:
        data = {
            "name": "テスト商品",
            "purchase_price": 5000,
            "purchase_shipping": 500,
            "shipping_cost": 700,
            "status": "candidate",
        }
        data.update(overrides)
        return self.db.upsert_item(data)

    def test_upsert_and_update(self) -> None:
        item_id = self._make_item()
        self.db.upsert_item({"id": item_id, "brand": "テストブランド"})
        item = self.db.get_item(item_id)
        self.assertEqual(item["brand"], "テストブランド")
        self.assertEqual(item["purchase_price"], 5000)  # 既存値は維持

    def test_judgement_buy(self) -> None:
        item_id = self._make_item()
        self.db.insert_market_snapshot({
            "item_id": item_id, "median_price": 10000, "sold_count": 10, "active_count": 5,
        })
        item = self.db.get_item(item_id)
        judgement = primary_judgement(
            item, self.db.latest_market(item_id), self.db.market_history(item_id), CONFIG
        )
        self.assertEqual(judgement["label"], "買い候補")
        self.assertEqual(judgement["ladder"]["standard"]["profit"], 2800)

    def test_judgement_need_info_without_market(self) -> None:
        item_id = self._make_item()
        judgement = primary_judgement(self.db.get_item(item_id), None, [], CONFIG)
        self.assertEqual(judgement["label"], "追加確認")

    def test_judgement_skip_when_loss(self) -> None:
        item_id = self._make_item(purchase_price=12000)
        self.db.insert_market_snapshot({
            "item_id": item_id, "median_price": 10000, "sold_count": 10, "active_count": 5,
        })
        item = self.db.get_item(item_id)
        judgement = primary_judgement(
            item, self.db.latest_market(item_id), self.db.market_history(item_id), CONFIG
        )
        self.assertEqual(judgement["label"], "見送り候補")

    def test_sale_updates_status(self) -> None:
        item_id = self._make_item(status="listed")
        listing_id = self.db.upsert_listing({
            "item_id": item_id, "status": "active", "list_price": 10000,
            "current_price": 10000, "listed_at": "2026-06-01",
        })
        self.db.record_sale({
            "item_id": item_id, "listing_id": listing_id,
            "sold_price": 9800, "sales_fee": 980, "shipping_cost": 700,
            "sold_at": "2026-07-01",
        })
        self.assertEqual(self.db.get_item(item_id)["status"], "sold")
        self.assertEqual(self.db.get_listing(listing_id)["status"], "sold")

    def test_price_change_history(self) -> None:
        item_id = self._make_item()
        listing_id = self.db.upsert_listing({
            "item_id": item_id, "status": "active", "list_price": 10000, "current_price": 10000,
        })
        self.db.record_price_change(listing_id, 9500, "売れないため")
        changes = self.db.price_changes_for_listing(listing_id)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["old_price"], 10000)
        self.assertEqual(changes[0]["new_price"], 9500)
        self.assertEqual(self.db.get_listing(listing_id)["current_price"], 9500)


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")
        self.item_id = self.db.upsert_item({
            "name": "ポケカ リザードンex SAR",
            "model_number": "sv4a 205/190",
            "purchase_price": 5000,
            "purchase_shipping": 300,
            "shipping_cost": 210,
            "condition": "白かけなし",
            "accessories": "スリーブ・ローダー",
            "status": "candidate",
        })
        self.db.insert_market_snapshot({
            "item_id": self.item_id,
            "min_price": 8000, "median_price": 9000, "mean_price": 9200, "max_price": 12000,
            "sold_count": 15, "active_count": 20,
            "url": "https://example.com/search",
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sourcing_all_formats(self) -> None:
        payload = sourcing_payload(self.db, self.item_id, CONFIG)
        labels = [f[0] for f in payload.fields]
        for expected in ("商品名", "仕入れ価格", "想定利益", "ROI", "相場中央値",
                         "売り切れ件数", "販売中件数", "価格履歴", "システムの一次判定"):
            self.assertIn(expected, labels)
        for fmt in ("text", "json", "csv", "markdown"):
            text = render(payload, fmt)
            self.assertIn("リザードン", text)
        data = json.loads(render(payload, "json"))
        self.assertEqual(data["種別"], "sourcing")
        self.assertEqual(data["項目"]["仕入れ価格"], 5000)

    def test_listing_export(self) -> None:
        payload = build_payload(self.db, "listing", CONFIG, item_id=self.item_id)
        text = render(payload, "text")
        self.assertIn("商品名候補", text)
        self.assertIn("相場データ", text)

    def test_stale_export_requires_listing(self) -> None:
        with self.assertRaises(ValueError):
            build_payload(self.db, "stale", CONFIG, item_id=self.item_id)
        listing_id = self.db.upsert_listing({
            "item_id": self.item_id, "status": "active", "title": "リザードンex SAR 美品",
            "list_price": 9500, "current_price": 9000, "listed_at": "2026-06-01",
            "views": 120, "likes": 8, "comments": 1,
        })
        self.db.record_price_change(listing_id, 8800, "2週間売れず")
        payload = build_payload(self.db, "stale", CONFIG, item_id=self.item_id)
        text = render(payload, "text")
        self.assertIn("経過日数", text)
        self.assertIn("値下げ履歴", text)
        self.assertIn("9,000円→8,800円", text)

    def test_sales_export(self) -> None:
        self.db.upsert_item({"id": self.item_id, "purchased_at": "2026-06-20", "category": "トレカ"})
        listing_id = self.db.upsert_listing({
            "item_id": self.item_id, "status": "active", "list_price": 9000, "listed_at": "2026-06-25",
        })
        self.db.record_sale({
            "item_id": self.item_id, "listing_id": listing_id,
            "sold_price": 9000, "sales_fee": 900, "shipping_cost": 210,
            "sold_at": "2026-07-05",
        })
        payload = sales_payload(self.db, "2026-07-01", "2026-07-31", CONFIG)
        raw = {label: value for label, value, _d in payload.fields}
        self.assertEqual(raw["売上"], 9000)
        # 9000 - 900 - 210 - 5300 = 2590
        self.assertEqual(raw["実利益"], 2590)
        self.assertEqual(raw["販売件数"], 1)
        text = render(payload, "text")
        self.assertIn("カテゴリー別実績", text)
        self.assertIn("トレカ", text)


class ImporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_import_with_existing_item(self) -> None:
        item_id = self.db.upsert_item({"name": "テスト", "min_price": 9000, "status": "purchased"})
        result = import_listing_json(self.db, json.dumps({
            "type": "mercari_listing_draft",
            "item_id": item_id,
            "title": "タイトル",
            "description": "説明",
            "price": 8500,
        }))
        listing = self.db.get_listing(result["listing_id"])
        self.assertEqual(listing["status"], "draft")
        self.assertEqual(listing["list_price"], 8500)
        # 最低販売価格を下回る警告が出る
        self.assertTrue(any("最低販売価格" in w for w in result["warnings"]))

    def test_import_creates_item(self) -> None:
        result = import_listing_json(self.db, {
            "name": "新規商品", "title": "タイトル", "description": "説明", "price": 5000,
        })
        self.assertIsNotNone(self.db.get_item(result["item_id"]))
        self.assertTrue(result["warnings"])

    def test_import_rejects_missing_fields(self) -> None:
        with self.assertRaises(ValueError):
            import_listing_json(self.db, {"title": "タイトルのみ"})
        with self.assertRaises(ValueError):
            import_listing_json(self.db, "これはJSONではない")


if __name__ == "__main__":
    unittest.main()
