from __future__ import annotations

import json
import os
import socket
import sqlite3
import struct
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from database import PriceDatabase
from decision import judge
from main import BASE_DIR, run_monitor


HOST = "0.0.0.0"
PORT = 8765
ACCESS_TOKEN = os.getenv("IPHONE_MONITOR_TOKEN")


def load_config() -> dict[str, Any]:
    with (BASE_DIR / "config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def database_path(config: dict[str, Any]) -> Path:
    db_path = Path(config.get("database", {}).get("path", "prices.sqlite3"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return db_path


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "raw_text"}


def dashboard_data() -> dict[str, Any]:
    config = load_config()
    db = PriceDatabase(database_path(config))
    thresholds = config.get("thresholds", {})

    items = []
    with db.connect() as conn:
        for item in config.get("items", []):
            colors = []
            for color in item.get("colors", []):
                latest_rows = [
                    compact_record(dict(row)) for row in db.latest_by_color(item["name"], color["key"])
                ]
                best = max(latest_rows, key=lambda row: int(row["price"])) if latest_rows else None
                history_rows = conn.execute(
                    """
                    SELECT observed_at, MAX(price) AS price
                    FROM price_observations
                    WHERE item_name = ? AND color_key = ?
                    GROUP BY observed_at
                    ORDER BY observed_at DESC
                    LIMIT 24
                    """,
                    (item["name"], color["key"]),
                ).fetchall()
                history = [row_to_dict(row) for row in reversed(history_rows)]
                if best:
                    decision = judge(int(best["price"]), int(item["cost_price"]), thresholds)
                    profit = int(best["price"]) - int(item["cost_price"])
                else:
                    decision = None
                    profit = None
                colors.append(
                    {
                        "key": color["key"],
                        "label": color["label"],
                        "quantity": color.get("quantity", 1),
                        "latest": latest_rows,
                        "best": best,
                        "profit": profit,
                        "decision": decision.__dict__ if decision else None,
                        "history": history,
                    }
                )
            items.append(
                {
                    "name": item["name"],
                    "cost_price": item["cost_price"],
                    "capacity": item.get("capacity"),
                    "colors": colors,
                }
            )

        recent = [
            compact_record(row_to_dict(row))
            for row in conn.execute(
                """
                SELECT *
                FROM price_observations
                ORDER BY observed_at DESC, id DESC
                LIMIT 30
                """
            ).fetchall()
        ]
        errors = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM scrape_errors
                ORDER BY observed_at DESC, id DESC
                LIMIT 8
                """
            ).fetchall()
        ]

    return {
        "items": items,
        "item": items[0] if items else None,
        "colors": items[0]["colors"] if items else [],
        "recommendations": recommendations(items),
        "recent": recent,
        "errors": errors,
    }


def recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for item in items:
        for color in item.get("colors", []):
            best = color.get("best")
            if not best or color.get("profit") is None:
                continue
            candidates.append(
                {
                    "item_name": item["name"],
                    "color_label": color["label"],
                    "quantity": color.get("quantity", 0),
                    "shop_name": best["shop_name"],
                    "price": int(best["price"]),
                    "profit": int(color["profit"]),
                    "decision": color.get("decision"),
                    "url": best["url"],
                }
            )
    return sorted(candidates, key=lambda row: (abs(row["profit"]), -row["price"]))[:8]


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/manifest.json":
            self.send_json(MANIFEST)
            return
        if path == "/sw.js":
            self.send_js(SERVICE_WORKER)
            return
        if path == "/icon.png":
            self.send_png(make_icon_png())
            return
        if not self.is_authorized():
            self.send_locked()
            return
        if path == "/":
            self.send_html(INDEX_HTML)
            return
        if path == "/api/data":
            self.send_json(dashboard_data())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self.is_authorized():
            self.send_json({"ok": False, "error": "unauthorized"}, status=401)
            return
        path = urlparse(self.path).path
        if path != "/api/refresh":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            saved_count = run_monitor(BASE_DIR / "config.yaml")
            self.send_json({"ok": True, "saved_count": saved_count, "data": dashboard_data()})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)

    def send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if ACCESS_TOKEN and self.token_from_query() == ACCESS_TOKEN:
            self.send_header("Set-Cookie", f"iphone_monitor_token={ACCESS_TOKEN}; Path=/; SameSite=Lax")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, data: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if ACCESS_TOKEN and self.token_from_query() == ACCESS_TOKEN:
            self.send_header("Set-Cookie", f"iphone_monitor_token={ACCESS_TOKEN}; Path=/; SameSite=Lax")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_js(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_png(self, body: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def token_from_query(self) -> str | None:
        query = urlparse(self.path).query
        for part in query.split("&"):
            if part.startswith("token="):
                return part.split("=", 1)[1]
        return None

    def is_authorized(self) -> bool:
        if not ACCESS_TOKEN:
            return True
        if self.token_from_query() == ACCESS_TOKEN:
            return True
        cookie = self.headers.get("Cookie", "")
        return f"iphone_monitor_token={ACCESS_TOKEN}" in cookie

    def send_locked(self) -> None:
        self.send_html(
            """<!doctype html><html lang="ja"><meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>認証が必要です</title>
            <body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:24px">
            <h1>認証が必要です</h1>
            <p>発行されたURLをそのまま開いてください。</p>
            </body></html>"""
        )


INDEX_HTML = r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#2764b3">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="買取監視">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <link rel="manifest" href="/manifest.json">
  <link rel="apple-touch-icon" href="/icon.png">
  <link rel="icon" href="/icon.png">
  <title>iPhone買取価格監視</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #d9dee7;
      --green: #0f8a5f;
      --red: #c2413a;
      --blue: #2764b3;
      --orange: #b65f16;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif;
      letter-spacing: 0;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { margin: 0; font-size: 20px; line-height: 1.2; }
    .meta { color: var(--muted); font-size: 13px; margin-top: 4px; }
    button {
      appearance: none;
      border: 1px solid #204f91;
      background: var(--blue);
      color: #fff;
      padding: 10px 14px;
      border-radius: 6px;
      font-weight: 700;
      cursor: pointer;
      min-width: 112px;
    }
    button:disabled { opacity: .55; cursor: wait; }
    main { max-width: 1180px; margin: 0 auto; padding: 22px; }
    .status {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric, .color-card, .table-wrap, .errors {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric { padding: 14px; }
    .metric-label { color: var(--muted); font-size: 12px; }
    .metric-value { font-size: 22px; font-weight: 800; margin-top: 6px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }
    .recommendations {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 18px;
      overflow: hidden;
    }
    .rec-row {
      display: grid;
      grid-template-columns: 1.4fr .7fr .7fr .7fr;
      gap: 10px;
      padding: 12px 14px;
      border-top: 1px solid #edf0f4;
      align-items: center;
      font-size: 13px;
    }
    .rec-row:first-child { border-top: 0; }
    .rec-title { font-weight: 800; }
    .rec-shop { color: var(--muted); margin-top: 3px; }
    .rec-action { text-align: right; }
    .rec-action a { color: var(--blue); font-weight: 700; text-decoration: none; }
    .item-section { margin-bottom: 24px; }
    .item-title {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin: 20px 0 10px;
    }
    .item-title h2 { margin: 0; }
    .color-card { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .color-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .color-name { display: flex; align-items: center; gap: 8px; font-weight: 800; }
    .swatch { width: 16px; height: 16px; border-radius: 50%; border: 1px solid #9aa4b2; flex: 0 0 auto; }
    .silver { background: #e7e9ed; }
    .deep_blue { background: #28415f; }
    .cosmic_orange { background: #d97935; }
    .qty { color: var(--muted); font-size: 12px; }
    .price { font-size: 28px; font-weight: 900; }
    .profit { font-weight: 800; }
    .plus { color: var(--green); }
    .minus { color: var(--red); }
    .decision {
      border-left: 4px solid var(--orange);
      padding-left: 10px;
      color: #3d3328;
      min-height: 48px;
      font-size: 13px;
      line-height: 1.45;
    }
    .shops { display: grid; gap: 6px; }
    .shop {
      display: grid;
      grid-template-columns: minmax(96px, 1fr) auto auto;
      gap: 8px;
      align-items: center;
      font-size: 13px;
      border-top: 1px solid #eef1f5;
      padding-top: 6px;
    }
    .spark { width: 100%; height: 46px; border: 1px solid #edf0f4; background: #fbfcfd; }
    h2 { font-size: 16px; margin: 22px 0 10px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #edf0f4; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 700; background: #fbfcfd; }
    .table-wrap { overflow-x: auto; }
    .errors { padding: 12px 14px; color: var(--red); font-size: 13px; }
    .empty { color: var(--muted); padding: 18px; }
    .install-note {
      display: none;
      margin-bottom: 14px;
      padding: 12px 14px;
      background: #eef6ff;
      border: 1px solid #b9d7f4;
      border-radius: 8px;
      color: #21486f;
      font-size: 13px;
      line-height: 1.45;
    }
    @media (max-width: 900px) {
      header { align-items: flex-start; flex-direction: column; }
      .status, .grid, .rec-row { grid-template-columns: 1fr; }
      .rec-action { text-align: left; }
      button { width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>iPhone買取価格監視</h1>
      <div class="meta" id="subtitle">読み込み中</div>
    </div>
    <button id="refreshBtn" type="button">価格取得</button>
  </header>
  <main>
    <section class="install-note" id="installNote">共有ボタンから「ホーム画面に追加」を選ぶと、アプリのように開けます。</section>
    <section class="status" id="metrics"></section>
    <h2>おすすめ売却候補</h2>
    <section class="recommendations" id="recommendations"></section>
    <section id="items"></section>
    <h2>最新取得履歴</h2>
    <section class="table-wrap" id="recent"></section>
    <h2>取得エラー</h2>
    <section id="errors"></section>
  </main>
  <script>
    const yen = new Intl.NumberFormat("ja-JP");
    const btn = document.getElementById("refreshBtn");

    function fmtYen(value) {
      if (value === null || value === undefined) return "-";
      return yen.format(value) + "円";
    }
    function fmtDiff(value) {
      if (value === null || value === undefined) return "初回";
      return (value >= 0 ? "+" : "") + yen.format(value) + "円";
    }
    function cls(value) {
      return value >= 0 ? "plus" : "minus";
    }
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[c]));
    }
    function sparkline(history) {
      if (!history || history.length < 2) return '<svg class="spark" viewBox="0 0 240 46"></svg>';
      const values = history.map((h) => Number(h.price));
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(1, max - min);
      const points = values.map((v, i) => {
        const x = 8 + i * (224 / Math.max(1, values.length - 1));
        const y = 38 - ((v - min) / span) * 30;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      return `<svg class="spark" viewBox="0 0 240 46" preserveAspectRatio="none">
        <polyline points="${points}" fill="none" stroke="#2764b3" stroke-width="2.5"></polyline>
      </svg>`;
    }
    function render(data) {
      const allColors = data.items.flatMap((item) => item.colors.map((color) => ({...color, item})));
      document.getElementById("subtitle").textContent =
        `${data.items.length}商品を監視中`;
      const bestPrices = allColors.map((c) => c.best ? Number(c.best.price) : 0).filter(Boolean);
      const top = bestPrices.length ? Math.max(...bestPrices) : null;
      const totalQty = allColors.reduce((sum, c) => sum + Number(c.quantity || 0), 0);
      const alertColors = allColors.filter((c) => c.profit !== null && c.profit >= -5000).length;
      document.getElementById("metrics").innerHTML = `
        <div class="metric"><div class="metric-label">全色最高</div><div class="metric-value">${fmtYen(top)}</div></div>
        <div class="metric"><div class="metric-label">監視商品</div><div class="metric-value">${data.items.length}</div></div>
        <div class="metric"><div class="metric-label">在庫</div><div class="metric-value">${totalQty}台</div></div>
        <div class="metric"><div class="metric-label">売却候補色</div><div class="metric-value">${alertColors}色</div></div>
      `;
      document.getElementById("recommendations").innerHTML = data.recommendations.length ? data.recommendations.map((row, index) => `
        <div class="rec-row">
          <div>
            <div class="rec-title">${index + 1}. ${esc(row.item_name)} / ${esc(row.color_label)}</div>
            <div class="rec-shop">${esc(row.shop_name)}${Number(row.quantity || 0) > 0 ? ` / 在庫${Number(row.quantity)}台` : ""}</div>
          </div>
          <div><div class="metric-label">最高価格</div><strong>${fmtYen(row.price)}</strong></div>
          <div><div class="metric-label">原価差</div><strong class="${cls(row.profit)}">${fmtDiff(row.profit)}</strong></div>
          <div class="rec-action"><a href="${esc(row.url)}" target="_blank" rel="noreferrer">店舗を開く</a></div>
        </div>
      `).join("") : '<div class="empty">価格取得後に表示されます</div>';
      document.getElementById("items").innerHTML = data.items.map((item) => {
        const itemBestPrices = item.colors.map((c) => c.best ? Number(c.best.price) : 0).filter(Boolean);
        const itemTop = itemBestPrices.length ? Math.max(...itemBestPrices) : null;
        return `<section class="item-section">
          <div class="item-title">
            <h2>${esc(item.name)}</h2>
            <div class="meta">原価 ${fmtYen(item.cost_price)} / 最高 ${fmtYen(itemTop)}</div>
          </div>
          <div class="grid">${item.colors.map((color) => renderColorCard(color)).join("")}</div>
        </section>`;
      }).join("");
      function renderColorCard(color) {
        const best = color.best;
        return `<article class="color-card">
          <div class="color-head">
            <div class="color-name"><span class="swatch ${esc(color.key)}"></span>${esc(color.label)}</div>
            <div class="qty">${Number(color.quantity || 0)}台</div>
          </div>
          <div>
            <div class="price">${fmtYen(best && best.price)}</div>
            <div class="meta">${best ? esc(best.shop_name) : "未取得"}</div>
          </div>
          <div class="profit ${cls(color.profit || 0)}">原価差 ${fmtDiff(color.profit)}</div>
          ${sparkline(color.history)}
          <div class="decision">${color.decision ? `<strong>${esc(color.decision.label)}</strong><br>${esc(color.decision.message)}` : "判断できる価格がありません"}</div>
          <div class="shops">
            ${color.latest.map((row) => `<div class="shop">
              <span>${esc(row.shop_name)}</span><strong>${fmtYen(row.price)}</strong><span class="${cls(row.diff || 0)}">${fmtDiff(row.diff)}</span>
            </div>`).join("")}
          </div>
        </article>`;
      }
      document.getElementById("recent").innerHTML = data.recent.length ? `<table>
        <thead><tr><th>取得日時</th><th>商品</th><th>店舗</th><th>色</th><th>価格</th><th>前回比</th><th>URL</th></tr></thead>
        <tbody>${data.recent.map((row) => `<tr>
          <td>${esc(row.observed_at)}</td><td>${esc(row.item_name)}</td><td>${esc(row.shop_name)}</td><td>${esc(row.color_label)}</td>
          <td>${fmtYen(row.price)}</td><td class="${cls(row.diff || 0)}">${fmtDiff(row.diff)}</td>
          <td><a href="${esc(row.url)}" target="_blank" rel="noreferrer">開く</a></td>
        </tr>`).join("")}</tbody>
      </table>` : '<div class="empty">まだ価格履歴がありません</div>';
      document.getElementById("errors").innerHTML = data.errors.length ? `<div class="errors">
        ${data.errors.map((e) => `<div>${esc(e.observed_at)} / ${esc(e.shop_name)} / ${esc(e.error)}</div>`).join("")}
      </div>` : '<div class="empty">直近の取得エラーはありません</div>';
    }
    async function load() {
      const res = await fetch("/api/data");
      render(await res.json());
    }
    async function refresh() {
      btn.disabled = true;
      btn.textContent = "取得中";
      try {
        const res = await fetch("/api/refresh", { method: "POST" });
        const payload = await res.json();
        if (!payload.ok) throw new Error(payload.error || "取得に失敗しました");
        render(payload.data);
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "価格取得";
      }
    }
    btn.addEventListener("click", refresh);
    if (navigator.standalone !== true && /iPhone|iPad|iPod/.test(navigator.userAgent)) {
      document.getElementById("installNote").style.display = "block";
    }
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    load();
  </script>
</body>
</html>
"""


MANIFEST = {
    "name": "iPhone買取価格監視",
    "short_name": "買取監視",
    "start_url": "/?token=" + ACCESS_TOKEN if ACCESS_TOKEN else "/",
    "display": "standalone",
    "background_color": "#f6f7f9",
    "theme_color": "#2764b3",
    "icons": [
        {"src": "/icon.png", "sizes": "180x180", "type": "image/png"},
        {"src": "/icon.png", "sizes": "512x512", "type": "image/png"},
    ],
}


SERVICE_WORKER = """
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open("iphone-monitor-v1").then((cache) => cache.addAll(["/icon.png"])));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
"""


def make_icon_png(size: int = 180) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            if 36 <= x <= 144 and 34 <= y <= 146:
                r, g, b = 39, 100, 179
            elif x > 96 and y > 100:
                r, g, b = 15, 138, 95
            else:
                r, g, b = 246, 247, 249
            row.extend([r, g, b])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    local_ip = get_local_ip()
    print(f"Macで開く: http://127.0.0.1:{PORT}")
    if local_ip:
        print(f"iPhoneで開く: http://{local_ip}:{PORT}")
    else:
        print("iPhoneで開く: http://MacのIPアドレス:8765")
    if ACCESS_TOKEN:
        print("アクセス制限: 有効")
    server.serve_forever()


def get_local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


if __name__ == "__main__":
    main()
