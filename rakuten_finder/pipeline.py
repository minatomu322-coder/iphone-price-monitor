"""巡回パイプライン。

楽天API検索 → 相場照合 → 利益計算 → スコアリング → 保存 → 通知 の一連を実行。
1 キーワードの失敗（API エラー等）で全体を止めず、エラーは記録して継続する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone

from .config import Config
from .database import FinderDatabase
from .mercari import CsvMercariSource, MercariSource
from .models import Candidate
from .notifier import notify_candidate, notify_daily_report, notify_error
from .profit import calc_profit
from .rakuten_api import RakutenApiError, search_items
from .scoring import score_candidate


@dataclass
class RunResult:
    searched: int = 0          # 楽天から取得した商品数
    matched: int = 0           # 相場と照合できた商品数
    saved: int = 0             # DB に保存した観測数
    notified: int = 0          # Discord に通知した件数
    errors: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)


def run_pipeline(
    config: Config,
    db: FinderDatabase | None = None,
    source: MercariSource | None = None,
    notify: bool = True,
) -> RunResult:
    db = db or FinderDatabase(config.db_path)
    source = source or CsvMercariSource(config.mercari_csv)
    webhook = config.webhook_url
    decision_stats = db.decision_stats_by_keyword()
    result = RunResult()

    for target in config.targets:
        try:
            items = search_items(config, target)
        except RakutenApiError as exc:
            message = f"検索失敗 keyword={target.keyword}: {exc}"
            result.errors.append(message)
            db.insert_error("rakuten_search", message)
            if notify:
                notify_error(webhook, "楽天API検索", message)
            continue

        result.searched += len(items)
        for item in items:
            stats = source.lookup(item)
            if stats is None:
                continue  # 相場データが無い商品は評価できない（MVP 仕様）
            result.matched += 1

            profit = calc_profit(item, stats, config.assumptions)
            score = score_candidate(
                item, stats, profit, config.weights, config.thresholds, decision_stats
            )
            candidate = Candidate(item=item, stats=stats, profit=profit, score=score)
            db.save_candidate(candidate)
            result.saved += 1
            result.candidates.append(candidate)

            if _should_notify(candidate, config, db) and notify:
                try:
                    notify_candidate(webhook, candidate)
                    result.notified += 1
                except Exception as exc:  # 通知失敗でパイプラインは止めない
                    message = f"通知失敗 {item.item_code}: {exc}"
                    result.errors.append(message)
                    db.insert_error("discord_notify", message)

    return result


def _should_notify(candidate: Candidate, config: Config, db: FinderDatabase) -> bool:
    """通知条件: ランク合致 + しきい値合致 + 重複抑制。"""
    th = config.thresholds
    if candidate.score.rank not in th.notify_rank:
        return False
    if candidate.profit.profit < th.min_profit or candidate.profit.roi < th.min_roi:
        return False
    # 「商品 × 実質仕入 × 売価」が同じ間は再通知しない（価格が動いたら再通知）
    dedupe_key = "|".join(
        [
            candidate.item.item_code,
            str(candidate.profit.effective_cost),
            str(candidate.profit.sell_price),
        ]
    )
    return db.should_notify(dedupe_key, candidate.item.item_code, th.alert_repeat_hours)


def run_daily_report(config: Config, db: FinderDatabase | None = None) -> None:
    """本日分（UTC 0時以降）の集計を Discord に送る。"""
    db = db or FinderDatabase(config.db_path)
    since = datetime.combine(
        datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc
    ).isoformat()
    summary = db.daily_summary(since)
    notify_daily_report(
        config.webhook_url, summary, datetime.now(timezone.utc).date().isoformat()
    )
