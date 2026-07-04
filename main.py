from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from database import PriceDatabase
from decision import alert_reasons, judge
from notifier import (
    notify_alert,
    notify_daily_summary,
    notify_scrape_failure,
    webhook_from_config,
)
from scraper import ScrapedOffer, scrape_site


BASE_DIR = Path(__file__).resolve().parent


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def offer_to_record(item: dict[str, Any], offer: ScrapedOffer) -> dict[str, Any]:
    return {
        "item_name": item["name"],
        "shop_name": offer.shop_name,
        "color_key": offer.color_key,
        "color_label": offer.color_label,
        "capacity": offer.capacity,
        "state": offer.state,
        "price": offer.price,
        "source_updated_at": offer.source_updated_at,
        "url": offer.url,
        "raw_text": offer.raw_text,
    }


def run_monitor(config_path: Path | None = None) -> int:
    config = load_config(config_path or BASE_DIR / "config.yaml")
    db_path = Path(config.get("database", {}).get("path", "prices.sqlite3"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db = PriceDatabase(db_path)
    webhook_url = webhook_from_config(config)
    thresholds = config.get("thresholds", {})
    scraping = config.get("scraping", {})

    saved_records: list[dict[str, Any]] = []
    for item in config.get("items", []):
        for site in config.get("sites", []):
            if site.get("enabled") is False:
                continue
            # モデル別ページの振り分け（無印Pro と Pro Max でURLが異なるため）
            if site.get("models") and item.get("model") not in site.get("models", []):
                continue
            if site.get("capacities") and item.get("capacity") not in site.get("capacities", []):
                continue
            try:
                offers = scrape_site(site, item, scraping)
                if not offers:
                    raise RuntimeError("対象商品の価格候補が見つかりませんでした")
            except Exception as exc:
                db.insert_error(site["name"], site["url"], str(exc))
                if config.get("discord", {}).get("notify_on_scrape_failure", True):
                    notify_scrape_failure(webhook_url, site["name"], site["url"], str(exc))
                continue

            for offer in offers:
                saved = db.insert_price(offer_to_record(item, offer))
                saved_records.append(saved)

        send_alerts_for_item(db, webhook_url, item, thresholds, saved_records)

    # デイリーまとめ：FORCE_NOTIFY=1 のときは、価格変化の有無に関わらず
    # 監視中の全アイテムの現在価格・原価差額・判断を必ず通知する。
    if os.getenv("FORCE_NOTIFY"):
        summaries = build_daily_summary(db, config.get("items", []), thresholds)
        notify_daily_summary(webhook_url, summaries, str(date.today()))

    return len(saved_records)


def build_daily_summary(
    db: PriceDatabase,
    items: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    """各アイテムの最新（最高）買取価格・原価差額・判断をまとめる。"""
    summaries: list[dict[str, Any]] = []
    for item in items:
        cost_price = int(item["cost_price"])
        best_row: dict[str, Any] | None = None
        best_color: str | None = None
        for color in item.get("colors", []):
            rows = [dict(row) for row in db.latest_by_color(item["name"], color["key"])]
            if not rows:
                continue
            row = max(rows, key=lambda r: int(r["price"]))
            if best_row is None or int(row["price"]) > int(best_row["price"]):
                best_row = row
                best_color = color.get("label")
        if best_row is None:
            summaries.append({"name": item["name"], "cost_price": cost_price, "best_price": None})
            continue
        price = int(best_row["price"])
        summaries.append(
            {
                "name": item["name"],
                "cost_price": cost_price,
                "best_price": price,
                "best_color": best_color,
                "best_shop": best_row.get("shop_name"),
                "profit": price - cost_price,
                "decision": judge(price, cost_price, thresholds).label,
            }
        )
    return summaries


def main() -> None:
    saved_count = run_monitor(BASE_DIR / "config.yaml")
    print(f"saved {saved_count} price observations")


def send_alerts_for_item(
    db: PriceDatabase,
    webhook_url: str | None,
    item: dict[str, Any],
    thresholds: dict[str, Any],
    saved_records: list[dict[str, Any]],
) -> None:
    cost_price = int(item["cost_price"])
    repeat_hours = int(thresholds.get("alert_repeat_hours", 24))
    sent_keys: set[tuple[str, str]] = set()
    for color in item.get("colors", []):
        latest_rows = [dict(row) for row in db.latest_by_color(item["name"], color["key"])]
        if not latest_rows:
            continue
        best_record = max(latest_rows, key=lambda row: int(row["price"]))
        color_saved = [
            record
            for record in saved_records
            if record["item_name"] == item["name"] and record["color_key"] == color["key"]
        ]
        for record in color_saved:
            # 通知過多対策：前回から価格が変化した時だけ通知する。
            # diff は「今回価格 - 前回価格」。None(初回で比較対象なし)や 0(変化なし)は黙って抑制。
            diff = record.get("diff")
            if diff is None or diff == 0:
                continue
            reasons = alert_reasons(record, best_record, latest_rows, cost_price, thresholds)
            # しきい値系の理由が無くても、価格が動いた事実は必ず知らせる（1変化=1通知）。
            if not reasons:
                reasons = [f"前回比 {diff:+,}円 変動"]
            reason_key = "|".join(sorted(set(reasons)))
            key = (record["color_key"], reason_key)
            alert_key = "|".join(
                [
                    item["name"],
                    record["color_key"],
                    reason_key,
                    best_record["shop_name"],
                    str(best_record["price"]),
                ]
            )
            if reasons and key not in sent_keys and db.should_send_alert(alert_key, repeat_hours):
                notify_alert(webhook_url, item, record, best_record, reasons, thresholds)
                sent_keys.add(key)


if __name__ == "__main__":
    main()
