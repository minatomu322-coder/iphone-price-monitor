# 利益商品AI（rakuten_finder）設計ドキュメント

楽天市場などから商品を巡回し、メルカリ相場と比較して「利益が出そうな商品」だけを
Discord に通知する、せどり支援ツール。既存の iPhone 買取モニターとは独立した
`rakuten_finder/` パッケージとして実装する。

> **重要な方針**
> - 自動購入はしない。購入判断は必ず人間が行う。
> - 楽天は公式 API を優先利用（スクレイピング不要で壊れにくい）。
> - メルカリは公式検索 API が存在せず、スクレイピングは規約リスクが高いため、
>   MVP では相場を「手入力 / CSV / 仮データ」で投入する。`MercariSource` を
>   インターフェース化し、将来は正規手段に差し替え可能にする。

---

## 1. ディレクトリ構成

```
rakuten_finder/
  __init__.py
  config.py         # .env + yaml のロード
  models.py         # RakutenItem / MercariStats / ProfitResult / ScoreResult
  rakuten_api.py    # 楽天市場商品検索API クライアント
  mercari.py        # 相場ソース（MVP=CSV/手入力）。IF 抽象化
  profit.py         # 利益計算（ポイント/クーポン/手数料/送料）
  scoring.py        # S/A/B/C/D スコアリング + 学習反映
  database.py       # SQLite（products/observations/decisions/notifications/errors）
  notifier.py       # Discord 通知（利益/日次/エラー）
  pipeline.py       # 巡回→取得→計算→採点→保存→通知
  cli.py            # python -m rakuten_finder.cli run|report
  dashboard.py      # FastAPI ダッシュボード + 判断記録
  templates/index.html
config/rakuten_finder.yaml
data/mercari_prices.sample.csv
tests/
.env.example
Dockerfile
docker-compose.yml
requirements-rakuten.txt
```

## 2. DB 設計（SQLite → 将来 PostgreSQL）

- **products**: `item_code`(PK) / jan / name / keyword / genre_id / image_url /
  first_seen_at / last_seen_at
- **observations**: id / item_code / observed_at / rakuten_price / shipping_in /
  point_rate / super_deal_rate / coupon / **effective_cost** / mercari_price /
  mercari_min / mercari_sold / mercari_active / stability /
  **profit / roi / margin / score / rank** / in_stock / shop_name / url
- **decisions**（学習）: id / item_code / decided_at / decision(buy|skip|hold) / note
- **notifications**（重複防止）: id / dedupe_key(UNIQUE) / item_code / notified_at
- **errors**: id / occurred_at / context / message

## 3. 処理フロー

```
巡回(cron 30〜60分)
 → 楽天API検索（対象キーワード毎）
 → 商品データ取得（価格/送料/ポイント/DEAL/在庫/画像/JAN）
 → メルカリ相場ルックアップ（JAN → キーワード）
 → 利益計算（実質仕入 = 価格 + 送料 - クーポン - ポイント合計）
 → スコアリング（利益/ROI/回転/希少性/安定性 + 学習ブースト）→ S〜D
 → SQLite 保存（products / observations）
 → しきい値 & ランク合致のみ Discord 通知（dedupe で重複抑制）
 → ダッシュボードで一覧・判断記録 → decisions に保存 → 次回採点へ反映
 → 日次レポート（Sランク / 利益 / ROI / 回転ランキング）
```

## 4. 利益計算式

```
point_total   = floor(price * (base_rate + spu_rate + campaign_rate + super_deal_rate) / 100)
                （point_cap > 0 なら上限でクリップ）
effective_cost = price + shipping_in - coupon - point_total
sell_price     = メルカリ相場（median / min / avg を選択）
mercari_fee    = round(sell_price * fee_rate)   # 既定 10%
profit         = sell_price - mercari_fee - shipping_out - effective_cost
roi            = profit / effective_cost
margin         = profit / sell_price
```

## 5. スコアリング

各指標を 0..1 に正規化し、重み付き合計（0..100）→ ランク化。

- 指標: profit / roi / turnover(売れた件数) / scarcity(出品数の少なさ) / stability(相場安定性)
- 学習ブースト: `decisions` からキーワード別の buy 率を算出し加点/減点
- ゲート: min_profit / min_roi を満たさない場合は上位ランクを付けない
- ランク: score>=80→S, >=65→A, >=50→B, >=35→C, else D

## 6. .env 例

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxx
RAKUTEN_APP_ID=your_rakuten_application_id
RAKUTEN_AFFILIATE_ID=
RAKUTEN_FINDER_DB=data/rakuten_finder.sqlite3
```

## 7. ロードマップ

- **v0.1 (MVP)**: 楽天API取得 / 相場=CSV / 利益計算 / S〜D / Discord通知 / SQLite / 一覧ダッシュボード
- **v0.2**: 学習ブースト高度化 / 日次レポート / 重複通知抑制 / 在庫復活検知
- **v0.3**: 楽天ブックス・楽天24・スーパーDEAL・クーポン会場の巡回追加 / ポイント倍率上昇検知
- **v0.4**: メルカリ相場の正規自動取得 / 相場急騰検知
- **v0.5**: Discord ボタン（interaction）で判断記録 / PostgreSQL 移行 / クラウド常時稼働
