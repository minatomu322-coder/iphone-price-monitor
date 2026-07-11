from __future__ import annotations

import json
from typing import Any

from mercari.db import MercariDatabase


# ChatGPTに生成してもらう出品用JSONの想定スキーマ。
# {
#   "type": "mercari_listing_draft",
#   "item_id": 3,                    // 既存商品に紐付ける場合。省略時はnameから新規作成
#   "name": "商品名",                 // item_id省略時は必須
#   "title": "出品タイトル",           // 必須
#   "description": "商品説明",         // 必須
#   "price": 12800,                  // 必須
#   "category": "...", "brand": "...", "condition": "...",
#   "min_price": 11000,
#   "shipping_method": "らくらくメルカリ便",
#   "shipping_days": "1-2日で発送",
#   "notes": "..."
# }
REQUIRED_KEYS = ("title", "description", "price")


def import_listing_json(db: MercariDatabase, raw: str | dict[str, Any]) -> dict[str, Any]:
    """ChatGPTが生成した出品用JSONを下書き（draft）として取り込む。

    本出品はしない。取り込んだ下書きをユーザーが確認してメルカリへ手動で出品する。
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONとして読み込めません: {exc}") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise ValueError("JSONオブジェクト（{...}）を渡してください")

    missing = [key for key in REQUIRED_KEYS if not data.get(key)]
    if missing:
        raise ValueError(f"必須項目が不足しています: {', '.join(missing)}")

    warnings: list[str] = []
    item_id = data.get("item_id")
    if item_id:
        item = db.get_item(int(item_id))
        if not item:
            raise ValueError(f"item {item_id} が見つかりません")
        item_id = int(item_id)
        # ChatGPT側で確定した属性は商品マスタにも反映する（未入力の項目のみ）
        updates = {"id": item_id}
        for src, dest in (("category", "category"), ("brand", "brand"),
                          ("condition", "condition"), ("min_price", "min_price"),
                          ("notes", "notes")):
            if data.get(src) and not item.get(dest):
                updates[dest] = data[src]
        if len(updates) > 1:
            db.upsert_item(updates)
    else:
        if not data.get("name"):
            raise ValueError("item_idまたはnameのどちらかが必要です")
        item_id = db.upsert_item({
            "name": data["name"],
            "status": "purchased",
            "category": data.get("category"),
            "brand": data.get("brand"),
            "condition": data.get("condition"),
            "min_price": data.get("min_price"),
            "notes": data.get("notes"),
        })
        warnings.append(f"商品を新規作成しました（item {item_id}）。仕入れ価格を登録してください")

    item = db.get_item(item_id)
    min_price = data.get("min_price") or item.get("min_price")
    if min_price and int(data["price"]) < int(min_price):
        warnings.append(
            f"出品価格{int(data['price']):,}円が最低販売価格{int(min_price):,}円を下回っています"
        )

    listing_id = db.upsert_listing({
        "item_id": item_id,
        "status": "draft",
        "title": str(data["title"]),
        "description": str(data["description"]),
        "category": data.get("category"),
        "condition_label": data.get("condition"),
        "list_price": int(data["price"]),
        "current_price": int(data["price"]),
        "shipping_method": data.get("shipping_method"),
        "shipping_days": data.get("shipping_days"),
    })
    return {
        "listing_id": listing_id,
        "item_id": item_id,
        "warnings": warnings,
        "message": "下書きとして保存しました。内容を確認してからメルカリで本出品してください。",
    }
