# 利益商品AI（rakuten_finder）

楽天市場から商品を巡回し、メルカリ相場と比較して「利益が出そうな商品」だけを
Discord に通知する、せどり支援ツール。設計の詳細は
[docs/rakuten_finder_design.md](docs/rakuten_finder_design.md) を参照。

> ⚠️ 自動購入はしません。購入判断は必ず人間が行います。
> メルカリ相場は MVP では CSV で手入力します（公式検索 API が無いため）。

## セットアップ

```bash
pip install -r requirements-rakuten.txt
cp .env.example .env
# .env に RAKUTEN_APP_ID と DISCORD_WEBHOOK_URL を設定
cp data/mercari_prices.sample.csv data/mercari_prices.csv
# mercari_prices.csv に相場を記入（query は JAN かキーワード）
```

- 楽天アプリID: https://webservice.rakuten.co.jp/ で無料発行
- 監視キーワードやしきい値は `config/rakuten_finder.yaml` で設定

## 使い方

```bash
# 巡回 1 回（検索 → 相場照合 → 利益計算 → 採点 → 保存 → Discord通知）
python -m rakuten_finder.cli run

# 通知なしで実行（動作確認用）
python -m rakuten_finder.cli run --no-notify

# 日次レポートを Discord に送信
python -m rakuten_finder.cli report

# 買う/見送り/保留 を記録（次回以降のスコアに学習反映）
python -m rakuten_finder.cli decide "shop:10001234" buy "利益率が良い"
```

## ダッシュボード

```bash
uvicorn rakuten_finder.dashboard:app --port 8000
# → http://localhost:8000
```

一覧表示（ランク/判断でフィルタ）と「買う / 保留 / 見送り」ボタンでの
判断記録ができます。記録は学習ブーストとして次回のスコアに反映されます。

## Docker

```bash
docker compose up          # ダッシュボード(8000) + 60分ごとの巡回ワーカー
```

## GitHub Actions（クラウド常時稼働）

`.github/workflows/rakuten-finder.yml` が 60 分ごとに巡回します。
リポジトリの Secrets に以下を設定してください。

- `RAKUTEN_APP_ID`
- `DISCORD_WEBHOOK_URL`
- `RAKUTEN_AFFILIATE_ID`（任意）

## テスト

```bash
python -m pytest tests/ -v
```

## 通知条件の考え方

- ランクが `notify_rank`（既定 S/A）に入っている
- 利益 ≥ `min_profit`（既定 1,000円）かつ ROI ≥ `min_roi`（既定 10%）
- 「商品 × 実質仕入 × 売価」が同一の間は再通知しない（価格が動けば再通知）
