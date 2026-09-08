from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .catalog import load_products
from .database import TcgDatabase, today_jst
from .metrics import compute_metrics
from .models import Candidate, Product
from .profit import compute_profit
from .report import build_summary, candidate_to_row, send_discord, webhook_from_config, write_csv
from .sources import (
    ManualSheetSource,
    RakutenSource,
    SurugayaSource,
    YahooShoppingSource,
    build_session,
    make_observed_on,
)
from .strategy import evaluate, select_daily


PACKAGE_DIR = Path(__file__).resolve().parent
BASE_DIR = PACKAGE_DIR.parent


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else BASE_DIR / path


def build_sources(config: dict[str, Any]) -> list[Any]:
    """有効な自動取得ソースを組み立てる（手動シートは別扱い）。"""
    scraping = config.get("scraping", {})
    sources_cfg = config.get("sources", {})
    session = build_session(scraping)
    sources: list[Any] = []

    rakuten_cfg = sources_cfg.get("rakuten", {})
    if rakuten_cfg.get("enabled", False):
        app_id = os.getenv(rakuten_cfg.get("app_id_env", "RAKUTEN_APP_ID"))
        if app_id:
            sources.append(RakutenSource(app_id, session, rakuten_cfg, scraping))
        else:
            print(f"[warn] 楽天API: 環境変数 {rakuten_cfg.get('app_id_env', 'RAKUTEN_APP_ID')} 未設定のためスキップ")

    yahoo_cfg = sources_cfg.get("yahoo_shopping", {})
    if yahoo_cfg.get("enabled", False):
        app_id = os.getenv(yahoo_cfg.get("app_id_env", "YAHOO_APP_ID"))
        if app_id:
            sources.append(YahooShoppingSource(app_id, session, yahoo_cfg, scraping))
        else:
            print(f"[warn] Yahoo!ショッピングAPI: 環境変数 {yahoo_cfg.get('app_id_env', 'YAHOO_APP_ID')} 未設定のためスキップ")

    surugaya_cfg = sources_cfg.get("surugaya", {})
    if surugaya_cfg.get("enabled", False):
        sources.append(SurugayaSource(session, surugaya_cfg, scraping))
    return sources


def collect_prices(db: TcgDatabase, products: list[Product], config: dict[str, Any], as_of: date, fetch: bool) -> int:
    """全ソースから価格を集めてDBへ。同日・同ソースの再実行は上書き。"""
    saved = 0
    sources = build_sources(config) if fetch else []
    for product in products:
        for source in sources:
            try:
                observations = source.fetch(product)
            except Exception as exc:  # 1件の失敗で全体を止めない
                db.insert_error(source.name, product.product_id, None, str(exc))
                print(f"[error] {source.name} {product.product_id}: {exc}")
                continue
            db.delete_observations(product.product_id, source.name, as_of)
            saved += db.insert_observations(observations, as_of)

    # フリマ相場シート（人が更新）。updated_on の日付で保存し、同日分は上書き。
    sheet = ManualSheetSource(resolve(config.get("flea_market", {}).get("price_sheet", "data/flea_market_prices.csv")))
    for product in products:
        observations = sheet.fetch(product)
        by_day: dict[date, list[Any]] = {}
        for obs in observations:
            day = make_observed_on(obs.observed_at) or as_of
            by_day.setdefault(day, []).append(obs)
        for day, group in by_day.items():
            db.delete_observations(product.product_id, sheet.name, day)
            saved += db.insert_observations(group, day)
    return saved


def build_candidates(db: TcgDatabase, products: list[Product], config: dict[str, Any], as_of: date) -> list[Candidate]:
    candidates: list[Candidate] = []
    metric_rows: list[dict[str, Any]] = []
    for product in products:
        metrics = compute_metrics(db, product, as_of, config)
        metric_rows.append(metrics.to_row())
        if metrics.buy_min is None:
            continue
        profit = compute_profit(product, metrics.buy_min, metrics.sell_prices, config)
        if profit is None:
            continue
        candidates.extend(evaluate(product, metrics, profit, config, as_of))
    db.upsert_metrics(metric_rows)
    return candidates


def run(config_path: Path, as_of: date | None = None, fetch: bool = True, dry_run: bool = False) -> dict[str, list[Candidate]]:
    config = load_config(config_path)
    as_of = as_of or today_jst()
    products = load_products(resolve(config.get("catalog", {}).get("products", "data/products.csv")))
    db = TcgDatabase(resolve(config.get("database", {}).get("path", "tcg_prices.sqlite3")))
    output = config.get("output", {})

    saved = collect_prices(db, products, config, as_of, fetch)
    print(f"[info] 商品 {len(products)}件 / 価格観測 {saved}件 保存 ({as_of})")

    candidates = build_candidates(db, products, config, as_of)
    excluded = db.recently_submitted(as_of, int(output.get("cooldown_days", 14)))
    selected = select_daily(candidates, config, excluded)

    rows = [candidate_to_row(c, as_of) for strategy in selected for c in selected[strategy]]
    csv_path = write_csv(resolve(output.get("csv_dir", "reports")) / f"{as_of.isoformat()}.csv", rows)
    display_path = csv_path.relative_to(BASE_DIR) if csv_path.is_relative_to(BASE_DIR) else csv_path
    summary = build_summary(selected, as_of, display_path, output.get("per_strategy", {}))

    if dry_run:
        print("[dry-run] 提出履歴の記録・Discord通知はスキップ")
        print(summary)
        return selected

    db.record_submissions(
        [
            {
                "submitted_on": as_of.isoformat(),
                "product_id": c.product.product_id,
                "strategy": c.strategy,
                "buy_price": c.profit.buy_price,
                "channel": c.profit.best.channel,
                "expect_sell": c.profit.best.gross,
                "expect_profit": c.profit.best.profit,
                "score": c.score,
            }
            for strategy in selected
            for c in selected[strategy]
        ]
    )
    if output.get("discord_summary", True):
        send_discord(webhook_from_config(config), summary)
    else:
        print(summary)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="TCG 利益商品 日次抽出")
    parser.add_argument("--config", default=str(PACKAGE_DIR / "config.yaml"))
    parser.add_argument("--date", help="集計日 YYYY-MM-DD（省略時はJSTの今日）")
    parser.add_argument("--no-fetch", action="store_true", help="外部取得せずDBの既存データだけで判定")
    parser.add_argument("--dry-run", action="store_true", help="提出履歴を記録せず、通知も出さない")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.date) if args.date else None
    run(Path(args.config), as_of=as_of, fetch=not args.no_fetch, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
