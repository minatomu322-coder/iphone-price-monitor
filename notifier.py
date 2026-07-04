from __future__ import annotations

import os
from typing import Any

import requests

from decision import judge


def send_discord(webhook_url: str | None, content: str) -> None:
    if not webhook_url:
        print(content)
        return
    response = requests.post(webhook_url, json={"content": content}, timeout=15)
    response.raise_for_status()


def notify_alert(
    webhook_url: str | None,
    item: dict[str, Any],
    record: dict[str, Any],
    best_record: dict[str, Any],
    reasons: list[str],
    thresholds: dict[str, Any],
) -> None:
    cost_price = int(item["cost_price"])
    price = int(best_record["price"])
    profit = price - cost_price
    decision = judge(price, cost_price, thresholds)
    diff = record.get("diff")
    diff_text = "初回取得" if diff is None else f"{diff:+,}円"
    source_updated = best_record.get("source_updated_at") or "取得不可"
    content = "\n".join(
        [
            "【iPhone買取価格アラート】",
            f"{item['name']} / {best_record['color_label']}",
            "",
            f"最高価格：{price:,}円",
            f"店舗：{best_record['shop_name']}",
            f"前回比：{diff_text}",
            f"原価差額：{profit:+,}円",
            f"更新日時：{source_updated}",
            f"URL：{best_record['url']}",
            "",
            "通知理由：",
            *[f"- {reason}" for reason in sorted(set(reasons))],
            "",
            "判断：",
            f"{decision.label}。{decision.message}",
        ]
    )
    send_discord(webhook_url, content)


def notify_daily_summary(
    webhook_url: str | None,
    summaries: list[dict[str, Any]],
    when_label: str,
) -> None:
    """デイリーまとめ（価格変化の有無に関わらず全アイテムを1通で通知）。"""
    lines = [
        f"【iPhone買取価格 デイリーまとめ】{when_label}",
        "※価格変化の有無に関わらず毎朝お届けします（海峡通信）",
        "",
    ]
    for s in summaries:
        lines.append(f"■ {s['name']}")
        if s.get("best_price") is None:
            lines.append("　最高買取：データなし（今回は価格を取得できませんでした）")
        else:
            lines.append(
                f"　最高買取 {int(s['best_price']):,}円"
                f"（{s.get('best_color', '-')} / {s.get('best_shop', '-')}）"
            )
            lines.append(
                f"　原価 {int(s['cost_price']):,}円 ／ 差額 {int(s['profit']):+,}円 → {s.get('decision', '-')}"
            )
    send_discord(webhook_url, "\n".join(lines).rstrip())


def notify_scrape_failure(webhook_url: str | None, shop_name: str, url: str, error: str) -> None:
    content = "\n".join(
        [
            "【iPhone買取価格スクレイピング失敗】",
            f"店舗：{shop_name}",
            f"URL：{url}",
            f"内容：{error[:500]}",
        ]
    )
    send_discord(webhook_url, content)


def webhook_from_config(config: dict[str, Any]) -> str | None:
    discord = config.get("discord", {})
    env_name = discord.get("webhook_env", "DISCORD_WEBHOOK_URL")
    return os.getenv(env_name)
