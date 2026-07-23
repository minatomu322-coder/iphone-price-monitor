from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from mercari.db import MercariDatabase, today_jst
from mercari.decision import is_stale, primary_judgement
from mercari.exports import build_payload, render, _days_between
from mercari.importer import import_listing_json
from mercari.kpi import days_in_stock, inventory_aging, item_capital, kpi_dashboard
from mercari.profit import DEFAULT_FEE_RATE, estimate_profit


BASE_DIR = Path(__file__).resolve().parent.parent
HOST = "0.0.0.0"
DEFAULT_PORT = 8766


def load_config() -> dict[str, Any]:
    with (BASE_DIR / "mercari_config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def open_db(config: dict[str, Any]) -> MercariDatabase:
    db_path = Path(config.get("database", {}).get("path", "mercari.sqlite3"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return MercariDatabase(db_path)


def state_data() -> dict[str, Any]:
    config = load_config()
    db = open_db(config)
    fee_rate = float(config.get("fees", {}).get("default_rate", DEFAULT_FEE_RATE))

    items = []
    for item in db.list_items():
        market = db.latest_market(item["id"])
        history = db.market_history(item["id"])
        listing = db.active_listing(item["id"])
        judgement = primary_judgement(item, market, history, config)
        days_listed = _days_between(listing.get("listed_at")) if listing else None
        stale = is_stale(listing, days_listed, config) if listing else False
        current_price = None
        profit = None
        if listing:
            current_price = listing.get("current_price") or listing.get("list_price")
        if current_price is None:
            current_price = item.get("planned_price") or (market or {}).get("median_price")
        if current_price and item.get("purchase_price") is not None:
            profit = estimate_profit(
                int(current_price),
                purchase_price=int(item["purchase_price"]),
                purchase_shipping=int(item.get("purchase_shipping") or 0),
                sell_shipping=int(item.get("shipping_cost") or 0),
                fee_rate=fee_rate,
            ).profit
        latest_review = db.latest_gpt_verdict(item["id"])
        items.append({
            "gpt_verdict": (
                {k: latest_review[k] for k in ("created_at", "kind", "verdict", "summary")}
                if latest_review else None
            ),
            "gpt_review_count": len(db.gpt_reviews_for_item(item["id"], limit=100)),
            "days_in_stock": (
                days_in_stock(item) if item["status"] in ("purchased", "listed") else None
            ),
            "capital": (
                item_capital(item) if item["status"] in ("purchased", "listed") else None
            ),
            **item,
            "market": market,
            "judgement": {k: v for k, v in judgement.items() if k != "ladder"},
            "ladder": judgement.get("ladder"),
            "listing": listing,
            "days_listed": days_listed,
            "stale": stale,
            "market_trend": [
                row["median_price"] for row in history if row.get("median_price") is not None
            ],
            "reference_price": current_price,
            "estimated_profit": profit,
        })

    stock = [i for i in items if i["status"] in ("purchased", "listed")]
    stock_value = sum(item_capital(i) for i in stock)
    aging = inventory_aging(stock)
    month_start = f"{today_jst()[:7]}-01"
    month_sales = db.sales_between(month_start, today_jst())
    month_revenue = sum(s["sold_price"] for s in month_sales)
    month_profit = sum(
        int(s["sold_price"]) - int(s["sales_fee"]) - int(s["shipping_cost"])
        - int(s["other_cost"])
        - int(s.get("purchase_price") or 0) - int(s.get("purchase_shipping") or 0)
        for s in month_sales
    )
    return {
        "today": today_jst(),
        "items": items,
        "summary": {
            "candidates": sum(1 for i in items if i["status"] == "candidate"),
            "stock_count": len(stock),
            "stock_value": stock_value,
            "listed_count": sum(1 for i in items if i["status"] == "listed"),
            "stale_count": sum(1 for i in items if i["stale"]),
            "month_sales_count": len(month_sales),
            "month_revenue": month_revenue,
            "month_profit": month_profit,
            "aging": aging,
        },
    }


class MercariHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(INDEX_HTML)
            return
        if parsed.path == "/api/state":
            self.send_json({"ok": True, "data": state_data()})
            return
        if parsed.path == "/api/export":
            self.handle_export(parse_qs(parsed.query))
            return
        if parsed.path == "/api/kpi":
            config = load_config()
            db = open_db(config)
            query = parse_qs(parsed.query)
            months = int((query.get("months") or ["6"])[0])
            self.send_json({"ok": True, "data": kpi_dashboard(db, months=months)})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_export(self, query: dict[str, list[str]]) -> None:
        try:
            config = load_config()
            db = open_db(config)
            kind = (query.get("kind") or [""])[0]
            fmt = (query.get("format") or ["text"])[0]
            item_id = (query.get("item_id") or [None])[0]
            payload = build_payload(
                db,
                kind,
                config,
                item_id=int(item_id) if item_id else None,
                date_from=(query.get("from") or [None])[0],
                date_to=(query.get("to") or [None])[0],
            )
            self.send_json({"ok": True, "kind": kind, "format": fmt, "text": render(payload, fmt)})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "JSONを解釈できません"}, status=400)
            return
        try:
            config = load_config()
            db = open_db(config)
            if path == "/api/item":
                item_id = db.upsert_item(body)
                self.send_json({"ok": True, "item_id": item_id})
            elif path == "/api/item-status":
                db.set_item_status(int(body["item_id"]), str(body["status"]))
                self.send_json({"ok": True})
            elif path == "/api/market":
                snapshot_id = db.insert_market_snapshot(body)
                self.send_json({"ok": True, "snapshot_id": snapshot_id})
            elif path == "/api/listing":
                listing_id = db.upsert_listing(body)
                if body.get("status") == "active":
                    db.set_item_status(int(body["item_id"]), "listed")
                self.send_json({"ok": True, "listing_id": listing_id})
            elif path == "/api/price-change":
                db.record_price_change(
                    int(body["listing_id"]), int(body["new_price"]), body.get("reason")
                )
                self.send_json({"ok": True})
            elif path == "/api/sale":
                sale_id = db.record_sale(body)
                self.send_json({"ok": True, "sale_id": sale_id})
            elif path == "/api/improvement":
                improvement_id = db.add_improvement(body)
                self.send_json({"ok": True, "improvement_id": improvement_id})
            elif path == "/api/improvement-status":
                db.update_improvement_status(
                    int(body["improvement_id"]), str(body["status"]), body.get("result")
                )
                self.send_json({"ok": True})
            elif path == "/api/gpt-review":
                review_id = db.add_gpt_review(body)
                self.send_json({"ok": True, "review_id": review_id})
            elif path == "/api/unsold-reason":
                reason_id = db.add_unsold_reason(body)
                self.send_json({"ok": True, "reason_id": reason_id})
            elif path == "/api/import-listing":
                result = import_listing_json(db, body.get("json") or body)
                self.send_json({"ok": True, **result})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, data: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


INDEX_HTML = r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>メルカリ販売管理</title>
  <style>
    :root {
      --bg: #f6f7f9; --panel: #fff; --ink: #1f2933; --muted: #667085;
      --line: #d9dee7; --green: #0f8a5f; --red: #c2413a; --blue: #2764b3; --orange: #b65f16;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif; }
    header { padding: 16px 22px; background: #fff; border-bottom: 1px solid var(--line);
      display: flex; justify-content: space-between; align-items: center; gap: 12px;
      position: sticky; top: 0; z-index: 2; flex-wrap: wrap; }
    h1 { margin: 0; font-size: 19px; }
    .meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
    main { max-width: 1180px; margin: 0 auto; padding: 20px; }
    nav { display: flex; gap: 8px; flex-wrap: wrap; }
    nav button { min-width: 0; }
    button { appearance: none; border: 1px solid #204f91; background: var(--blue); color: #fff;
      padding: 8px 12px; border-radius: 6px; font-weight: 700; cursor: pointer; font-size: 13px; }
    button.ghost { background: #fff; color: var(--blue); }
    button.small { padding: 5px 8px; font-size: 12px; }
    button:disabled { opacity: .5; }
    .status { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 10px; margin-bottom: 16px; }
    .metric { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
    .metric-label { color: var(--muted); font-size: 12px; }
    .metric-value { font-size: 20px; font-weight: 800; margin-top: 4px; }
    section.tab { display: none; }
    section.tab.active { display: block; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
      padding: 14px; margin-bottom: 14px; }
    .item-row { border-top: 1px solid #edf0f4; padding: 12px 0; }
    .item-row:first-child { border-top: 0; }
    .item-head { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; align-items: baseline; }
    .item-name { font-weight: 800; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px;
      font-weight: 700; background: #eef2f7; color: var(--muted); margin-left: 6px; }
    .badge.buy { background: #e2f4ec; color: var(--green); }
    .badge.skip { background: #fbe9e7; color: var(--red); }
    .badge.stale { background: #fdf0e3; color: var(--orange); }
    .item-detail { color: var(--muted); font-size: 12px; margin: 4px 0 8px; line-height: 1.6; }
    .copy-actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .plus { color: var(--green); } .minus { color: var(--red); }
    label { display: block; font-size: 12px; color: var(--muted); margin: 8px 0 3px; }
    input, select, textarea { width: 100%; padding: 8px; border: 1px solid var(--line);
      border-radius: 6px; font-size: 13px; font-family: inherit; background: #fff; }
    textarea { min-height: 84px; }
    .form-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 0 14px; }
    .form-grid .wide { grid-column: 1 / -1; }
    .form-actions { margin-top: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.6; }
    h2 { font-size: 15px; margin: 0 0 10px; }
    pre.preview { background: #f2f4f8; border: 1px solid var(--line); border-radius: 6px;
      padding: 10px; font-size: 12px; white-space: pre-wrap; max-height: 320px; overflow: auto; }
    #toast { position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
      background: #1f2933; color: #fff; padding: 10px 16px; border-radius: 8px; font-size: 13px;
      opacity: 0; transition: opacity .25s; pointer-events: none; z-index: 10; }
    #toast.show { opacity: 1; }
    @media (max-width: 860px) {
      .status { grid-template-columns: repeat(2, minmax(0,1fr)); }
      .form-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>メルカリ販売管理</h1>
      <div class="meta">ChatGPT＝判断・文章 ／ このシステム＝記録・計算・出力 ／ あなた＝最終決定</div>
    </div>
    <nav>
      <button data-tab="items">商品一覧</button>
      <button class="ghost" data-tab="input">登録・入力</button>
      <button class="ghost" data-tab="kpi">KPI</button>
      <button class="ghost" data-tab="sales">売上分析</button>
      <button class="ghost" data-tab="import">出品JSON取り込み</button>
    </nav>
  </header>
  <main>
    <section class="status" id="metrics"></section>

    <section class="tab active" id="tab-items">
      <div class="card">
        <div class="copy-actions" style="margin-bottom:8px">
          <span class="hint">コピー形式：</span>
          <select id="fmt" style="width:auto">
            <option value="text">テキスト</option>
            <option value="markdown">Markdown</option>
            <option value="json">JSON</option>
            <option value="csv">CSV</option>
          </select>
          <span class="hint">絞り込み：</span>
          <select id="statusFilter" style="width:auto">
            <option value="">すべて</option>
            <option value="candidate">仕入れ候補</option>
            <option value="purchased">仕入れ済み</option>
            <option value="listed">出品中</option>
            <option value="sold">売却済み</option>
            <option value="discarded">見送り</option>
          </select>
        </div>
        <div id="itemList"></div>
      </div>
      <div class="card">
        <h2>コピー内容プレビュー</h2>
        <pre class="preview" id="preview">コピーすると内容がここに表示されます</pre>
      </div>
    </section>

    <section class="tab" id="tab-input">
      <div class="card">
        <h2>商品の登録・更新</h2>
        <div class="hint">仕入れ候補として登録 → 相場を登録 → 一次判定を見てChatGPTへ最終レビュー依頼、の順で使います。</div>
        <form id="itemForm" class="form-grid">
          <div><label>既存商品を編集（新規は空欄）</label><select name="id" id="itemSelect"><option value="">新規登録</option></select></div>
          <div><label>商品名 *</label><input name="name" required></div>
          <div><label>ステータス</label><select name="status">
            <option value="candidate">仕入れ候補</option><option value="purchased">仕入れ済み</option>
            <option value="listed">出品中</option><option value="sold">売却済み</option>
            <option value="discarded">見送り</option></select></div>
          <div><label>型番</label><input name="model_number"></div>
          <div><label>JANコード</label><input name="jan_code"></div>
          <div><label>ブランド</label><input name="brand"></div>
          <div><label>カテゴリー</label><input name="category"></div>
          <div><label>状態</label><input name="condition" placeholder="例: 目立った傷なし"></div>
          <div><label>付属品</label><input name="accessories"></div>
          <div><label>傷・欠品</label><input name="flaws"></div>
          <div><label>仕入れ価格（円）</label><input name="purchase_price" type="number"></div>
          <div><label>仕入れ送料（円）</label><input name="purchase_shipping" type="number"></div>
          <div><label>仕入れ先</label><input name="purchase_source" placeholder="例: ヤフオク"></div>
          <div><label>仕入れ元URL</label><input name="purchase_url"></div>
          <div><label>仕入れ日</label><input name="purchased_at" type="date"></div>
          <div><label>想定販売価格（円）</label><input name="planned_price" type="number"></div>
          <div><label>最低販売価格（円）</label><input name="min_price" type="number"></div>
          <div><label>希望販売方針</label><select name="sales_policy">
            <option value="">未設定</option><option>早く売る</option><option>相場で売る</option><option>利益重視</option></select></div>
          <div><label>発送方法</label><input name="shipping_method" placeholder="例: らくらくメルカリ便"></div>
          <div><label>販売送料見込み（円）</label><input name="shipping_cost" type="number"></div>
          <div><label>発送日数</label><input name="shipping_days" placeholder="例: 1-2日で発送"></div>
          <div class="wide"><label>画像メモ（撮影済みの写真の内容）</label><input name="images_note" placeholder="例: 全体/背面/傷部分/付属品 の4枚撮影済み"></div>
          <div class="wide"><label>注意点</label><textarea name="notes"></textarea></div>
          <div class="form-actions wide"><button type="submit">保存</button><span class="hint" id="itemFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>相場データの登録（メルカリの売り切れ検索結果を手入力）</h2>
        <form id="marketForm" class="form-grid">
          <div><label>商品 *</label><select name="item_id" class="itemPick" required></select></div>
          <div><label>売り切れ最安値（円）</label><input name="min_price" type="number"></div>
          <div><label>売り切れ中央値（円）*</label><input name="median_price" type="number" required></div>
          <div><label>売り切れ平均値（円）</label><input name="mean_price" type="number"></div>
          <div><label>売り切れ件数</label><input name="sold_count" type="number"></div>
          <div><label>販売中件数</label><input name="active_count" type="number"></div>
          <div><label>相場商品URL</label><input name="url"></div>
          <div><label>取得元</label><input name="source" placeholder="メルカリ"></div>
          <div class="wide"><label>メモ（外れ値を除外した等）</label><input name="notes"></div>
          <div class="form-actions wide"><button type="submit">保存</button><span class="hint" id="marketFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>出品の登録・更新（閲覧数・いいね等の記録）</h2>
        <form id="listingForm" class="form-grid">
          <div><label>商品 *</label><select name="item_id" class="itemPick" required></select></div>
          <div><label>既存出品を更新（新規は空欄）</label><input name="id" type="number" placeholder="listing ID"></div>
          <div><label>ステータス</label><select name="status">
            <option value="active">出品中</option><option value="draft">下書き</option>
            <option value="cancelled">取り下げ</option></select></div>
          <div><label>出品価格（円）</label><input name="list_price" type="number"></div>
          <div><label>出品日</label><input name="listed_at" type="date"></div>
          <div><label>閲覧数</label><input name="views" type="number"></div>
          <div><label>いいね数</label><input name="likes" type="number"></div>
          <div><label>コメント数</label><input name="comments" type="number"></div>
          <div><label>発送方法</label><input name="shipping_method"></div>
          <div class="wide"><label>タイトル</label><input name="title"></div>
          <div class="wide"><label>商品説明</label><textarea name="description"></textarea></div>
          <div class="form-actions wide"><button type="submit">保存</button><span class="hint" id="listingFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>値下げの記録</h2>
        <form id="priceForm" class="form-grid">
          <div><label>出品（listing ID）*</label><input name="listing_id" type="number" required></div>
          <div><label>新価格（円）*</label><input name="new_price" type="number" required></div>
          <div><label>理由</label><input name="reason" placeholder="例: 2週間売れず"></div>
          <div class="form-actions wide"><button type="submit">記録</button><span class="hint" id="priceFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>売却の記録</h2>
        <form id="saleForm" class="form-grid">
          <div><label>商品 *</label><select name="item_id" class="itemPick" required></select></div>
          <div><label>listing ID（任意）</label><input name="listing_id" type="number"></div>
          <div><label>売却価格（円）*</label><input name="sold_price" type="number" required></div>
          <div><label>販売手数料（円）*</label><input name="sales_fee" type="number" required>
            <span class="hint">空欄で保存すると10%で自動計算します</span></div>
          <div><label>販売送料（円）</label><input name="shipping_cost" type="number"></div>
          <div><label>その他費用（円）</label><input name="other_cost" type="number"></div>
          <div><label>売却日</label><input name="sold_at" type="date"></div>
          <div><label>販売先</label><select name="channel"><option value="mercari">メルカリ</option>
            <option value="yahoo_flea">Yahoo!フリマ</option><option value="rakuma">ラクマ</option>
            <option value="other">その他</option></select></div>
          <div class="wide"><label>メモ</label><input name="note"></div>
          <div class="form-actions wide"><button type="submit">記録</button><span class="hint" id="saleFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>ChatGPT判断の記録（回答を貼って履歴として残す）</h2>
        <div class="hint">仕入れレビューや売れ残り分析でChatGPTが出した結論を保存すると、次回の出力に「過去のChatGPT判断」として自動で含まれます。</div>
        <form id="gptForm" class="form-grid">
          <div><label>商品 *</label><select name="item_id" class="itemPick" required></select></div>
          <div><label>種類</label><select name="kind">
            <option value="sourcing">仕入れレビュー</option><option value="listing">出品文作成</option>
            <option value="stale">売れ残り分析</option><option value="monthly">月次分析</option>
            <option value="reply">対応文</option><option value="other">その他</option></select></div>
          <div><label>結論</label><select name="verdict">
            <option value="">なし</option><option>買い</option><option>条件付きで買い</option>
            <option>見送り</option><option>追加確認が必要</option></select></div>
          <div class="wide"><label>要点（1〜2行）</label><input name="summary" placeholder="例: 相場は安定。付属品完備なら買い"></div>
          <div class="wide"><label>ChatGPT回答の全文（任意）</label><textarea name="raw_text"></textarea></div>
          <div class="form-actions wide"><button type="submit">保存</button><span class="hint" id="gptFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>売れない理由の記録（ChatGPT分析の結論をタグで蓄積）</h2>
        <form id="unsoldForm" class="form-grid">
          <div><label>商品 *</label><select name="item_id" class="itemPick" required></select></div>
          <div><label>理由タグ</label><select name="reason_tag">
            <option>価格が高い</option><option>写真が悪い</option><option>タイトルが弱い</option>
            <option>説明不足</option><option>需要が少ない</option><option>季節外れ</option>
            <option>相場下落</option><option>供給過多</option><option>状態が悪い</option><option>その他</option></select></div>
          <div><label>判断した人</label><select name="source">
            <option value="chatgpt">ChatGPT</option><option value="user">自分</option></select></div>
          <div class="wide"><label>詳細</label><input name="detail"></div>
          <div class="form-actions wide"><button type="submit">保存</button><span class="hint" id="unsoldFormMsg"></span></div>
        </form>
      </div>

      <div class="card">
        <h2>改善の記録（ChatGPTの提案と実施結果を残す）</h2>
        <form id="improveForm" class="form-grid">
          <div><label>商品 *</label><select name="item_id" class="itemPick" required></select></div>
          <div><label>種類</label><select name="kind">
            <option>タイトル変更</option><option>説明文変更</option><option>写真変更</option>
            <option>値下げ</option><option>カテゴリー変更</option><option>キーワード追加</option>
            <option>他販路出品</option><option>その他</option></select></div>
          <div><label>状態</label><select name="status">
            <option value="proposed">提案のみ（未実施）</option>
            <option value="applied" selected>実施済み</option>
            <option value="rejected">不採用</option></select></div>
          <div><label>提案した人</label><select name="source">
            <option value="chatgpt">ChatGPT</option><option value="user">自分</option></select></div>
          <div><label>日付</label><input name="applied_at" type="date"></div>
          <div class="wide"><label>内容</label><input name="detail"></div>
          <div class="wide"><label>結果（あとで記入可）</label><input name="result"></div>
          <div class="form-actions wide"><button type="submit">記録</button><span class="hint" id="improveFormMsg"></span></div>
        </form>
      </div>
    </section>

    <section class="tab" id="tab-kpi">
      <div class="card">
        <h2>月次KPI（直近6ヶ月）</h2>
        <div id="kpiMonths" class="hint">読み込み中…</div>
      </div>
      <div class="card">
        <h2>在庫・資金効率</h2>
        <div id="kpiStock" class="hint"></div>
      </div>
      <div class="card">
        <h2>売れなかった理由（全期間の累計）</h2>
        <div id="kpiReasons" class="hint"></div>
      </div>
    </section>

    <section class="tab" id="tab-sales">
      <div class="card">
        <h2>売上分析データ</h2>
        <div class="form-grid">
          <div><label>開始日</label><input id="salesFrom" type="date"></div>
          <div><label>終了日</label><input id="salesTo" type="date"></div>
          <div class="form-actions"><button id="salesCopy">ChatGPT分析用にコピー</button></div>
        </div>
        <div class="hint">期間未指定なら当月分を出力します。コピーしてChatGPTへ貼り付けると、事業分析と翌月の改善提案を依頼できます。</div>
        <pre class="preview" id="salesPreview">コピーすると内容がここに表示されます</pre>
      </div>
    </section>

    <section class="tab" id="tab-import">
      <div class="card">
        <h2>ChatGPTが作った出品用JSONの取り込み</h2>
        <div class="hint">
          ChatGPTに「出品作成用データ」を渡すと、タイトル・説明文・価格入りのJSONを返してもらえます。<br>
          ここに貼り付けると<strong>下書き</strong>として保存されます。本出品は必ずあなたがメルカリアプリで行ってください。
        </div>
        <label>出品用JSON</label>
        <textarea id="importJson" style="min-height:160px" placeholder='{"type":"mercari_listing_draft","item_id":1,"title":"...","description":"...","price":12800}'></textarea>
        <div class="form-actions"><button id="importBtn">下書きとして取り込む</button><span class="hint" id="importMsg"></span></div>
      </div>
    </section>
  </main>
  <div id="toast"></div>

  <script>
    const yen = new Intl.NumberFormat("ja-JP");
    let state = null;

    function esc(v) {
      return String(v ?? "").replace(/[&<>"']/g, (c) => ({
        "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    }
    function fmtYen(v) { return (v === null || v === undefined) ? "-" : yen.format(v) + "円"; }
    function toast(message) {
      const el = document.getElementById("toast");
      el.textContent = message;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 2600);
    }

    document.querySelectorAll("nav button").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("nav button").forEach((b) => b.classList.add("ghost"));
        btn.classList.remove("ghost");
        document.querySelectorAll("section.tab").forEach((s) => s.classList.remove("active"));
        document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
        if (btn.dataset.tab === "kpi") loadKpi();
      });
    });

    function sparkline(values, width = 160, height = 34) {
      if (!values || values.length < 2) return "";
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(1, max - min);
      const points = values.map((v, i) => {
        const x = 4 + i * ((width - 8) / (values.length - 1));
        const y = height - 6 - ((v - min) / span) * (height - 12);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      return `<svg width="${width}" height="${height}" style="vertical-align:middle;border:1px solid #edf0f4;background:#fbfcfd;border-radius:4px">
        <polyline points="${points}" fill="none" stroke="#2764b3" stroke-width="2"></polyline>
      </svg>`;
    }

    async function loadKpi() {
      const res = await fetch("/api/kpi");
      const payload = await res.json();
      if (!payload.ok) return;
      const d = payload.data;
      const maxProfit = Math.max(1, ...d.months.map((m) => Math.abs(m.profit)));
      document.getElementById("kpiMonths").innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr>
            <th style="text-align:left;padding:6px">月</th><th style="text-align:right;padding:6px">件数</th>
            <th style="text-align:right;padding:6px">売上</th><th style="text-align:right;padding:6px">利益</th>
            <th style="text-align:right;padding:6px">粗利率</th><th style="text-align:right;padding:6px">ROI</th>
            <th style="text-align:right;padding:6px">平均回転</th><th style="text-align:right;padding:6px">赤字件数</th>
            <th style="text-align:left;padding:6px;width:30%">利益バー</th>
          </tr></thead>
          <tbody>${d.months.map((m) => `<tr style="border-top:1px solid #edf0f4">
            <td style="padding:6px">${m.month}</td>
            <td style="padding:6px;text-align:right">${m.count}</td>
            <td style="padding:6px;text-align:right">${fmtYen(m.revenue)}</td>
            <td style="padding:6px;text-align:right" class="${m.profit >= 0 ? "plus" : "minus"}">${fmtYen(m.profit)}</td>
            <td style="padding:6px;text-align:right">${m.margin !== null ? (m.margin * 100).toFixed(1) + "%" : "-"}</td>
            <td style="padding:6px;text-align:right">${m.roi !== null ? (m.roi * 100).toFixed(1) + "%" : "-"}</td>
            <td style="padding:6px;text-align:right">${m.avg_days_to_sell !== null ? m.avg_days_to_sell + "日" : "-"}</td>
            <td style="padding:6px;text-align:right" class="${m.loss_count ? "minus" : ""}">${m.loss_count}</td>
            <td style="padding:6px"><div style="height:12px;border-radius:3px;width:${Math.round(Math.abs(m.profit) / maxProfit * 100)}%;background:${m.profit >= 0 ? "#0f8a5f" : "#c2413a"}"></div></td>
          </tr>`).join("")}</tbody>
        </table>`;
      const agingRows = d.stock.aging.filter((b) => b.count)
        .map((b) => `${b.label}: ${b.count}件 / ${fmtYen(b.capital)}`).join("<br>") || "在庫なし";
      document.getElementById("kpiStock").innerHTML = `
        在庫 ${d.stock.count}点 ／ 寝ている資金 <strong>${fmtYen(d.stock.capital)}</strong><br>
        ${agingRows}<br>
        資金効率の目安（今月利益 ÷ 在庫資金）: <strong>${d.capital_efficiency !== null ? (d.capital_efficiency * 100).toFixed(1) + "%" : "-"}</strong>`;
      document.getElementById("kpiReasons").innerHTML = d.unsold_reasons.length
        ? d.unsold_reasons.map((r) => `${esc(r.reason_tag)}: ${r.count}件`).join("<br>")
        : "まだ記録がありません";
    }

    const STATUS_LABELS = {
      candidate: "仕入れ候補", purchased: "仕入れ済み", listed: "出品中",
      sold: "売却済み", discarded: "見送り",
    };

    function render() {
      const s = state.summary;
      const agingText = (s.aging || []).filter((b) => b.count)
        .map((b) => `${b.label}: ${b.count}件 ${fmtYen(b.capital)}`).join("<br>") || "在庫なし";
      document.getElementById("metrics").innerHTML = `
        <div class="metric"><div class="metric-label">仕入れ候補</div><div class="metric-value">${s.candidates}件</div></div>
        <div class="metric"><div class="metric-label">在庫に寝ている資金</div><div class="metric-value">${s.stock_count}点 / ${fmtYen(s.stock_value)}</div>
          <div class="metric-label" style="margin-top:6px">${agingText}</div></div>
        <div class="metric"><div class="metric-label">出品中 / 売れ残り</div><div class="metric-value">${s.listed_count}件 / <span class="${s.stale_count ? "minus" : ""}">${s.stale_count}件</span></div></div>
        <div class="metric"><div class="metric-label">今月の売上 / 利益</div><div class="metric-value">${fmtYen(s.month_revenue)} / <span class="${s.month_profit >= 0 ? "plus" : "minus"}">${fmtYen(s.month_profit)}</span></div></div>
      `;
      const filter = document.getElementById("statusFilter").value;
      const items = state.items.filter((i) => !filter || i.status === filter);
      document.getElementById("itemList").innerHTML = items.length ? items.map((item) => {
        const j = item.judgement || {};
        const badgeClass = j.label === "買い候補" ? "buy" : (j.label === "見送り候補" ? "skip" : "");
        const market = item.market;
        const profit = item.estimated_profit;
        return `<div class="item-row">
          <div class="item-head">
            <div>
              <span class="item-name">#${item.id} ${esc(item.name)}</span>
              <span class="badge">${STATUS_LABELS[item.status] || esc(item.status)}</span>
              ${item.status === "candidate" && j.label ? `<span class="badge ${badgeClass}">一次判定: ${esc(j.label)}</span>` : ""}
              ${item.stale ? `<span class="badge stale">売れ残り ${item.days_listed}日</span>` : ""}
              ${item.gpt_verdict && item.gpt_verdict.verdict ? `<span class="badge">GPT: ${esc(item.gpt_verdict.verdict)}</span>` : ""}
              ${item.days_in_stock !== null && item.days_in_stock !== undefined ? `<span class="badge">在庫${item.days_in_stock}日 / ${fmtYen(item.capital)}</span>` : ""}
            </div>
            <div class="copy-actions">
              <button class="small" onclick="copyExport('sourcing', ${item.id})">仕入れ判断用にコピー</button>
              <button class="small ghost" onclick="copyExport('listing', ${item.id})">出品作成用にコピー</button>
              ${item.listing ? `<button class="small ghost" onclick="copyExport('stale', ${item.id})">売れ残り分析用にコピー</button>` : ""}
            </div>
          </div>
          <div class="item-detail">
            仕入れ ${fmtYen(item.purchase_price)}（送料${fmtYen(item.purchase_shipping)}）
            ／ 相場中央値 ${market ? fmtYen(market.median_price) : "未登録"}
            ${item.market_trend && item.market_trend.length >= 2 ? " " + sparkline(item.market_trend) : ""}
            ${market ? `（売切${market.sold_count ?? "-"}件・販売中${market.active_count ?? "-"}件）` : ""}
            ／ 基準価格 ${fmtYen(item.reference_price)}
            ／ 想定利益 <strong class="${profit === null ? "" : (profit >= 0 ? "plus" : "minus")}">${profit === null ? "未計算" : yen.format(profit) + "円"}</strong>
            ${item.listing ? `<br>出品: listing ${item.listing.id}（${esc(item.listing.status)}） ${fmtYen(item.listing.current_price || item.listing.list_price)} / 閲覧${item.listing.views} いいね${item.listing.likes} コメント${item.listing.comments}` : ""}
            ${j.reasons && j.reasons.length ? `<br>判定理由: ${j.reasons.map(esc).join("、")}` : ""}
            ${j.warnings && j.warnings.length ? `<br><span class="minus">注意: ${j.warnings.map(esc).join("、")}</span>` : ""}
          </div>
        </div>`;
      }).join("") : '<div class="hint">商品がありません。「登録・入力」タブから登録してください。</div>';

      const selects = document.querySelectorAll("select.itemPick, #itemSelect");
      selects.forEach((select) => {
        const keep = select.value;
        const isEdit = select.id === "itemSelect";
        select.innerHTML = (isEdit ? '<option value="">新規登録</option>' : '<option value="">選択してください</option>')
          + state.items.map((i) => `<option value="${i.id}">#${i.id} ${esc(i.name)}（${STATUS_LABELS[i.status] || i.status}）</option>`).join("");
        if (keep) select.value = keep;
      });
    }

    async function load() {
      const res = await fetch("/api/state");
      const payload = await res.json();
      state = payload.data;
      render();
    }

    async function copyExport(kind, itemId) {
      const fmt = document.getElementById("fmt").value;
      const res = await fetch(`/api/export?kind=${kind}&item_id=${itemId}&format=${fmt}`);
      const payload = await res.json();
      if (!payload.ok) { toast("出力エラー: " + payload.error); return; }
      document.getElementById("preview").textContent = payload.text;
      await copyText(payload.text);
    }

    async function copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
        toast("コピーしました。ChatGPTへ貼り付けてください");
      } catch (err) {
        toast("自動コピーできない環境です。プレビューから手動でコピーしてください");
      }
    }

    async function postForm(form, url, msgId, transform) {
      const data = {};
      new FormData(form).forEach((value, key) => {
        if (value !== "") data[key] = value;
      });
      if (transform) transform(data);
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const payload = await res.json();
      const msg = document.getElementById(msgId);
      if (payload.ok) {
        msg.textContent = "保存しました";
        form.reset();
        await load();
      } else {
        msg.textContent = "エラー: " + payload.error;
      }
      setTimeout(() => { msg.textContent = ""; }, 4000);
      return payload;
    }

    document.getElementById("itemForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/item", "itemFormMsg");
    });
    document.getElementById("itemSelect").addEventListener("change", (e) => {
      const item = state.items.find((i) => String(i.id) === e.target.value);
      const form = document.getElementById("itemForm");
      if (!item) { form.reset(); return; }
      for (const el of form.elements) {
        if (!el.name || el.name === "id") continue;
        const value = item[el.name];
        el.value = (value === null || value === undefined) ? "" : String(value).slice(0, el.type === "date" ? 10 : undefined);
      }
    });
    document.getElementById("marketForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/market", "marketFormMsg");
    });
    document.getElementById("listingForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/listing", "listingFormMsg", (data) => {
        if (data.list_price) data.current_price = data.current_price || data.list_price;
      });
    });
    document.getElementById("priceForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/price-change", "priceFormMsg");
    });
    document.getElementById("saleForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/sale", "saleFormMsg", (data) => {
        if (!data.sales_fee && data.sold_price) {
          data.sales_fee = Math.floor(Number(data.sold_price) * 0.10);
        }
      });
    });
    document.getElementById("improveForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/improvement", "improveFormMsg");
    });
    document.getElementById("gptForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/gpt-review", "gptFormMsg");
    });
    document.getElementById("unsoldForm").addEventListener("submit", (e) => {
      e.preventDefault();
      postForm(e.target, "/api/unsold-reason", "unsoldFormMsg");
    });

    document.getElementById("salesCopy").addEventListener("click", async () => {
      const fmt = document.getElementById("fmt").value;
      const from = document.getElementById("salesFrom").value;
      const to = document.getElementById("salesTo").value;
      const params = new URLSearchParams({ kind: "sales", format: fmt });
      if (from) params.set("from", from);
      if (to) params.set("to", to);
      const res = await fetch("/api/export?" + params.toString());
      const payload = await res.json();
      if (!payload.ok) { toast("出力エラー: " + payload.error); return; }
      document.getElementById("salesPreview").textContent = payload.text;
      await copyText(payload.text);
    });

    document.getElementById("importBtn").addEventListener("click", async () => {
      const raw = document.getElementById("importJson").value.trim();
      const msg = document.getElementById("importMsg");
      if (!raw) { msg.textContent = "JSONを貼り付けてください"; return; }
      const res = await fetch("/api/import-listing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ json: raw }),
      });
      const payload = await res.json();
      if (payload.ok) {
        msg.textContent = payload.message + (payload.warnings.length ? " ⚠ " + payload.warnings.join(" / ") : "");
        document.getElementById("importJson").value = "";
        await load();
      } else {
        msg.textContent = "エラー: " + payload.error;
      }
    });

    document.getElementById("statusFilter").addEventListener("change", render);
    load();
  </script>
</body>
</html>
"""


def main(port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((HOST, port), MercariHandler)
    print(f"メルカリ販売管理: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
