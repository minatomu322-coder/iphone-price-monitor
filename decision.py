from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    label: str
    message: str


def judge(price: int, cost_price: int, thresholds: dict[str, Any]) -> Decision:
    profit = price - cost_price
    if profit >= 0:
        return Decision("即売り推奨", "トントン以上。価格が崩れる前に売却候補。")
    if profit >= int(thresholds.get("sell_recommend", -3000)):
        return Decision("売却推奨", "原価差が-3,000円以内。手間と下落リスクを考えると売却候補。")
    if profit >= int(thresholds.get("near_break_even", -5000)):
        return Decision("売却検討", "原価差が-5,000円以内。弱い色から売却検討。")
    if profit >= int(thresholds.get("watch_limit", -10000)):
        return Decision("期限付き監視", "まだ待ち。ただし-5,000円以内に戻ったら売却候補。")
    return Decision("損切り検討", "さらに下落。期限を決め、戻らなければ損切り候補。")


def alert_reasons(
    record: dict[str, Any],
    best_record: dict[str, Any] | None,
    color_records: list[dict[str, Any]],
    cost_price: int,
    thresholds: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    diff = record.get("diff")
    if diff is not None and diff >= int(thresholds.get("rise_alert", 3000)):
        reasons.append(f"前回より+{diff:,}円上昇")
    if diff is not None and diff <= int(thresholds.get("drop_alert", -5000)):
        reasons.append(f"前回より{diff:,}円下落")

    if best_record and same_offer(record, best_record):
        profit = int(best_record["price"]) - cost_price
        if profit >= 0:
            reasons.append("最高価格がトントン以上に到達")
        elif profit >= int(thresholds.get("near_break_even", -5000)):
            reasons.append("最高価格が原価-5,000円以内に回復")

    prices = [int(row["price"]) for row in color_records]
    if len(prices) >= 2:
        gap = max(prices) - min(prices)
        if gap >= int(thresholds.get("shop_gap_alert", 8000)):
            reasons.append(f"店舗間の価格差が{gap:,}円")
    return reasons


def same_offer(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("shop_name") == right.get("shop_name")
        and left.get("color_key") == right.get("color_key")
        and int(left.get("price", 0)) == int(right.get("price", 0))
    )
