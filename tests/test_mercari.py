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


class Stage1Test(unittest.TestCase):
    """ChatGPT判断履歴・回転日数・在庫資金・売れない理由（優先順位1）"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")
        self.item_id = self.db.upsert_item({
            "name": "テスト商品", "purchase_price": 5000, "purchase_shipping": 300,
            "purchased_at": "2026-06-01", "status": "purchased",
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_gpt_review_roundtrip(self) -> None:
        self.db.add_gpt_review({
            "item_id": self.item_id, "kind": "sourcing",
            "verdict": "条件付きで買い", "summary": "付属品完備なら買い",
            "raw_text": "全文...",
        })
        latest = self.db.latest_gpt_verdict(self.item_id)
        self.assertEqual(latest["verdict"], "条件付きで買い")
        with self.assertRaises(ValueError):
            self.db.add_gpt_review({"item_id": self.item_id, "kind": "不正な種類"})

    def test_gpt_history_in_sourcing_export(self) -> None:
        self.db.add_gpt_review({
            "item_id": self.item_id, "kind": "sourcing", "verdict": "買い", "summary": "相場安定",
        })
        self.db.insert_market_snapshot({
            "item_id": self.item_id, "median_price": 9000, "sold_count": 10,
        })
        text = render(sourcing_payload(self.db, self.item_id, CONFIG), "text")
        self.assertIn("過去のChatGPT判断", text)
        self.assertIn("買い 相場安定", text)

    def test_days_to_sell_recorded(self) -> None:
        self.db.record_sale({
            "item_id": self.item_id, "sold_price": 8000, "sales_fee": 800,
            "sold_at": "2026-06-21",
        })
        sales = self.db.sales_between("2026-06-01", "2026-06-30")
        self.assertEqual(sales[0]["days_to_sell"], 20)

    def test_unsold_reasons_and_stats(self) -> None:
        self.db.add_unsold_reason({
            "item_id": self.item_id, "reason_tag": "価格が高い",
            "detail": "相場より1000円高い", "source": "chatgpt",
            "recorded_at": "2026-07-01",
        })
        self.db.add_unsold_reason({
            "item_id": self.item_id, "reason_tag": "価格が高い", "recorded_at": "2026-07-02",
        })
        self.db.add_unsold_reason({
            "item_id": self.item_id, "reason_tag": "写真が悪い", "recorded_at": "2026-07-03",
        })
        stats = self.db.unsold_reason_stats("2026-07-01", "2026-07-31")
        self.assertEqual(stats[0]["reason_tag"], "価格が高い")
        self.assertEqual(stats[0]["count"], 2)

    def test_inventory_aging(self) -> None:
        from mercari.kpi import inventory_aging, stock_summary

        self.db.upsert_item({
            "name": "新しい在庫", "purchase_price": 2000, "purchased_at": "2026-07-20",
            "status": "purchased",
        })
        summary = stock_summary(self.db, today="2026-07-23")
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["capital"], 5300 + 2000)
        buckets = {b["label"]: b for b in summary["aging"]}
        self.assertEqual(buckets["0〜30日"]["count"], 1)      # 7/20仕入れ→3日
        self.assertEqual(buckets["31〜60日"]["count"], 1)     # 6/1仕入れ→52日
        # 仕入れ日未入力は「日数不明」へ
        self.db.upsert_item({"name": "日付なし", "purchase_price": 1000, "status": "purchased"})
        aging = inventory_aging(
            [i for i in self.db.list_items() if i["status"] == "purchased"], today="2026-07-23"
        )
        self.assertTrue(any(b["label"] == "日数不明" and b["count"] == 1 for b in aging))

    def test_sales_export_includes_aging_and_reasons(self) -> None:
        self.db.add_unsold_reason({
            "item_id": self.item_id, "reason_tag": "供給過多", "recorded_at": "2026-07-05",
        })
        self.db.record_sale({
            "item_id": self.item_id, "sold_price": 8000, "sales_fee": 800, "sold_at": "2026-07-10",
        })
        self.db.upsert_item({
            "name": "在庫商品", "purchase_price": 3000, "purchased_at": "2026-05-01",
            "status": "listed",
        })
        payload = sales_payload(self.db, "2026-07-01", "2026-07-31", CONFIG)
        text = render(payload, "text")
        self.assertIn("在庫年齢別の寝ている資金", text)
        self.assertIn("売れなかった理由の集計", text)
        self.assertIn("供給過多", text)


class Stage2Test(unittest.TestCase):
    """改善提案ライフサイクル・月次KPI（優先順位2）"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_improvement_lifecycle(self) -> None:
        item_id = self.db.upsert_item({"name": "テスト", "status": "listed"})
        imp_id = self.db.add_improvement({
            "item_id": item_id, "kind": "タイトル変更",
            "detail": "型番をタイトルへ", "status": "proposed", "source": "chatgpt",
        })
        imps = self.db.improvements_for_item(item_id)
        self.assertEqual(imps[0]["status"], "proposed")
        self.assertEqual(imps[0]["source"], "chatgpt")
        self.db.update_improvement_status(imp_id, "applied", "閲覧数が増えた")
        imps = self.db.improvements_for_item(item_id)
        self.assertEqual(imps[0]["status"], "applied")
        self.assertEqual(imps[0]["result"], "閲覧数が増えた")
        with self.assertRaises(ValueError):
            self.db.add_improvement({"item_id": item_id, "kind": "x", "status": "bad"})

    def test_monthly_kpis(self) -> None:
        from mercari.kpi import kpi_dashboard, monthly_kpis

        for month, price in (("2026-05", 8000), ("2026-06", 9000), ("2026-07", 7000)):
            item_id = self.db.upsert_item({
                "name": f"商品{month}", "purchase_price": 5000,
                "purchased_at": f"{month}-01", "status": "purchased",
            })
            self.db.record_sale({
                "item_id": item_id, "sold_price": price,
                "sales_fee": price // 10, "sold_at": f"{month}-15",
            })
        series = monthly_kpis(self.db, months=3, today="2026-07-23")
        self.assertEqual([m["month"] for m in series], ["2026-05", "2026-06", "2026-07"])
        june = series[1]
        self.assertEqual(june["count"], 1)
        self.assertEqual(june["revenue"], 9000)
        self.assertEqual(june["profit"], 9000 - 900 - 5000)
        self.assertEqual(june["avg_days_to_sell"], 14.0)
        # 12月をまたぐ月範囲の計算も確認
        jan = monthly_kpis(self.db, months=1, today="2026-01-10")
        self.assertEqual(jan[0]["month"], "2026-01")
        dashboard = kpi_dashboard(self.db, months=3, today="2026-07-23")
        self.assertIn("stock", dashboard)
        self.assertEqual(len(dashboard["months"]), 3)

    def test_stale_export_shows_improvement_status(self) -> None:
        item_id = self.db.upsert_item({
            "name": "テスト", "purchase_price": 5000, "status": "listed",
        })
        self.db.upsert_listing({
            "item_id": item_id, "status": "active", "list_price": 9000,
            "listed_at": "2026-06-01",
        })
        self.db.add_improvement({
            "item_id": item_id, "kind": "値下げ", "detail": "500円下げ",
            "status": "proposed", "source": "chatgpt",
        })
        text = render(build_payload(self.db, "stale", CONFIG, item_id=item_id), "text")
        self.assertIn("値下げ[提案のみ]", text)


class Stage3Test(unittest.TestCase):
    """商品スコア・リピート判定・特徴分析（優先順位3）"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sell(self, name, model, cost, price, purchased, sold, category="トレカ", source="ヤフオク"):
        item_id = self.db.upsert_item({
            "name": name, "model_number": model, "category": category,
            "purchase_price": cost, "purchased_at": purchased,
            "purchase_source": source, "status": "purchased",
        })
        self.db.record_sale({
            "item_id": item_id, "sold_price": price,
            "sales_fee": price // 10, "sold_at": sold,
        })
        return item_id

    def test_score_sale_grades(self) -> None:
        from mercari.scoring import score_sale

        # 高ROI・高利益・1週間以内 → S
        best = score_sale(profit=6000, roi=0.50, days_to_sell=5)
        self.assertEqual(best["grade"], "S")
        self.assertEqual(best["points"], 100)
        # 赤字 → D
        worst = score_sale(profit=-500, roi=-0.10, days_to_sell=90)
        self.assertEqual(worst["grade"], "D")
        # 回転日数不明は中立点
        neutral = score_sale(profit=2000, roi=0.20, days_to_sell=None)
        self.assertEqual(neutral["breakdown"]["days"], 10)

    def test_repeat_candidates(self) -> None:
        from mercari.scoring import repeat_candidates

        # 同じ型番を2回、良い成績で売却 → リピート推奨
        self._sell("リザードンex", "sv4a-205", 5000, 9000, "2026-06-01", "2026-06-10")
        self._sell("リザードンex SAR", "SV4A-205", 5200, 9500, "2026-06-15", "2026-06-25")
        # 赤字商品 → 推奨しない
        self._sell("赤字商品", None, 8000, 7000, "2026-06-01", "2026-07-20")
        results = repeat_candidates(self.db, CONFIG)
        by_key = {r["key"]: r for r in results}
        charizard = by_key["model:sv4a-205"]  # 型番の大文字小文字は同一視
        self.assertEqual(charizard["sold_count"], 2)
        self.assertTrue(charizard["recommend_repeat"])
        loser = [r for r in results if r["name"] == "赤字商品"][0]
        self.assertFalse(loser["recommend_repeat"])
        # 推奨が先頭に来る
        self.assertTrue(results[0]["recommend_repeat"])

    def test_insights_export(self) -> None:
        self._sell("商品A", "m-1", 5000, 9000, "2026-06-01", "2026-06-10", category="トレカ")
        self._sell("商品B", "m-2", 20000, 19000, "2026-06-01", "2026-07-10", category="ゲーム")
        payload = build_payload(self.db, "insights", CONFIG)
        text = render(payload, "text")
        self.assertIn("カテゴリー別", text)
        self.assertIn("仕入れ価格帯別", text)
        self.assertIn("スコア分布", text)
        self.assertIn("リピート", text)
        self.assertIn("トレカ", text)
        data = json.loads(render(payload, "json"))
        self.assertEqual(data["種別"], "insights")
        self.assertEqual(data["項目"]["総売却件数"], 2)
        self.assertEqual(data["項目"]["赤字件数"], 1)


class Stage5Test(unittest.TestCase):
    """判断の答え合わせループ（①自動評価・⑥自信度）"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_confidence_validation(self) -> None:
        item_id = self.db.upsert_item({"name": "テスト"})
        review_id = self.db.add_gpt_review({
            "item_id": item_id, "kind": "sourcing", "verdict": "買い", "confidence": 85,
        })
        self.assertEqual(self.db.latest_gpt_verdict(item_id)["confidence"], 85)
        with self.assertRaises(ValueError):
            self.db.add_gpt_review({"item_id": item_id, "kind": "sourcing", "confidence": 150})
        # 手動の答え合わせ
        self.db.record_review_outcome(review_id, "利益+2,000円で売却", "correct")
        review = self.db.latest_gpt_verdict(item_id)
        self.assertEqual(review["accuracy"], "correct")
        self.assertEqual(review["outcome"], "利益+2,000円で売却")

    def test_auto_evaluate_buy_correct(self) -> None:
        from mercari.judgment import auto_evaluate

        item_id = self.db.upsert_item({
            "name": "買い正解", "purchase_price": 5000, "purchased_at": "2026-06-01",
            "status": "purchased",
        })
        self.db.add_gpt_review({
            "item_id": item_id, "kind": "sourcing", "verdict": "買い", "confidence": 80,
        })
        self.db.record_sale({
            "item_id": item_id, "sold_price": 9000, "sales_fee": 900, "sold_at": "2026-06-15",
        })
        self.assertEqual(auto_evaluate(self.db, CONFIG), 1)
        review = self.db.latest_gpt_verdict(item_id)
        self.assertEqual(review["accuracy"], "correct")
        self.assertIn("利益+3,100円", review["outcome"])
        # 再実行しても二重評価しない
        self.assertEqual(auto_evaluate(self.db, CONFIG), 0)

    def test_auto_evaluate_buy_incorrect_on_loss(self) -> None:
        from mercari.judgment import auto_evaluate

        item_id = self.db.upsert_item({
            "name": "買い失敗", "purchase_price": 9000, "status": "purchased",
        })
        self.db.add_gpt_review({
            "item_id": item_id, "kind": "sourcing", "verdict": "条件付きで買い",
        })
        self.db.record_sale({
            "item_id": item_id, "sold_price": 8000, "sales_fee": 800, "sold_at": "2026-07-01",
        })
        auto_evaluate(self.db, CONFIG)
        self.assertEqual(self.db.latest_gpt_verdict(item_id)["accuracy"], "incorrect")

    def test_auto_evaluate_skip_by_market_move(self) -> None:
        from mercari.judgment import auto_evaluate

        item_id = self.db.upsert_item({"name": "見送り検証", "purchase_price": 5000})
        self.db.insert_market_snapshot({
            "item_id": item_id, "median_price": 10000, "captured_at": "2026-06-01T10:00:00+09:00",
        })
        self.db.add_gpt_review({
            "item_id": item_id, "kind": "sourcing", "verdict": "見送り",
            "created_at": "2026-06-02T10:00:00+09:00",
        })
        # まだ新しい相場がない → 評価されない
        self.assertEqual(auto_evaluate(self.db, CONFIG), 0)
        # 相場が20%上昇 → 見送りは誤り（機会損失）
        self.db.insert_market_snapshot({
            "item_id": item_id, "median_price": 12000, "captured_at": "2026-06-20T10:00:00+09:00",
        })
        self.assertEqual(auto_evaluate(self.db, CONFIG), 1)
        review = self.db.latest_gpt_verdict(item_id)
        self.assertEqual(review["accuracy"], "incorrect")
        self.assertIn("+20%上昇", review["outcome"])

    def test_history_and_stats_in_sourcing_export(self) -> None:
        item_id = self.db.upsert_item({
            "name": "成績確認", "purchase_price": 5000, "purchased_at": "2026-06-01",
            "status": "purchased",
        })
        self.db.insert_market_snapshot({
            "item_id": item_id, "median_price": 9000, "sold_count": 10,
        })
        self.db.add_gpt_review({
            "item_id": item_id, "kind": "sourcing", "verdict": "買い", "confidence": 90,
        })
        self.db.record_sale({
            "item_id": item_id, "sold_price": 9000, "sales_fee": 900, "sold_at": "2026-06-10",
        })
        text = render(sourcing_payload(self.db, item_id, CONFIG), "text")
        self.assertIn("自信度90%", text)
        self.assertIn("→評価: 判断は正しい", text)
        self.assertIn("ChatGPT判断の成績", text)
        self.assertIn("自信度70%以上の判断は1件中1件が正解", text)


class Stage6Test(unittest.TestCase):
    """資金拘束ペナルティ（②）と失敗コストの見える化（⑤）"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_capital_penalty(self) -> None:
        from mercari.scoring import capital_penalty, score_sale

        self.assertEqual(capital_penalty(5000, 10), 0)
        self.assertEqual(capital_penalty(5000, 61), 8)
        self.assertEqual(capital_penalty(5000, 90), 15)
        self.assertEqual(capital_penalty(5000, 180), 25)
        # 高額仕入れ×31日超は追加減点
        self.assertEqual(capital_penalty(50000, 40), 10)
        self.assertEqual(capital_penalty(50000, 90), 25)
        # 利益5,000円でも半年売れなかったら高評価にしない
        slow = score_sale(profit=5000, roi=0.30, days_to_sell=180, cost=15000)
        fast = score_sale(profit=5000, roi=0.30, days_to_sell=7, cost=15000)
        self.assertEqual(fast["grade"], "S")
        self.assertEqual(slow["breakdown"]["capital_penalty"], -25)
        self.assertLess(slow["points"], fast["points"] - 40)
        self.assertIn(slow["grade"], ("C", "D"))

    def test_failure_costs(self) -> None:
        from mercari.kpi import failure_costs

        # 黒字売却（出品9,500円→9,000円に値下げして売却）
        win_id = self.db.upsert_item({
            "name": "黒字", "purchase_price": 5000, "purchased_at": "2026-06-01",
            "status": "listed",
        })
        listing_id = self.db.upsert_listing({
            "item_id": win_id, "status": "active", "list_price": 9500, "listed_at": "2026-06-05",
        })
        self.db.record_sale({
            "item_id": win_id, "listing_id": listing_id, "sold_price": 9000,
            "sales_fee": 900, "sold_at": "2026-06-20",
        })
        # 赤字売却
        lose_id = self.db.upsert_item({
            "name": "赤字", "purchase_price": 9000, "status": "purchased",
        })
        self.db.record_sale({
            "item_id": lose_id, "sold_price": 8000, "sales_fee": 800, "sold_at": "2026-07-01",
        })
        # 見送り誤りの判断
        skip_id = self.db.upsert_item({"name": "見送り", "purchase_price": 3000})
        review_id = self.db.add_gpt_review({
            "item_id": skip_id, "kind": "sourcing", "verdict": "見送り",
        })
        self.db.record_review_outcome(review_id, "相場+20%上昇", "incorrect")

        fc = failure_costs(self.db)
        self.assertEqual(fc["sales_count"], 2)
        self.assertEqual(fc["gross_profit"], 9000 - 900 - 5000)   # +3100
        self.assertEqual(fc["gross_loss"], 8000 - 800 - 9000)     # -1800
        self.assertEqual(fc["net_profit"], 3100 - 1800)
        self.assertEqual(fc["markdown_loss"], 500)                # 9500→9000
        self.assertEqual(fc["skip_error_count"], 1)
        self.assertEqual(fc["failure_rate"], 0.5)

    def test_sales_export_shows_failure_costs(self) -> None:
        item_id = self.db.upsert_item({
            "name": "赤字商品", "purchase_price": 9000, "status": "purchased",
        })
        self.db.record_sale({
            "item_id": item_id, "sold_price": 8000, "sales_fee": 800, "sold_at": "2026-07-01",
        })
        text = render(sales_payload(self.db, "2026-07-01", "2026-07-31", CONFIG), "text")
        self.assertIn("累計赤字", text)
        self.assertIn("失敗率", text)
        self.assertIn("100.0%", text)  # 1件中1件赤字


class Stage7Test(unittest.TestCase):
    """利益理由ランキング（③）と改善案必須の依頼文（④）"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = MercariDatabase(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_profit_reason_ranking(self) -> None:
        from mercari.scoring import profit_reason_ranking

        for i, (price, tag) in enumerate((
            (9000, "発売直後・新作"), (12000, "発売直後・新作"), (7000, "仕入れ先が良い"),
        )):
            item_id = self.db.upsert_item({
                "name": f"商品{i}", "purchase_price": 5000,
                "purchased_at": "2026-06-01", "status": "purchased",
            })
            self.db.record_sale({
                "item_id": item_id, "sold_price": price,
                "sales_fee": price // 10, "sold_at": "2026-06-10",
            })
            self.db.add_profit_reason({
                "item_id": item_id, "reason_tag": tag, "source": "chatgpt",
            })
        ranking = profit_reason_ranking(self.db)
        self.assertEqual(ranking[0]["reason_tag"], "発売直後・新作")
        self.assertEqual(ranking[0]["count"], 2)
        # (9000-900-5000) + (12000-1200-5000) = 3100 + 5800
        self.assertEqual(ranking[0]["total_profit"], 8900)
        self.assertEqual(ranking[0]["avg_days"], 9.0)
        self.assertEqual(ranking[1]["reason_tag"], "仕入れ先が良い")

    def test_ranking_in_insights_and_sales_exports(self) -> None:
        item_id = self.db.upsert_item({
            "name": "勝ち商品", "purchase_price": 5000,
            "purchased_at": "2026-06-01", "status": "purchased",
        })
        self.db.record_sale({
            "item_id": item_id, "sold_price": 9000, "sales_fee": 900, "sold_at": "2026-07-05",
        })
        self.db.add_profit_reason({"item_id": item_id, "reason_tag": "人気シリーズ"})
        insights_text = render(build_payload(self.db, "insights", CONFIG), "text")
        self.assertIn("利益になった理由ランキング", insights_text)
        self.assertIn("人気シリーズ", insights_text)
        sales_text = render(sales_payload(self.db, "2026-07-01", "2026-07-31", CONFIG), "text")
        self.assertIn("利益になった理由ランキング", sales_text)

    def test_preambles_demand_three_proposals(self) -> None:
        from mercari.exports import PREAMBLES

        for kind in ("stale", "sales", "insights"):
            self.assertIn("必ず3つ", PREAMBLES[kind])
            self.assertIn("期待利益", PREAMBLES[kind])
            self.assertIn("期待ROI", PREAMBLES[kind])
            self.assertIn("期待回転", PREAMBLES[kind])


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

    def test_import_item_with_market_returns_judgement(self) -> None:
        from mercari.importer import import_item_json

        result = import_item_json(self.db, json.dumps({
            "type": "mercari_item_info",
            "name": "クイック登録商品",
            "model_number": "q-1",
            "purchase_price": 5000,
            "purchase_url": "https://example.com/item",
            "market": {
                "min_price": 8000, "median_price": 9000,
                "sold_count": 12, "active_count": 8, "url": "https://example.com/search",
            },
        }), CONFIG)
        item = self.db.get_item(result["item_id"])
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["purchase_price"], 5000)
        market = self.db.latest_market(result["item_id"])
        self.assertEqual(market["median_price"], 9000)
        self.assertEqual(market["source"], "ChatGPT調査")
        # 相場込みなので一次判定まで自動で出る
        self.assertEqual(result["judgement"]["label"], "買い候補")

    def test_import_item_without_market(self) -> None:
        from mercari.importer import import_item_json

        result = import_item_json(self.db, {
            "type": "mercari_item_info", "name": "相場なし商品", "purchase_price": 3000,
        }, CONFIG)
        self.assertEqual(result["judgement"]["label"], "追加確認")
        with self.assertRaises(ValueError):
            import_item_json(self.db, {"type": "mercari_item_info"}, CONFIG)

    def test_import_item_updates_existing(self) -> None:
        from mercari.importer import import_item_json

        item_id = self.db.upsert_item({"name": "既存商品", "status": "candidate"})
        result = import_item_json(self.db, {
            "type": "mercari_item_info", "item_id": item_id,
            "model_number": "upd-1", "purchase_price": 4000,
        }, CONFIG)
        self.assertEqual(result["item_id"], item_id)
        item = self.db.get_item(item_id)
        self.assertEqual(item["model_number"], "upd-1")
        self.assertEqual(item["purchase_price"], 4000)


if __name__ == "__main__":
    unittest.main()
