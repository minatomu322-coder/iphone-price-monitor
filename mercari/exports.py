from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mercari.db import MercariDatabase, today_jst
from mercari.decision import is_stale, primary_judgement
from mercari.profit import DEFAULT_FEE_RATE, breakeven_price, estimate_profit


FORMATS = ("text", "json", "csv", "markdown")

# ChatGPTへ貼り付けたときに、そのまま依頼として機能するよう先頭に付ける文章
PREAMBLES = {
    "sourcing": (
        "以下は仕入れ候補のデータです。同一商品か・状態差・型番違い・セット数・付属品差・"
        "送料と手数料の妥当性・相場件数・外れ値・偽物リスク・価格下落リスク・回転率・資金拘束を確認し、"
        "「買い / 条件付きで買い / 見送り / 追加確認が必要」のいずれかを理由付きで返してください。"
    ),
    "listing": (
        "以下のデータと（別途送付する）商品画像をもとに、メルカリ出品用の"
        "「検索されやすいタイトル」「自然で誇張のない説明文」「カテゴリー候補」「価格3案（早売り/相場/強気）」を"
        "作成してください。画像やデータで確認できない内容（美品・未使用・正規品など）は書かないでください。"
    ),
    "stale": (
        "以下は売れ残っている出品のデータです。値下げだけでなく、まず売れない理由を分析し、"
        "タイトル・説明文・1枚目写真・追加撮影・価格・セット/バラ売り・他販路・カテゴリー・検索キーワード・"
        "損切りor保有継続の観点で改善案を出してください。"
    ),
    "sales": (
        "以下は販売実績の集計データです。利益が出ているカテゴリー/仕入れ先、赤字や資金効率の悪い在庫、"
        "値下げしすぎ・仕入れ高すぎの傾向を分析し、増やすべきジャンル・撤退すべきジャンル・翌月の具体的な改善行動を"
        "提案してください。"
    ),
}


@dataclass
class ExportPayload:
    kind: str
    title: str
    preamble: str
    fields: list[tuple[str, Any, str]] = field(default_factory=list)  # (項目, 生値, 表示値)
    tables: list[dict[str, Any]] = field(default_factory=list)  # {name, headers, rows}


def fmt_yen(value: Any) -> str:
    if value is None or value == "":
        return "未入力"
    return f"{int(value):,}円"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{round(value * 100, 1)}%"


def _text(value: Any) -> str:
    if value is None or value == "":
        return "未入力"
    return str(value)


def _days_between(start: str | None, end: str | None = None) -> int | None:
    if not start:
        return None
    try:
        start_date = date.fromisoformat(str(start)[:10])
    except ValueError:
        return None
    end_date = date.fromisoformat((end or today_jst())[:10])
    return (end_date - start_date).days


def _f(payload: ExportPayload, label: str, raw: Any, display: str | None = None) -> None:
    payload.fields.append((label, raw, display if display is not None else _text(raw)))


# ---------------------------------------------------------------- 仕入れ判断用


def sourcing_payload(db: MercariDatabase, item_id: int, config: dict[str, Any]) -> ExportPayload:
    item = _require_item(db, item_id)
    market = db.latest_market(item_id)
    history = db.market_history(item_id)
    judgement = primary_judgement(item, market, history, config)
    fee_rate = float(config.get("fees", {}).get("default_rate", DEFAULT_FEE_RATE))

    payload = ExportPayload(
        kind="sourcing",
        title=f"仕入れ判断用データ：{item['name']}",
        preamble=PREAMBLES["sourcing"],
    )
    planned = item.get("planned_price") or (market or {}).get("median_price")
    est = None
    if planned and item.get("purchase_price") is not None:
        est = estimate_profit(
            int(planned),
            purchase_price=int(item["purchase_price"]),
            purchase_shipping=int(item.get("purchase_shipping") or 0),
            sell_shipping=int(item.get("shipping_cost") or 0),
            fee_rate=fee_rate,
        )

    _f(payload, "商品名", item["name"])
    _f(payload, "型番", item.get("model_number"))
    _f(payload, "JANコード", item.get("jan_code"))
    _f(payload, "仕入れ価格", item.get("purchase_price"), fmt_yen(item.get("purchase_price")))
    _f(payload, "仕入れ送料", item.get("purchase_shipping"), fmt_yen(item.get("purchase_shipping")))
    _f(payload, "想定販売価格", planned, fmt_yen(planned))
    _f(payload, "販売手数料", est.fee if est else None, fmt_yen(est.fee) if est else "未計算")
    _f(payload, "販売送料", item.get("shipping_cost"), fmt_yen(item.get("shipping_cost")))
    _f(payload, "想定利益", est.profit if est else None,
       f"{est.profit:+,}円" if est else "未計算")
    _f(payload, "ROI", est.roi if est else None, fmt_pct(est.roi) if est else "未計算")
    _f(payload, "損益分岐価格",
       breakeven_price(
           int(item.get("purchase_price") or 0),
           int(item.get("purchase_shipping") or 0),
           int(item.get("shipping_cost") or 0),
           fee_rate=fee_rate,
       ) if item.get("purchase_price") is not None else None,
       fmt_yen(breakeven_price(
           int(item.get("purchase_price") or 0),
           int(item.get("purchase_shipping") or 0),
           int(item.get("shipping_cost") or 0),
           fee_rate=fee_rate,
       )) if item.get("purchase_price") is not None else "未計算")
    _f(payload, "相場最安値", (market or {}).get("min_price"), fmt_yen((market or {}).get("min_price")))
    _f(payload, "相場中央値", (market or {}).get("median_price"), fmt_yen((market or {}).get("median_price")))
    _f(payload, "相場平均値", (market or {}).get("mean_price"), fmt_yen((market or {}).get("mean_price")))
    _f(payload, "売り切れ件数", (market or {}).get("sold_count"))
    _f(payload, "販売中件数", (market or {}).get("active_count"))
    _f(payload, "状態", item.get("condition"))
    _f(payload, "付属品", item.get("accessories"))
    _f(payload, "傷・欠品", item.get("flaws"))
    _f(payload, "仕入れ元URL", item.get("purchase_url"))
    _f(payload, "相場商品URL", (market or {}).get("url"))
    price_history = " / ".join(
        f"{str(row['captured_at'])[:10]} 中央値{fmt_yen(row.get('median_price'))}"
        for row in history if row.get("median_price") is not None
    )
    _f(payload, "価格履歴", price_history or None, price_history or "履歴なし")
    _f(payload, "注意点", item.get("notes"))
    judgement_text = f"{judgement['label']}：" + "、".join(judgement["reasons"])
    if judgement["warnings"]:
        judgement_text += "／注意：" + "、".join(judgement["warnings"])
    _f(payload, "システムの一次判定", judgement["label"], judgement_text)

    if judgement.get("ladder"):
        rows = []
        labels = {"quick": "早売り価格", "standard": "相場価格", "strong": "強気価格"}
        for key in ("quick", "standard", "strong"):
            entry = judgement["ladder"][key]
            rows.append([
                labels[key],
                fmt_yen(entry["price"]),
                fmt_yen(entry["fee"]),
                f"{entry['profit']:+,}円",
                fmt_pct(entry["roi"]),
            ])
        payload.tables.append({
            "name": "価格候補（システム試算・最終判断はChatGPTとユーザー）",
            "headers": ["区分", "販売価格", "手数料", "想定利益", "ROI"],
            "rows": rows,
        })
    return payload


# ---------------------------------------------------------------- 出品作成用


def listing_payload(db: MercariDatabase, item_id: int, config: dict[str, Any]) -> ExportPayload:
    item = _require_item(db, item_id)
    market = db.latest_market(item_id)
    history = db.market_history(item_id)

    payload = ExportPayload(
        kind="listing",
        title=f"出品作成用データ：{item['name']}",
        preamble=PREAMBLES["listing"],
    )
    _f(payload, "商品画像", item.get("images_note"),
       _text(item.get("images_note")) if item.get("images_note")
       else "このメッセージに商品画像を添付してください")
    _f(payload, "商品名候補", item["name"])
    _f(payload, "型番候補", item.get("model_number"))
    _f(payload, "カテゴリー候補", item.get("category"))
    _f(payload, "ブランド候補", item.get("brand"))
    _f(payload, "状態", item.get("condition"))
    _f(payload, "傷や欠品", item.get("flaws"))
    _f(payload, "付属品", item.get("accessories"))
    _f(payload, "仕入れ価格", item.get("purchase_price"), fmt_yen(item.get("purchase_price")))
    market_text = "未登録"
    if market:
        market_text = (
            f"最安{fmt_yen(market.get('min_price'))} / 中央値{fmt_yen(market.get('median_price'))} / "
            f"平均{fmt_yen(market.get('mean_price'))}（売り切れ{_text(market.get('sold_count'))}件・"
            f"販売中{_text(market.get('active_count'))}件）"
        )
    _f(payload, "相場データ", market, market_text)
    _f(payload, "希望販売方針", item.get("sales_policy"),
       _text(item.get("sales_policy")) if item.get("sales_policy") else "相場価格で販売")
    _f(payload, "発送方法", item.get("shipping_method"))
    _f(payload, "発送日数", item.get("shipping_days"))
    _f(payload, "最低販売価格", item.get("min_price"), fmt_yen(item.get("min_price")))
    _f(payload, "注意点", item.get("notes"))

    if history:
        payload.tables.append({
            "name": "相場推移",
            "headers": ["取得日", "最安値", "中央値", "売り切れ件数", "販売中件数"],
            "rows": [
                [
                    str(row["captured_at"])[:10],
                    fmt_yen(row.get("min_price")),
                    fmt_yen(row.get("median_price")),
                    _text(row.get("sold_count")),
                    _text(row.get("active_count")),
                ]
                for row in history
            ],
        })
    return payload


# ---------------------------------------------------------------- 売れ残り分析用


def stale_payload(db: MercariDatabase, item_id: int, config: dict[str, Any]) -> ExportPayload:
    item = _require_item(db, item_id)
    listing = db.active_listing(item_id)
    if not listing:
        raise ValueError(f"item {item_id} に出品情報がありません（先に出品を登録してください）")
    market = db.latest_market(item_id)
    history = db.market_history(item_id)
    fee_rate = float(config.get("fees", {}).get("default_rate", DEFAULT_FEE_RATE))

    days = _days_between(listing.get("listed_at"))
    current_price = listing.get("current_price") or listing.get("list_price")
    est = None
    if current_price and item.get("purchase_price") is not None:
        est = estimate_profit(
            int(current_price),
            purchase_price=int(item["purchase_price"]),
            purchase_shipping=int(item.get("purchase_shipping") or 0),
            sell_shipping=int(item.get("shipping_cost") or 0),
            fee_rate=fee_rate,
        )

    payload = ExportPayload(
        kind="stale",
        title=f"売れ残り分析用データ：{item['name']}",
        preamble=PREAMBLES["stale"],
    )
    _f(payload, "商品名", item["name"])
    _f(payload, "出品日", (listing.get("listed_at") or "")[:10] or None)
    _f(payload, "経過日数", days, f"{days}日" if days is not None else "未入力")
    _f(payload, "出品価格", listing.get("list_price"), fmt_yen(listing.get("list_price")))
    _f(payload, "現在価格", current_price, fmt_yen(current_price))
    _f(payload, "閲覧数", listing.get("views"))
    _f(payload, "いいね数", listing.get("likes"))
    _f(payload, "コメント数", listing.get("comments"))
    changes = db.price_changes_for_listing(int(listing["id"]))
    change_text = " / ".join(
        f"{str(c['changed_at'])[:10]} {fmt_yen(c.get('old_price'))}→{fmt_yen(c['new_price'])}"
        + (f"（{c['reason']}）" if c.get("reason") else "")
        for c in changes
    )
    _f(payload, "値下げ履歴", change_text or None, change_text or "値下げなし")
    market_trend = " / ".join(
        f"{str(row['captured_at'])[:10]} 中央値{fmt_yen(row.get('median_price'))}"
        for row in history if row.get("median_price") is not None
    )
    _f(payload, "相場推移", market_trend or None, market_trend or "未登録")
    _f(payload, "現在の相場中央値", (market or {}).get("median_price"),
       fmt_yen((market or {}).get("median_price")))
    _f(payload, "仕入れ価格", item.get("purchase_price"), fmt_yen(item.get("purchase_price")))
    _f(payload, "最低販売価格", item.get("min_price"), fmt_yen(item.get("min_price")))
    _f(payload, "現在の想定利益", est.profit if est else None,
       f"{est.profit:+,}円" if est else "未計算")
    _f(payload, "タイトル", listing.get("title"))
    _f(payload, "商品説明", listing.get("description"))
    _f(payload, "画像一覧", item.get("images_note"),
       _text(item.get("images_note")) if item.get("images_note")
       else "画像はこのメッセージに添付してください")
    improvements = db.improvements_for_item(item_id)
    imp_text = " / ".join(
        f"{str(i['applied_at'])[:10]} {i['kind']}：{i.get('detail') or ''}"
        + (f"（結果：{i['result']}）" if i.get("result") else "")
        for i in improvements
    )
    _f(payload, "過去に実施した改善", imp_text or None, imp_text or "改善履歴なし")
    stale_flag = is_stale(listing, days, config)
    _f(payload, "システムの売れ残り判定", stale_flag,
       "売れ残り（基準日数超過）" if stale_flag else "基準日数内")
    return payload


# ---------------------------------------------------------------- 売上分析用


def sales_payload(
    db: MercariDatabase,
    date_from: str,
    date_to: str,
    config: dict[str, Any],
) -> ExportPayload:
    sales = db.sales_between(date_from, date_to)
    thresholds = config.get("thresholds", {})
    long_stock_days = int(thresholds.get("long_stock_days", 30))

    payload = ExportPayload(
        kind="sales",
        title=f"売上分析用データ：{date_from} 〜 {date_to}",
        preamble=PREAMBLES["sales"],
    )

    revenue = sum(s["sold_price"] for s in sales)
    per_sale: list[dict[str, Any]] = []
    for s in sales:
        cost = int(s.get("purchase_price") or 0) + int(s.get("purchase_shipping") or 0)
        profit = (
            int(s["sold_price"]) - int(s["sales_fee"])
            - int(s["shipping_cost"]) - int(s["other_cost"]) - cost
        )
        days_to_sell = _days_between(s.get("purchased_at"), s.get("sold_at"))
        per_sale.append({**s, "cost": cost, "profit": profit, "days_to_sell": days_to_sell})
    total_profit = sum(s["profit"] for s in per_sale)
    total_cost = sum(s["cost"] for s in per_sale)
    margin = total_profit / revenue if revenue else None
    roi = total_profit / total_cost if total_cost else None
    turn_days = [s["days_to_sell"] for s in per_sale if s["days_to_sell"] is not None]
    avg_turn = round(sum(turn_days) / len(turn_days), 1) if turn_days else None

    # 在庫（未売却）状況
    stock_items = [i for i in db.list_items() if i["status"] in ("purchased", "listed")]
    stock_value = sum(
        int(i.get("purchase_price") or 0) + int(i.get("purchase_shipping") or 0)
        for i in stock_items
    )
    long_stock = [
        i for i in stock_items
        if (_days_between(i.get("purchased_at")) or 0) >= long_stock_days
        and i.get("purchased_at")
    ]

    _f(payload, "期間", f"{date_from}〜{date_to}")
    _f(payload, "売上", revenue, fmt_yen(revenue))
    _f(payload, "実利益", total_profit, f"{total_profit:+,}円")
    _f(payload, "粗利率", margin, fmt_pct(margin))
    _f(payload, "ROI", roi, fmt_pct(roi))
    _f(payload, "販売件数", len(sales), f"{len(sales)}件")
    _f(payload, "在庫数", len(stock_items), f"{len(stock_items)}点")
    _f(payload, "未回収在庫金額", stock_value, fmt_yen(stock_value))
    _f(payload, "平均回転日数", avg_turn, f"{avg_turn}日" if avg_turn is not None else "計測不可")

    def group_table(name: str, key: str, label: str) -> None:
        groups: dict[str, dict[str, int]] = {}
        for s in per_sale:
            group = s.get(key) or "未分類"
            bucket = groups.setdefault(group, {"count": 0, "revenue": 0, "profit": 0})
            bucket["count"] += 1
            bucket["revenue"] += int(s["sold_price"])
            bucket["profit"] += int(s["profit"])
        rows = [
            [g, f"{v['count']}件", fmt_yen(v["revenue"]), f"{v['profit']:+,}円"]
            for g, v in sorted(groups.items(), key=lambda kv: -kv[1]["profit"])
        ]
        if rows:
            payload.tables.append({
                "name": name,
                "headers": [label, "件数", "売上", "利益"],
                "rows": rows,
            })

    group_table("カテゴリー別実績", "category", "カテゴリー")
    group_table("仕入れ先別実績", "purchase_source", "仕入れ先")
    group_table("販売先別実績", "channel", "販売先")

    if long_stock:
        payload.tables.append({
            "name": f"長期在庫（仕入れから{long_stock_days}日以上）",
            "headers": ["商品名", "状態", "仕入れ日", "経過日数", "仕入れ総額"],
            "rows": [
                [
                    i["name"],
                    i["status"],
                    (i.get("purchased_at") or "")[:10],
                    f"{_days_between(i.get('purchased_at'))}日",
                    fmt_yen(int(i.get("purchase_price") or 0) + int(i.get("purchase_shipping") or 0)),
                ]
                for i in long_stock
            ],
        })

    losers = [s for s in per_sale if s["profit"] < 0]
    if losers:
        payload.tables.append({
            "name": "赤字商品",
            "headers": ["商品名", "売却日", "売却価格", "利益"],
            "rows": [
                [s["item_name"], str(s["sold_at"])[:10], fmt_yen(s["sold_price"]), f"{s['profit']:+,}円"]
                for s in sorted(losers, key=lambda s: s["profit"])
            ],
        })

    ranked = sorted(per_sale, key=lambda s: -s["profit"])
    if ranked:
        payload.tables.append({
            "name": "利益上位商品（最大5件）",
            "headers": ["商品名", "売却日", "売却価格", "利益", "回転日数"],
            "rows": [
                [
                    s["item_name"], str(s["sold_at"])[:10], fmt_yen(s["sold_price"]),
                    f"{s['profit']:+,}円",
                    f"{s['days_to_sell']}日" if s["days_to_sell"] is not None else "-",
                ]
                for s in ranked[:5]
            ],
        })
        payload.tables.append({
            "name": "利益下位商品（最大5件）",
            "headers": ["商品名", "売却日", "売却価格", "利益", "回転日数"],
            "rows": [
                [
                    s["item_name"], str(s["sold_at"])[:10], fmt_yen(s["sold_price"]),
                    f"{s['profit']:+,}円",
                    f"{s['days_to_sell']}日" if s["days_to_sell"] is not None else "-",
                ]
                for s in ranked[-5:][::-1]
            ],
        })
    return payload


# ---------------------------------------------------------------- レンダリング


def render(payload: ExportPayload, fmt: str) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"未対応の形式: {fmt}（text/json/csv/markdownのいずれか）")
    if fmt == "text":
        return _render_text(payload)
    if fmt == "markdown":
        return _render_markdown(payload)
    if fmt == "json":
        return _render_json(payload)
    return _render_csv(payload)


def _render_text(payload: ExportPayload) -> str:
    lines = [f"【{payload.title}】", payload.preamble, ""]
    for label, _raw, display in payload.fields:
        lines.append(f"{label}：{display}")
    for table in payload.tables:
        lines.append("")
        lines.append(f"■ {table['name']}")
        for row in table["rows"]:
            lines.append("- " + " / ".join(
                f"{header}:{cell}" for header, cell in zip(table["headers"], row)
            ))
    return "\n".join(lines)


def _render_markdown(payload: ExportPayload) -> str:
    lines = [f"## {payload.title}", "", payload.preamble, ""]
    lines.append("| 項目 | 内容 |")
    lines.append("| --- | --- |")
    for label, _raw, display in payload.fields:
        lines.append(f"| {label} | {_md_escape(display)} |")
    for table in payload.tables:
        lines.append("")
        lines.append(f"### {table['name']}")
        lines.append("| " + " | ".join(table["headers"]) + " |")
        lines.append("|" + " --- |" * len(table["headers"]))
        for row in table["rows"]:
            lines.append("| " + " | ".join(_md_escape(str(cell)) for cell in row) + " |")
    return "\n".join(lines)


def _md_escape(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _render_json(payload: ExportPayload) -> str:
    data = {
        "種別": payload.kind,
        "タイトル": payload.title,
        "依頼": payload.preamble,
        "項目": {label: raw for label, raw, _display in payload.fields},
        "明細": {
            table["name"]: {"headers": table["headers"], "rows": table["rows"]}
            for table in payload.tables
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _render_csv(payload: ExportPayload) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["タイトル", payload.title])
    writer.writerow(["依頼", payload.preamble])
    writer.writerow([])
    writer.writerow(["項目", "内容"])
    for label, _raw, display in payload.fields:
        writer.writerow([label, display])
    for table in payload.tables:
        writer.writerow([])
        writer.writerow([table["name"]])
        writer.writerow(table["headers"])
        for row in table["rows"]:
            writer.writerow(row)
    return buf.getvalue()


def build_payload(
    db: MercariDatabase,
    kind: str,
    config: dict[str, Any],
    item_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> ExportPayload:
    if kind == "sourcing":
        return sourcing_payload(db, _require_id(item_id), config)
    if kind == "listing":
        return listing_payload(db, _require_id(item_id), config)
    if kind == "stale":
        return stale_payload(db, _require_id(item_id), config)
    if kind == "sales":
        date_to = date_to or today_jst()
        if not date_from:
            date_from = f"{date_to[:7]}-01"  # デフォルトは当月頭から
        return sales_payload(db, date_from, date_to, config)
    raise ValueError(f"未対応の出力種別: {kind}（sourcing/listing/stale/salesのいずれか）")


def _require_item(db: MercariDatabase, item_id: int) -> dict[str, Any]:
    item = db.get_item(item_id)
    if not item:
        raise ValueError(f"item {item_id} が見つかりません")
    return item


def _require_id(item_id: int | None) -> int:
    if not item_id:
        raise ValueError("item_idを指定してください")
    return int(item_id)
