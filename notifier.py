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
