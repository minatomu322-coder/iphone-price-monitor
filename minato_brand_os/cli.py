from __future__ import annotations

"""MINATO Brand OS コマンドライン。

  python mbos.py collect                 # シードCSV→DB（①ターゲット収集）
  python mbos.py analyze                 # 多軸採点＋CRM再計算（②③⑥）
  python mbos.py notify --slot noon      # 12:00便（④）
  python mbos.py notify --slot evening   # 17:30便（④）
  python mbos.py notify --slot night     # 22:00便＋リプ生成（④⑤）
  python mbos.py record --handle x --kind like   # 交流を1件記録（⑥CRM）
  python mbos.py daily                    # collect→analyze（毎朝バッチ）
"""

import argparse
from datetime import date

from .agents.analyzer import analyze_all
from .agents.replier import generate_replies
from .config import load_config
from .db import BrandDB, now_jst
from .discord import notify_evening, notify_night, notify_noon
from .select import build_candidates
from . import x_client


def _db(cfg_path: str | None) -> tuple[BrandDB, dict]:
    cfg = load_config(cfg_path)
    from .config import DEFAULT_DB

    return BrandDB(DEFAULT_DB), cfg


def cmd_collect(args) -> None:
    db, cfg = _db(args.config)
    accounts = x_client.collect(source=args.source)
    for acc in accounts:
        db.upsert_account(acc)
    db.log_run("collect", f"source={args.source} count={len(accounts)}")
    print(f"収集: {len(accounts)}件 を取り込みました。")


def cmd_analyze(args) -> None:
    db, cfg = _db(args.config)
    stats = analyze_all(db, cfg)
    print(f"分析完了: {stats}")


def _today_progress(db: BrandDB) -> dict[str, int]:
    today = now_jst().date().isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) c FROM interactions WHERE substr(occurred_at,1,10)=? GROUP BY kind",
            (today,),
        ).fetchall()
    return {r["kind"]: int(r["c"]) for r in rows}


def cmd_notify(args) -> None:
    db, cfg = _db(args.config)
    cand = build_candidates(db, cfg)
    if args.slot == "noon":
        notify_noon(cand)
    elif args.slot == "evening":
        notify_evening(cand, _today_progress(db))
    elif args.slot == "night":
        replies = {r["id"]: generate_replies(r, cfg) for r in cand["replies"]}
        notify_night(cand, replies)
    db.log_run("notify", f"slot={args.slot}")
    print(f"通知送信: {args.slot}")


def cmd_record(args) -> None:
    db, cfg = _db(args.config)
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE handle=?", (args.handle.lstrip("@"),)).fetchone()
    if not row:
        print(f"handle '{args.handle}' は未登録です。先に collect してください。")
        return
    db.add_interaction(int(row["id"]), args.kind, note=args.note or "", source="manual")
    rel = cfg["relationship"]
    r = db.recompute_relationship(int(row["id"]), rel["points"], rel["decay_per_day"], rel["cadence_days"])
    print(f"記録: @{args.handle} {args.kind} → 親密度 {r['intimacy']}% / 次回 {r['next_recommended_at'][:10]}")


def cmd_daily(args) -> None:
    cmd_collect(args)
    cmd_analyze(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mbos", description="MINATO Brand OS")
    p.add_argument("--config", default=None, help="設定YAMLパス")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="シードCSV→DB")
    c.add_argument("--source", default="seed_csv")
    c.set_defaults(func=cmd_collect)

    a = sub.add_parser("analyze", help="多軸採点＋CRM再計算")
    a.set_defaults(func=cmd_analyze)

    n = sub.add_parser("notify", help="Discord通知")
    n.add_argument("--slot", required=True, choices=["noon", "evening", "night"])
    n.set_defaults(func=cmd_notify)

    r = sub.add_parser("record", help="交流を1件記録(CRM)")
    r.add_argument("--handle", required=True)
    r.add_argument("--kind", required=True, choices=["like", "reply", "follow", "dm", "meet"])
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_record)

    d = sub.add_parser("daily", help="collect→analyze")
    d.add_argument("--source", default="seed_csv")
    d.set_defaults(func=cmd_daily)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
