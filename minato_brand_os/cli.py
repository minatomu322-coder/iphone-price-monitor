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
from .agents.composer import compose_morning, compose_night
from .agents.replier import generate_replies
from .config import load_config
from .db import BrandDB, jst_iso, now_jst
from .discord import (
    notify_evening,
    notify_morning,
    notify_night,
    notify_night_personality,
    notify_noon,
)
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
        from .growth.advisor import build_advice

        stats = db.dashboard_stats()
        cand["advice"] = build_advice(db, cfg, cand)
        notify_noon(cand, stats, cfg=cfg, disc=db.discovery_stats_today())
        for c in cand["likes"]:  # 通知した候補を記録（90日ルールの起点）
            db.mark_notified(c["id"])
    elif args.slot == "evening":
        today = [{"star": _star_of(db, r), "name": r["name"] or r["handle"], "handle": r["handle"]}
                 for r in db.notified_today()]
        today.sort(key=lambda x: x["star"], reverse=True)
        notify_evening(today, _today_progress(db), cand)
    elif args.slot == "night":
        replies = {r["id"]: generate_replies(r, cfg) for r in cand["replies"]}
        notify_night(cand, replies)
    db.log_run("notify", f"slot={args.slot}")
    print(f"通知送信: {args.slot}")


def _star_of(db, acc_row) -> int:
    sc = db.latest_scores().get(int(acc_row["id"]))
    return int(sc["star"]) if sc else 3


# 交流種別 → ステータス自動昇格（降格なし）
# 自分の行動: like/reply(送信)/follow/dm/meet
# 相手の反応(KPIイベント): reply_received/followback/consult/deal
KIND_TO_STATUS = {"like": "ENGAGED", "reply": "ENGAGED", "follow": "FOLLOWED",
                  "dm": "ACTIVE", "meet": "ACTIVE",
                  "reply_received": "ENGAGED", "followback": "FOLLOWED",
                  "consult": "ACTIVE", "deal": "ACTIVE"}


def _find_account(db, handle: str):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM accounts WHERE handle=?", (handle.lstrip("@"),)).fetchone()


def cmd_record(args) -> None:
    db, cfg = _db(args.config)
    row = _find_account(db, args.handle)
    if not row:
        print(f"handle '{args.handle}' は未登録です。先に collect してください。")
        return
    db.add_interaction(int(row["id"]), args.kind, note=args.note or "", source="manual")
    db.promote_status(int(row["id"]), KIND_TO_STATUS[args.kind])
    rel = cfg["relationship"]
    r = db.recompute_relationship(int(row["id"]), rel["points"], rel["decay_per_day"], rel["cadence_days"])
    print(f"記録: @{args.handle} {args.kind} → 親密度 {r['intimacy']}% / 次回 {r['next_recommended_at'][:10]}")


def cmd_status(args) -> None:
    """ステータス手動変更（NOT_INTERESTED/ARCHIVED等）。"""
    db, _ = _db(args.config)
    row = _find_account(db, args.handle)
    if not row:
        print(f"handle '{args.handle}' は未登録です。")
        return
    db.set_status(int(row["id"]), args.set)
    print(f"@{args.handle} → {args.set}")


def cmd_reeval(args) -> None:
    """CEO再評価指定：90日ルールを解除して次回通知対象へ戻す。"""
    db, _ = _db(args.config)
    row = _find_account(db, args.handle)
    if not row:
        print(f"handle '{args.handle}' は未登録です。")
        return
    db.mark_reevaluate(int(row["id"]))
    print(f"@{args.handle} を再評価対象にしました（次回昼便で通知可能）。")


def cmd_dashboard(args) -> None:
    db, cfg = _db(args.config)
    cand = build_candidates(db, cfg)
    stats = db.dashboard_stats()
    disc = db.discovery_stats_today()
    funnel = db.kpi_funnel()
    print("== MINATO Brand OS ダッシュボード ==")
    print(f"今日新規発見: {stats['new_today']}人 / 重複ヒット: {disc['dup_today']}件")
    if disc["by_source"]:
        print("ソース別: " + " / ".join(f"{k}: {v['total']}件(重複{v['dup']})" for k, v in disc["by_source"].items()))
    print(f"通知可能(次回昼便): {len(cand['likes'])}人（目標30 / 不足 {cand['shortfall']}人）")
    if cand["shortfall"]:
        print(f"  不足原因: {cand['shortfall_reason']}")
    print(f"90日除外: {cand['excluded_dup']}人")
    print(f"DB総数: {stats['db_total']}人 / ACTIVE: {stats['active']}人 / ARCHIVED: {stats['archived']}人")
    print("-- Source分析（全期間） --")
    for s in db.source_analysis():
        print(f"  {s['source']:12} 取得{s['total']}件 / 候補{s['unique']}人 / "
              f"採用率{s['adoption_rate']:.0%} / 重複率{s['dup_rate']:.0%}")
    genres = db.genre_counts()
    if genres:
        print("-- テーマ別候補数 --")
        print("  " + " / ".join(f"{g}: {c}人" for g, c in list(genres.items())[:10]))
    print("-- 改善提案 --")
    from .growth.advisor import build_advice

    for line in build_advice(db, cfg, cand):
        print(f"  ・{line}")
    print("-- KPIファネル（通知コホートに対する独立転換率） --")
    print(f"候補 {funnel['candidates']}人 → 通知 {funnel['notified']}人")
    labels = {"like": "いいね", "reply": "リプ送信", "reply_received": "返信あり",
              "follow": "フォロー", "followback": "フォロバ", "dm": "DM",
              "consult": "相談", "deal": "成約"}
    for kind in db.FUNNEL_KINDS:
        n, rate = funnel["events"][kind], funnel["rates"][kind]
        print(f"  {labels[kind]:6}: {n}人 ({rate:.0%})")


def cmd_scout(args) -> None:
    """Web検索で候補アカウントを自動発掘（要ANTHROPIC_API_KEY。P1で正式統合予定）。"""
    from .agents.scout import scout

    db, cfg = _db(args.config)
    scout(db, cfg)


def cmd_daily(args) -> None:
    """毎朝バッチ: Growth Engineパイプライン（発見→正規化→重複排除→保存）→採点。"""
    from .growth import run_pipeline

    db, cfg = _db(args.config)
    stats = run_pipeline(db, cfg)
    print(f"発見: 新規{stats['new']}人 / 重複{stats['duplicate']}件 / 統合{stats['merged']}件"
          + (f" / ソース障害: {list(stats['source_errors'])}" if stats["source_errors"] else ""))
    cmd_analyze(args)


# ---------------- Proof & Personality Engine ----------------

def cmd_brief(args) -> None:
    """朝便(Proof3件) / 夜便(Personality3件) を生成してDiscord通知。"""
    db, cfg = _db(args.config)
    cand = build_candidates(db, cfg)
    if args.slot == "morning":
        drafts = compose_morning(db, cfg)
        notify_morning(drafts, cand)
    else:
        drafts = compose_night(db, cfg)
        notify_night_personality(drafts, cand)
    db.log_run("brief", f"slot={args.slot} drafts={len(drafts)}")
    print(f"{args.slot}便: 候補{len(drafts)}件を通知しました。")


def cmd_memo(args) -> None:
    """Personality素材の1行メモを追加。"""
    db, _ = _db(args.config)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO memos (created_at, kind, text) VALUES (?,?,?)",
            (jst_iso(), args.kind, args.text),
        )
    print(f"メモ保存: [{args.kind}] {args.text}")


def cmd_posted(args) -> None:
    """投稿候補を「投稿済み」にする（投稿履歴＝実験データの起点）。"""
    db, _ = _db(args.config)
    with db.connect() as conn:
        row = conn.execute("SELECT id, status FROM post_drafts WHERE id=?", (args.draft,)).fetchone()
        if not row:
            print(f"候補 #{args.draft} が見つかりません。")
            return
        conn.execute(
            "UPDATE post_drafts SET status='posted', posted_at=? WHERE id=?",
            (jst_iso(), args.draft),
        )
    print(f"候補 #{args.draft} を投稿済みにしました。KPIは後で: python mbos.py kpi --draft {args.draft} --imp 1000 --likes 5")


def cmd_kpi(args) -> None:
    """投稿のKPIを記録（Xアナリティクスから手入力）。"""
    db, _ = _db(args.config)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO post_kpis (draft_id, recorded_at, impressions, likes, replies, bookmarks, profile_views, follows)
               VALUES (?,?,?,?,?,?,?,?)""",
            (args.draft, jst_iso(), args.imp, args.likes, args.replies,
             args.bookmarks, args.views, args.follows),
        )
    print(f"KPI記録: draft#{args.draft} imp={args.imp} likes={args.likes}")


def cmd_metrics(args) -> None:
    """日次アカウント指標を記録（週2回でOK）。"""
    db, _ = _db(args.config)
    d = args.date or now_jst().date().isoformat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO daily_metrics (date, followers, profile_views, dms_received, replies_received, note)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 followers=COALESCE(excluded.followers, followers),
                 profile_views=COALESCE(excluded.profile_views, profile_views),
                 dms_received=COALESCE(excluded.dms_received, dms_received),
                 replies_received=COALESCE(excluded.replies_received, replies_received),
                 note=COALESCE(excluded.note, note)""",
            (d, args.followers, args.views, args.dms, args.replies, args.note),
        )
    print(f"日次指標を記録: {d}")


def cmd_report(args) -> None:
    """実験レポート: 投稿タイプ別×時間帯別の成績（14日実験の集計）。"""
    db, _ = _db(args.config)
    with db.connect() as conn:
        by_type = conn.execute(
            """SELECT d.post_type,
                      COUNT(DISTINCT d.id) posts,
                      AVG(k.impressions) imp, AVG(k.likes) likes,
                      AVG(k.replies) reps, AVG(k.profile_views) views, AVG(k.follows) fol
               FROM post_drafts d JOIN post_kpis k ON k.draft_id=d.id
               WHERE d.status='posted'
               GROUP BY d.post_type ORDER BY imp DESC"""
        ).fetchall()
        by_hour = conn.execute(
            """SELECT substr(d.posted_at,12,2) hh, COUNT(*) posts,
                      AVG(k.impressions) imp, AVG(k.likes) likes
               FROM post_drafts d JOIN post_kpis k ON k.draft_id=d.id
               WHERE d.status='posted' AND d.posted_at IS NOT NULL
               GROUP BY hh ORDER BY imp DESC"""
        ).fetchall()
        metrics = conn.execute(
            "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT 14"
        ).fetchall()

    print("== 投稿タイプ別（平均） ==")
    if not by_type:
        print("  データなし（posted + kpi を記録すると出ます）")
    for r in by_type:
        print(f"  {r['post_type']:<12} 投稿{r['posts']}本  imp {r['imp'] or 0:.0f}  "
              f"like {r['likes'] or 0:.1f}  リプ {r['reps'] or 0:.1f}  "
              f"プロフ {r['views'] or 0:.1f}  フォロー {r['fol'] or 0:.1f}")
    print("== 投稿時間帯別（平均） ==")
    for r in by_hour:
        print(f"  {r['hh']}時台  投稿{r['posts']}本  imp {r['imp'] or 0:.0f}  like {r['likes'] or 0:.1f}")
    print("== 直近の日次指標 ==")
    for r in metrics:
        print(f"  {r['date']}  フォロワー{r['followers'] or '-'}  プロフ{r['profile_views'] or '-'}  "
              f"DM{r['dms_received'] or '-'}  被リプ{r['replies_received'] or '-'}")


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

    r = sub.add_parser("record", help="交流/KPIイベントを1件記録(CRM)")
    r.add_argument("--handle", required=True)
    r.add_argument("--kind", required=True,
                   choices=["like", "reply", "follow", "dm", "meet",
                            "reply_received", "followback", "consult", "deal"])
    r.add_argument("--note", default="", help="メモ（dealは金額等を記録推奨）")
    r.set_defaults(func=cmd_record)

    d = sub.add_parser("daily", help="collect→analyze")
    d.add_argument("--source", default="seed_csv")
    d.set_defaults(func=cmd_daily)

    b = sub.add_parser("brief", help="朝便(Proof)/夜便(Personality)を生成して通知")
    b.add_argument("--slot", required=True, choices=["morning", "night"])
    b.set_defaults(func=cmd_brief)

    m = sub.add_parser("memo", help="Personality素材の1行メモ")
    m.add_argument("--kind", required=True, choices=["fail", "learn", "story", "thought"])
    m.add_argument("--text", required=True)
    m.set_defaults(func=cmd_memo)

    po = sub.add_parser("posted", help="投稿候補を投稿済みにする")
    po.add_argument("--draft", type=int, required=True)
    po.set_defaults(func=cmd_posted)

    k = sub.add_parser("kpi", help="投稿KPIを記録")
    k.add_argument("--draft", type=int, required=True)
    k.add_argument("--imp", type=int, default=None)
    k.add_argument("--likes", type=int, default=None)
    k.add_argument("--replies", type=int, default=None)
    k.add_argument("--bookmarks", type=int, default=None)
    k.add_argument("--views", type=int, default=None, help="プロフィール閲覧")
    k.add_argument("--follows", type=int, default=None)
    k.set_defaults(func=cmd_kpi)

    me = sub.add_parser("metrics", help="日次アカウント指標を記録")
    me.add_argument("--date", default=None, help="YYYY-MM-DD（省略=今日）")
    me.add_argument("--followers", type=int, default=None)
    me.add_argument("--views", type=int, default=None, help="プロフィール閲覧")
    me.add_argument("--dms", type=int, default=None)
    me.add_argument("--replies", type=int, default=None, help="受け取ったリプ数")
    me.add_argument("--note", default=None)
    me.set_defaults(func=cmd_metrics)

    rp = sub.add_parser("report", help="実験レポート(タイプ別×時間帯別)")
    rp.set_defaults(func=cmd_report)

    sc = sub.add_parser("scout", help="Web検索で候補を自動発掘(要ANTHROPIC_API_KEY)")
    sc.set_defaults(func=cmd_scout)

    st = sub.add_parser("status", help="候補者ステータス変更")
    st.add_argument("--handle", required=True)
    st.add_argument("--set", required=True,
                    choices=["NEW", "DISCOVERED", "ENGAGED", "FOLLOWED", "ACTIVE", "NOT_INTERESTED", "ARCHIVED"])
    st.set_defaults(func=cmd_status)

    rv = sub.add_parser("reeval", help="CEO再評価指定(90日ルール解除)")
    rv.add_argument("--handle", required=True)
    rv.set_defaults(func=cmd_reeval)

    db_ = sub.add_parser("dashboard", help="ダッシュボード表示")
    db_.set_defaults(func=cmd_dashboard)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
