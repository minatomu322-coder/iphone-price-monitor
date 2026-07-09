"""Discord 通知。

- notify_candidate: 利益商品 1 件の通知（embed 形式・画像付き）
- notify_daily_report: 日次レポート
- notify_error: エラー通知

Webhook 未設定時は標準出力に出す（ローカル動作確認用）。
"""
from __future__ import annotations

import json
from typing import Any

import requests

from .models import Candidate

RANK_EMOJI = {"S": "🟣", "A": "🔵", "B": "🟢", "C": "🟡", "D": "⚪"}


def _post(webhook_url: str | None, payload: dict[str, Any]) -> None:
    if not webhook_url:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    response = requests.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def notify_candidate(webhook_url: str | None, candidate: Candidate) -> None:
    item, stats, profit, score = (
        candidate.item,
        candidate.stats,
        candidate.profit,
        candidate.score,
    )
    emoji = RANK_EMOJI.get(score.rank, "")
    fields = [
        {"name": "楽天価格", "value": f"{profit.rakuten_price:,}円", "inline": True},
        {"name": "実質仕入", "value": f"{profit.effective_cost:,}円", "inline": True},
        {"name": "メルカリ相場", "value": f"{profit.sell_price:,}円", "inline": True},
        {"name": "想定利益", "value": f"{profit.profit:+,}円", "inline": True},
        {"name": "ROI", "value": f"{profit.roi:.1%}", "inline": True},
        {"name": "売れた件数/出品中", "value": f"{stats.sold_count}件 / {stats.active_count}件", "inline": True},
        {"name": "ポイント想定", "value": f"{profit.point_total:,}pt", "inline": True},
        {"name": "ショップ", "value": item.shop_name or "-", "inline": True},
        {"name": "おすすめ度", "value": f"{score.rank}（{score.score:.0f}点）", "inline": True},
    ]
    if score.warnings:
        fields.append(
            {"name": "⚠ 危険ポイント", "value": "\n".join(f"- {w}" for w in score.warnings), "inline": False}
        )
    fields.append(
        {
            "name": "リンク",
            "value": f"[楽天で見る]({item.url}) / [メルカリ相場を確認]({candidate.mercari_search_url})",
            "inline": False,
        }
    )
    fields.append(
        {
            "name": "判断の記録",
            "value": (
                "ダッシュボードで「買う / 見送り / 保留」を記録すると学習に反映されます。\n"
                f"`item_code: {item.item_code}`"
            ),
            "inline": False,
        }
    )
    embed: dict[str, Any] = {
        "title": f"{emoji} [{score.rank}] {item.name[:200]}",
        "url": item.url or None,
        "color": {"S": 0x9B59B6, "A": 0x3498DB, "B": 0x2ECC71, "C": 0xF1C40F}.get(score.rank, 0x95A5A6),
        "fields": fields,
    }
    if item.image_url:
        embed["thumbnail"] = {"url": item.image_url}
    _post(webhook_url, {"content": "【利益商品AI】利益候補を検知", "embeds": [embed]})


def notify_daily_report(webhook_url: str | None, summary: dict[str, Any], when_label: str) -> None:
    lines = [
        f"【利益商品AI デイリーレポート】{when_label}",
        f"今日評価した商品数：{summary['total']}件",
        "",
    ]
    if summary["s_rank"]:
        lines.append("◆ Sランク商品")
        for row in summary["s_rank"]:
            lines.append(f"　- {row['name'][:60]}（利益 {int(row['profit']):+,}円）")
    else:
        lines.append("◆ Sランク商品：なし")
    lines.append("")
    lines.append("◆ 想定利益ランキング")
    for i, row in enumerate(summary["top_profit"], 1):
        lines.append(f"　{i}. {row['name'][:60]}（{int(row['profit']):+,}円）")
    lines.append("")
    lines.append("◆ ROIランキング")
    for i, row in enumerate(summary["top_roi"], 1):
        lines.append(f"　{i}. {row['name'][:60]}（{float(row['roi']):.1%}）")
    _post(webhook_url, {"content": "\n".join(lines).rstrip()})


def notify_error(webhook_url: str | None, context: str, message: str) -> None:
    content = "\n".join(
        [
            "【利益商品AI エラー】",
            f"箇所：{context}",
            f"内容：{message[:500]}",
        ]
    )
    _post(webhook_url, {"content": content})
