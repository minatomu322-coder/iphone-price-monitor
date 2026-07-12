from __future__ import annotations

"""Proofソース第1号: iPhone買取価格監視（prices.sqlite3）。

このリポジトリで30分ごとに自動収集している実データから、
「投稿の材料になる数字付きの出来事」を抽出する。

抽出するFact:
    - price_move : 直近3日/7日で大きく動いた機種（±2,000円以上）
    - profit_now : 原価(config.yaml)に対して今すぐ売った場合の利益・ROI
    - spread     : 同一機種の店舗間価格差（あれば）
"""

import sqlite3
from pathlib import Path

import yaml

from .base import ProofFact

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PRICES_DB = BASE_DIR / "prices.sqlite3"
ITEMS_CONFIG = BASE_DIR / "config.yaml"

MOVE_THRESHOLD = 2000  # この額以上動いたらFact化


def _cost_prices() -> dict[str, int]:
    try:
        with ITEMS_CONFIG.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return {i["name"]: int(i["cost_price"]) for i in cfg.get("items", []) if i.get("cost_price")}
    except Exception:  # noqa: BLE001
        return {}


def _connect() -> sqlite3.Connection | None:
    if not PRICES_DB.exists():
        return None
    conn = sqlite3.connect(PRICES_DB)
    conn.row_factory = sqlite3.Row
    return conn


def collect_facts() -> list[ProofFact]:
    conn = _connect()
    if conn is None:
        return []
    costs = _cost_prices()
    facts: list[ProofFact] = []
    try:
        # 機種×色ごとの最新価格と、3日前・7日前の価格
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT item_name, color_label, shop_name, price, observed_at,
                       ROW_NUMBER() OVER (PARTITION BY item_name, color_key
                                          ORDER BY observed_at DESC, id DESC) rn
                FROM price_observations
            )
            SELECT item_name, color_label, shop_name, price, observed_at
            FROM latest WHERE rn = 1
            """
        ).fetchall()

        seen_items: set[str] = set()
        for r in rows:
            item, color = r["item_name"], r["color_label"]
            latest_price = int(r["price"])

            for days, label in ((3, "3日"), (7, "1週間")):
                old = conn.execute(
                    """
                    SELECT price FROM price_observations
                    WHERE item_name=? AND color_label=?
                      AND datetime(observed_at) <= datetime(?, ?)
                    ORDER BY observed_at DESC LIMIT 1
                    """,
                    (item, color, r["observed_at"], f"-{days} days"),
                ).fetchone()
                if not old:
                    continue
                diff = latest_price - int(old["price"])
                if abs(diff) >= MOVE_THRESHOLD:
                    direction = "上昇" if diff > 0 else "下落"
                    facts.append(ProofFact(
                        source="iphone_price",
                        kind="price_move",
                        headline=f"{item}（{color}）の買取価格が{label}で{diff:+,}円の{direction}",
                        numbers={"現在価格": latest_price, "変動": diff, "期間": label},
                        context=f"店舗: {r['shop_name']} / 当システムが30分ごとに自動記録した実測値",
                        judgement_hint=(
                            "上昇中→まだ持つか今売るか、下落中→損切りか静観か。自分の判断を書く"
                        ),
                        impact=abs(diff),
                        tags=["iPhone", "せどり", "相場"],
                    ))
                    break  # 3日で動いていれば7日はスキップ（重複防止）

            # 原価があれば「今売ったら」の利益Fact（機種ごとに1回）
            cost = costs.get(item)
            if cost and item not in seen_items:
                seen_items.add(item)
                profit = latest_price - cost
                roi = profit / cost * 100
                facts.append(ProofFact(
                    source="iphone_price",
                    kind="profit_now",
                    headline=f"{item}: 今売ると {profit:+,}円（ROI {roi:+.1f}%）",
                    numbers={"買取価格": latest_price, "原価": cost, "利益": profit, "ROI%": round(roi, 1)},
                    context=f"店舗: {r['shop_name']} / 原価は実際の仕入値",
                    judgement_hint="なぜこのROIで売る/売らないのか、待つならいくらまで待つか",
                    impact=abs(profit) / 10,  # 値動きFactより控えめの重み
                    tags=["iPhone", "せどり", "利益"],
                ))
    finally:
        conn.close()
    return facts
