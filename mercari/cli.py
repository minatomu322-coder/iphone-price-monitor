from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from mercari.db import MercariDatabase
from mercari.exports import FORMATS, build_payload, render
from mercari.importer import import_listing_json
from mercari.notify import notify_sourcing_candidates


BASE_DIR = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    with (path or BASE_DIR / "mercari_config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def open_db(config: dict[str, Any]) -> MercariDatabase:
    db_path = Path(config.get("database", {}).get("path", "mercari.sqlite3"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return MercariDatabase(db_path)


def cmd_serve(args: argparse.Namespace) -> None:
    from mercari.webapp import main as serve_main

    serve_main(port=args.port)


def cmd_export(args: argparse.Namespace) -> None:
    config = load_config()
    db = open_db(config)
    payload = build_payload(
        db,
        args.kind,
        config,
        item_id=args.item,
        date_from=getattr(args, "date_from", None),
        date_to=getattr(args, "date_to", None),
    )
    print(render(payload, args.format))


def cmd_import_listing(args: argparse.Namespace) -> None:
    config = load_config()
    db = open_db(config)
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    result = import_listing_json(db, raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_notify(args: argparse.Namespace) -> None:
    config = load_config()
    db = open_db(config)
    count = notify_sourcing_candidates(db, config)
    print(f"{count}件の仕入れ候補を通知しました")


def cmd_backup(args: argparse.Namespace) -> None:
    config = load_config()
    db = open_db(config)
    dest = db.backup(BASE_DIR / "backups")
    print(f"バックアップを作成しました: {dest}")


def cmd_dump_csv(args: argparse.Namespace) -> None:
    config = load_config()
    db = open_db(config)
    allowed = ("items", "listings", "sales", "market_snapshots", "improvements", "price_changes")
    if args.table not in allowed:
        raise SystemExit(f"テーブルは {', '.join(allowed)} のいずれかを指定してください")
    with db.connect() as conn:
        rows = conn.execute(f"SELECT * FROM {args.table}").fetchall()  # noqa: S608 - 許可リスト検証済み
    out = Path(args.out) if args.out else Path(f"{args.table}.csv")
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows([tuple(row) for row in rows])
    print(f"{len(rows)}行を書き出しました: {out}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mercari",
        description="メルカリ販売管理・ChatGPT連携ツール",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="ダッシュボードを起動する")
    p_serve.add_argument("--port", type=int, default=8766)
    p_serve.set_defaults(func=cmd_serve)

    p_export = sub.add_parser("export", help="ChatGPT分析用データを出力する")
    p_export.add_argument("--kind", required=True, choices=("sourcing", "listing", "stale", "sales"))
    p_export.add_argument("--item", type=int, help="対象商品ID（sourcing/listing/stale）")
    p_export.add_argument("--format", default="text", choices=FORMATS)
    p_export.add_argument("--from", dest="date_from", help="集計開始日 YYYY-MM-DD（sales）")
    p_export.add_argument("--to", dest="date_to", help="集計終了日 YYYY-MM-DD（sales）")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import-listing", help="ChatGPTの出品用JSONを下書きとして取り込む")
    p_import.add_argument("--file", help="JSONファイル（省略時は標準入力）")
    p_import.set_defaults(func=cmd_import_listing)

    p_notify = sub.add_parser("notify-candidates", help="一次判定通過の仕入れ候補をDiscordへ通知する")
    p_notify.set_defaults(func=cmd_notify)

    p_backup = sub.add_parser("backup", help="データベースをbackups/へバックアップする")
    p_backup.set_defaults(func=cmd_backup)

    p_dump = sub.add_parser("dump-csv", help="テーブルをCSVへ書き出す")
    p_dump.add_argument("--table", required=True)
    p_dump.add_argument("--out")
    p_dump.set_defaults(func=cmd_dump_csv)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
