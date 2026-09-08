# iphone-price-monitor

iPhone 買取価格モニター（`main.py` / `.github/workflows/monitor.yml`）と、
TCG 利益商品の日次抽出システム（`tcg/` / `.github/workflows/tcg_daily.yml`）を同居させたリポジトリ。

## TCG 利益商品 日次抽出（BS提出用）

ポケモンカード／ワンピースカード／フュージョンワールドを対象に、
**短期・中期・長期の3パターンで1日15件（各5件）** の候補を CSV と Discord に出す。
設計は [docs/tcg_profit_system_design.md](docs/tcg_profit_system_design.md)。

### 構成

```
tcg/
  config.yaml       設定（チャネル手数料・戦略しきい値・ソース・出力）
  main.py           日次パイプライン（収集 → 指標 → 3戦略スコア → 選定 → CSV/Discord）
  catalog.py        商品マスタ data/products.csv の読込
  database.py       SQLite（tcg_prices.sqlite3）
  sources/          価格ソース（下表）: cardrush / toretoku / pricebase / rakuten / yahoo / manual_sheet
  probe.py          候補サイトの到達性と robots.txt を確認（Actions の検証実行で使用）
  dump.py           候補ページの価格要素の構造をログに出す（手動デバッグ実行時のみ）
  metrics.py        騰落率・ボラ・流動性・在庫などの指標
  profit.py         4チャネル（メルカリ/ヤフオク/買取店/業者間卸）の手取り比較と推奨出口
  strategy.py       短期/中期/長期のスコアと選定制約
  report.py         24列CSV と Discord 要約
  inspect.py        駿河屋ページの価格抽出を目視確認する補助コマンド
data/
  products.csv                  監視対象（サンプル8件入り。実運用では差し替え・追加する）
  flea_market_prices.csv        フリマ相場シート（人が週1更新。ヘッダのみ）
  flea_market_prices.example.csv 記入例
reports/YYYY-MM-DD.csv          毎日の提出用CSV（Actions がコミット）
```

### 価格ソース（2026-09 に GitHub Actions からの到達性を実測して選定）

| ソース | 側 | 対象 | 備考 |
|---|---|---|---|
| カードラッシュ通販（`cardrush-pokemon.jp` / `cardrush-op.jp` / `cardrush-db.jp`） | 仕入（販売価格・在庫） | 3タイトル | 検索結果から型番一致の最安。鑑定品・状態B以下は除外 |
| トレトク 買取価格表 | 出口（買取） | ポケカ／ワンピ | 1ページに数百件。タイトルごとに1回取得 |
| PRICE BASE 買取表 | 出口（買取） | 3タイトル | robots.txt の Crawl-delay 5 に従う |
| 楽天市場 商品検索API | 仕入 | 3タイトル | **要 `RAKUTEN_APP_ID` + `RAKUTEN_ACCESS_KEY`**（2026-02 の新基盤仕様） |
| Yahoo!ショッピング API | 仕入 | 3タイトル | 要 `YAHOO_APP_ID` |
| フリマ相場シート | 出口（メルカリ等） | 3タイトル | 人が週1で更新 |
| 駿河屋 | — | — | Actions からは全件 403（Cloudflare）のため無効化。回避はしない |

キー無しでも **カードラッシュ＋トレトク／PRICE BASE** で仕入・出口の両側が揃うため、短期パターンは動く。

### セットアップ

1. GitHub Secrets に以下を登録（任意。無くてもカードラッシュ／買取表だけで動く）
   - `RAKUTEN_APP_ID` と `RAKUTEN_ACCESS_KEY` … 楽天ウェブサービス（2026-02 以降はアプリ再登録でアクセスキーも発行される）
   - `YAHOO_APP_ID` … Yahoo!デベロッパーネットワークの Client ID
   - `DISCORD_WEBHOOK_URL` … 既存のものを流用可
2. `data/products.csv` に監視したい商品を登録
   - `card_no`（型番）は必ず入れる。一覧ページとの照合キーになる
   - `must_keywords` / `exclude_keywords` は `|` 区切り。商品名を絞る（PSA・プロキシ・まとめ売り・スーパーパラレル等を除外）
3. `data/flea_market_prices.csv` にメルカリ等の相場（中央値・直近30日売れ数）を記入（週1更新）
4. ワークフロー `TCG利益商品 日次抽出` は毎朝 07:00 JST に自動実行。手動実行（`debug` / `dry_run` 入力あり）も可

### ローカル実行

```bash
pip install -r requirements-dev.txt
python -m tcg.main                     # 収集 → 選定 → CSV → Discord（Webhook未設定なら標準出力）
python -m tcg.main --no-fetch --dry-run  # DBの既存データだけで判定し、履歴記録・通知はしない
python -m pytest -q
```

### 運用上の前提

- **短期は初日から出る**（当日の仕入価格と出口価格の差だけで判定できる）。
  **中期は履歴30日、長期は履歴90日** が貯まるまで「該当なし」になる。水増しはしない。
- メルカリ等のフリマは規約リスクを避けるため自動巡回しない。出口価格は手動シート、
  仕入側は CSV の「フリマ指値」「探索キーワード」を見て人が探す半自動方式。
- 同一商品は14日間再提出しない（`submissions` テーブルで管理）。
  高額品（10万円超）は1日3件まで、各タイトル最低1件ずつ確保。
