from __future__ import annotations

import os
from typing import Any

from mercari.db import MercariDatabase
from mercari.decision import LABEL_BUY, LABEL_CONDITIONAL, primary_judgement
from notifier import send_discord


def webhook_from_config(config: dict[str, Any]) -> str | None:
    env_name = config.get("discord", {}).get("webhook_env", "DISCORD_WEBHOOK_URL")
    return os.getenv(env_name)


def notify_sourcing_candidates(
    db: MercariDatabase,
    config: dict[str, Any],
    webhook_url: str | None = None,
) -> int:
    """仕入れ候補のうち一次判定が「買い候補/条件付き候補」のものをDiscordへまとめて通知する。

    通知はあくまで候補の共有であり、購入操作は行わない。
    """
    webhook_url = webhook_url or webhook_from_config(config)
    candidates = db.list_items(status="candidate")
    lines: list[str] = []
    count = 0
    for item in candidates:
        market = db.latest_market(item["id"])
        history = db.market_history(item["id"])
        judgement = primary_judgement(item, market, history, config)
        if judgement["label"] not in (LABEL_BUY, LABEL_CONDITIONAL):
            continue
        count += 1
        standard = (judgement.get("ladder") or {}).get("standard", {})
        lines.append(f"■ [{judgement['label']}] {item['name']}（item {item['id']}）")
        if item.get("purchase_price") is not None:
            lines.append(f"　仕入れ {int(item['purchase_price']):,}円")
        if standard:
            roi = standard.get("roi")
            roi_text = f" / ROI {round(roi * 100)}%" if roi is not None else ""
            lines.append(
                f"　相場価格 {standard['price']:,}円 → 利益 {standard['profit']:+,}円{roi_text}"
            )
        for reason in judgement["reasons"]:
            lines.append(f"　- {reason}")
        for warning in judgement["warnings"]:
            lines.append(f"　⚠ {warning}")
        if item.get("purchase_url"):
            lines.append(f"　{item['purchase_url']}")
    if not count:
        return 0
    content = "\n".join(
        [
            "【メルカリ仕入れ候補】",
            "一次判定を通過した候補です。ダッシュボードの「仕入れ判断用にコピー」で"
            "ChatGPTへ最終レビューを依頼してください。",
            "",
            *lines,
        ]
    )
    send_discord(webhook_url, content)
    return count
