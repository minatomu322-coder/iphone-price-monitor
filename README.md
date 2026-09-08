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
  sources/          価格ソース: rakuten(公式API) / surugaya(販売+買取) / manual_sheet(フリマ相場)
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

### セットアップ

1. GitHub Secrets に以下を登録
   - `RAKUTEN_APP_ID` … 楽天ウェブサービスのアプリケーションID（無料）
   - `DISCORD_WEBHOOK_URL` … 既存のものを流用可
2. `data/products.csv` に監視したい商品を登録
   - `must_keywords` / `exclude_keywords` は `|` 区切り。検索結果の商品名を絞る（PSA・プロキシ・まとめ売り等を除外）
   - 駿河屋を使う場合は `surugaya_url`（販売 or 検索ページ）と `surugaya_kaitori_url`（買取ページ）を記入し、
     初回は `python -m tcg.inspect <URL>` で価格が正しく取れるか確認する
3. `data/flea_market_prices.csv` にメルカリ等の相場（中央値・直近30日売れ数）を記入（週1更新）
4. ワークフロー `TCG利益商品 日次抽出` は毎朝 07:00 JST に自動実行。手動実行も可

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
