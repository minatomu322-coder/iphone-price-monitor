from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path
from typing import Any

import requests

from .models import STRATEGY_LABELS, Candidate
from .profit import comparison_text
from .strategy import STRATEGY_ORDER


# 設計 §7 の24列。スプレッドシートにそのまま貼れる並び。
CSV_COLUMNS = [
    "提出日", "戦略", "タイトル", "商品名", "型番", "レアリティ", "状態",
    "仕入先", "仕入URL", "仕入価格", "在庫数",
    "推奨出口", "想定売価", "手数料", "送料・資材", "純利益", "利益率", "想定回転日数",
    "チャネル比較", "フリマ指値", "探索キーワード", "根拠", "リスク", "推奨仕入数",
]


def candidate_to_row(c: Candidate, as_of: date) -> dict[str, Any]:
    best = c.profit.best
    m = c.metrics
    limit = c.profit.flea_limit_price
    return {
        "提出日": as_of.isoformat(),
        "戦略": STRATEGY_LABELS[c.strategy],
        "タイトル": c.product.title_label,
        "商品名": c.product.name,
        "型番": c.product.display_no,
        "レアリティ": c.product.rarity,
        "状態": m.buy_condition or ("新品未開封" if c.product.form == "box" else ""),
        "仕入先": m.buy_source,
        "仕入URL": m.buy_url,
        "仕入価格": c.profit.buy_price,
        "在庫数": "" if m.stock_total is None else m.stock_total,
        "推奨出口": best.label,
        "想定売価": best.gross,
        "手数料": best.fee,
        "送料・資材": best.shipping + best.packing,
        "純利益": best.profit,
        "利益率": f"{best.margin:.1%}",
        "想定回転日数": best.turnover_days,
        "チャネル比較": comparison_text(c.profit),
        "フリマ指値": "" if limit is None else limit,
        "探索キーワード": c.product.keyword,
        "根拠": " ／ ".join(c.reasons),
        "リスク": " ／ ".join(c.risks),
        "推奨仕入数": c.recommended_qty,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Excel/スプレッドシートで文字化けしないよう BOM 付き UTF-8
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def build_summary(selected: dict[str, list[Candidate]], as_of: date, csv_path: Path, per_strategy: dict[str, int]) -> str:
    total = sum(len(v) for v in selected.values())
    lines = [f"【TCG 利益商品 {total}件】{as_of.isoformat()}"]
    for strategy in STRATEGY_ORDER:
        items = selected.get(strategy, [])
        label = STRATEGY_LABELS[strategy]
        want = int(per_strategy.get(strategy, 0))
        if not items:
            lines.append(f"■{label}(0/{want}) 該当なし（条件を満たす商品なし／履歴不足）")
            continue
        profit_sum = sum(c.profit.best.profit for c in items)
        lines.append(f"■{label}({len(items)}/{want}) 想定利益合計 {profit_sum:,}円")
        for i, c in enumerate(items, 1):
            best = c.profit.best
            lines.append(f"　{i}. {c.product.title_label} {c.product.display_no} {c.product.name}".rstrip())
            lines.append(
                f"　　 仕入 {c.metrics.buy_source} {c.profit.buy_price:,} → {best.label} {best.gross:,}"
                f" ／ 純利益 {best.profit:+,}（{best.margin:.1%}）／ 回転{best.turnover_days}日"
            )
            if c.profit.flea_limit_price:
                lines.append(f"　　 フリマ指値：{c.profit.flea_limit_price:,}以下  検索「{c.product.keyword}」")
    lines.append(f"→ 詳細CSV: {csv_path.as_posix()}")
    return "\n".join(lines)


def send_discord(webhook_url: str | None, content: str) -> None:
    """Discordの2000文字制限に合わせて分割送信。Webhook未設定なら標準出力へ。"""
    if not webhook_url:
        print(content)
        return
    for chunk in split_message(content):
        response = requests.post(webhook_url, json={"content": chunk}, timeout=15)
        response.raise_for_status()


def split_message(content: str, limit: int = 1900) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in content.splitlines():
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def webhook_from_config(config: dict[str, Any]) -> str | None:
    env_name = config.get("output", {}).get("discord_webhook_env", "DISCORD_WEBHOOK_URL")
    return os.getenv(env_name)
