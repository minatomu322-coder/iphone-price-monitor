"""AI（ChatGPT等）から返るJSONの解析・検証を一元管理するモジュール。

UI・DB・HTTPに依存しない純粋な解析層。将来OpenAI API / Claude API / Gemini APIへ
切り替えても、応答テキストをここへ渡すだけで既存の取り込み処理がそのまま使える
（プロンプト生成は prompts.py、DBへの反映は importer.py が担当）。
"""
from __future__ import annotations

import json
import re
from typing import Any


TYPE_ITEM_INFO = "mercari_item_info"
TYPE_LISTING_DRAFT = "mercari_listing_draft"

LISTING_REQUIRED = ("title", "description", "price")

MARKET_FIELDS = (
    "min_price", "median_price", "mean_price", "max_price",
    "sold_count", "active_count", "url", "source", "notes",
)


def parse_json_object(raw: str | dict[str, Any]) -> dict[str, Any]:
    """文字列または辞書からJSONオブジェクトを取り出す。

    AIの返答にはコードブロック（```json ... ```）や前後の文章が混ざることがあるため、
    その中からJSONオブジェクトを抽出できるようにしてある。
    """
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError("JSONオブジェクト（{...}）を渡してください")
    except json.JSONDecodeError:
        pass
    # コードブロックや文章に埋まったJSONを抽出して再挑戦する
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if candidate is None:
        raise ValueError("JSONとして読み込めません（{...}が見つかりません）")
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSONとして読み込めません: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSONオブジェクト（{...}）を渡してください")
    return data


def detect_type(data: dict[str, Any]) -> str:
    """AI応答JSONの種別を判定する（typeフィールド優先、無ければ内容から推定）。"""
    declared = data.get("type")
    if declared in (TYPE_ITEM_INFO, TYPE_LISTING_DRAFT):
        return declared
    if all(data.get(key) for key in LISTING_REQUIRED):
        return TYPE_LISTING_DRAFT
    if data.get("name") or data.get("item_id"):
        return TYPE_ITEM_INFO
    raise ValueError(
        f"JSONの種別を判定できません（typeに{TYPE_ITEM_INFO}か{TYPE_LISTING_DRAFT}を指定してください）"
    )


def validate_listing_draft(data: dict[str, Any]) -> dict[str, Any]:
    """出品下書きJSONを検証し、正規化して返す。"""
    missing = [key for key in LISTING_REQUIRED if not data.get(key)]
    if missing:
        raise ValueError(f"必須項目が不足しています: {', '.join(missing)}")
    price = int(data["price"])
    if price < 300:
        raise ValueError("メルカリの最低出品価格は300円です")
    return {**data, "price": price}


def validate_item_info(data: dict[str, Any]) -> dict[str, Any]:
    """商品情報JSONを検証し、正規化して返す。"""
    if not data.get("item_id") and not data.get("name"):
        raise ValueError("nameは必須です（商品を特定できませんでした）")
    confidence = data.get("confidence")
    if confidence is not None:
        confidence = int(confidence)
        if not 0 <= confidence <= 100:
            raise ValueError("confidenceは0〜100で指定してください")
    market = data.get("market")
    if market is not None and not isinstance(market, dict):
        raise ValueError("marketはオブジェクト（{...}）で指定してください")
    return {**data, "confidence": confidence}
