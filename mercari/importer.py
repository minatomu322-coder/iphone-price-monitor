from __future__ import annotations

from typing import Any

from mercari.db import MercariDatabase
from mercari.gpt_schemas import (
    parse_json_object,
    validate_item_info,
    validate_listing_draft,
)


# AI応答JSONのスキーマ定義と解析はgpt_schemas.pyに一元化されている。
# このモジュールは「検証済みデータをDBへ反映する」ことだけを担当する。
#
# 出品用JSON（mercari_listing_draft）:
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

# クイック登録（URL→ChatGPT解析→JSON取り込み）で受け取る商品情報JSONのスキーマ。
# {
#   "type": "mercari_item_info",
#   "item_id": 3,                  // 既存商品を更新する場合のみ
#   "name": "商品名",               // 新規作成時は必須
#   "model_number": "...", "jan_code": "...", "brand": "...", "category": "...",
#   "condition": "...", "accessories": "...", "flaws": "...", "notes": "...",
#   "purchase_price": 5000, "purchase_shipping": 300,
#   "purchase_url": "...", "purchase_source": "...",
#   "market": {                    // ChatGPTが相場を調べられた場合のみ
#     "min_price": 8000, "median_price": 9000, "mean_price": 9200,
#     "sold_count": 15, "active_count": 20, "url": "...", "notes": "外れ値2件除外"
#   }
# }
ITEM_INFO_FIELDS = (
    "name", "model_number", "jan_code", "brand", "category", "condition",
    "accessories", "flaws", "notes", "purchase_price", "purchase_shipping",
    "purchase_url", "purchase_source", "planned_price", "min_price",
    "shipping_method", "shipping_cost", "shipping_days",
)


def import_item_json(
    db: MercariDatabase, raw: str | dict[str, Any], config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """AIが解析した商品情報JSONを仕入れ候補として取り込む。

    相場（market）が含まれていれば相場スナップショットも保存し、
    AIの一次見解（verdict/confidence）があれば判断履歴にも記録して、
    一次判定まで済ませて返す。購入・出品などの操作は行わない。
    """
    data = validate_item_info(parse_json_object(raw))
    warnings: list[str] = []

    item_id = data.get("item_id")
    values = {key: data.get(key) for key in ITEM_INFO_FIELDS if data.get(key) is not None}
    if item_id:
        if not db.get_item(int(item_id)):
            raise ValueError(f"item {item_id} が見つかりません")
        db.upsert_item({"id": int(item_id), **values})
        item_id = int(item_id)
    else:
        if not values.get("name"):
            raise ValueError("nameは必須です（商品を特定できませんでした）")
        values.setdefault("status", "candidate")
        item_id = db.upsert_item(values)
    if values.get("purchase_price") is None and not item_id:
        warnings.append("仕入れ価格が未入力のため一次判定できません")

    market = data.get("market")
    if isinstance(market, dict) and market.get("median_price") is not None:
        db.insert_market_snapshot({
            "item_id": item_id,
            "source": market.get("source") or "ChatGPT調査",
            **{k: market.get(k) for k in (
                "min_price", "median_price", "mean_price", "max_price",
                "sold_count", "active_count", "url", "notes",
            )},
        })
    elif market:
        warnings.append("market.median_priceが無いため相場は保存しませんでした")

    # AIの一次見解が含まれていれば判断履歴として保存（後で自動答え合わせされる）
    if data.get("verdict"):
        db.add_gpt_review({
            "item_id": item_id,
            "kind": "sourcing",
            "verdict": data["verdict"],
            "confidence": data.get("confidence"),
            "summary": "クイック登録時のAI一次見解",
        })

    # 取り込み直後に一次判定まで返す（画面・CLIでそのまま確認できる）
    from mercari.decision import primary_judgement

    item = db.get_item(item_id)
    judgement = primary_judgement(
        item, db.latest_market(item_id), db.market_history(item_id), config or {}
    )
    return {
        "item_id": item_id,
        "warnings": warnings,
        "judgement": {k: v for k, v in judgement.items() if k != "ladder"},
        "message": (
            f"仕入れ候補として保存しました（item {item_id}）。"
            f"一次判定: {judgement['label']}"
        ),
    }


def import_listing_json(db: MercariDatabase, raw: str | dict[str, Any]) -> dict[str, Any]:
    """ChatGPTが生成した出品用JSONを下書き（draft）として取り込む。

    本出品はしない。取り込んだ下書きをユーザーが確認してメルカリへ手動で出品する。
    """
    data = validate_listing_draft(parse_json_object(raw))
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
