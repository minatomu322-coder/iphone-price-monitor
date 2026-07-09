"""CLI エントリポイント。

使い方:
    python -m rakuten_finder.cli run       # 巡回 1 回実行（検索→評価→保存→通知）
    python -m rakuten_finder.cli report    # 日次レポートを Discord に送信
    python -m rakuten_finder.cli decide <item_code> <buy|skip|hold> [メモ]
                                           # 判断を記録（学習に反映）
"""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .database import FinderDatabase
from .pipeline import run_daily_report, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rakuten_finder", description="利益商品AI CLI")
    parser.add_argument("--config", default=None, help="設定 YAML のパス")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="巡回を1回実行")
    run_p.add_argument("--no-notify", action="store_true", help="Discord通知を行わない")

    sub.add_parser("report", help="日次レポートを送信")

    decide_p = sub.add_parser("decide", help="買う/見送り/保留 を記録")
    decide_p.add_argument("item_code")
    decide_p.add_argument("decision", choices=["buy", "skip", "hold"])
    decide_p.add_argument("note", nargs="?", default="")

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "run":
        result = run_pipeline(config, notify=not args.no_notify)
        print(
            f"取得 {result.searched}件 / 相場照合 {result.matched}件 / "
            f"保存 {result.saved}件 / 通知 {result.notified}件"
        )
        for error in result.errors:
            print(f"[error] {error}", file=sys.stderr)
        return 1 if result.errors and result.saved == 0 else 0

    if args.command == "report":
        run_daily_report(config)
        print("日次レポートを送信しました")
        return 0

    if args.command == "decide":
        db = FinderDatabase(config.db_path)
        db.record_decision(args.item_code, args.decision, args.note)
        print(f"記録しました: {args.item_code} -> {args.decision}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
