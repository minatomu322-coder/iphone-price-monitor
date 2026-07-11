# iphone-price-monitor

iPhone買取価格の監視（既存機能）と、メルカリ販売管理・ChatGPT連携（新機能）を含むリポジトリです。

## 1. iPhone買取価格監視（既存）

買取店の価格をスクレイピングしてSQLiteへ保存し、変動をDiscordへ通知します。

```bash
pip install -r requirements.txt
python main.py        # 価格取得と通知
python app.py         # 監視ダッシュボード（ポート8765）
```

設定は `config.yaml`、GitHub Actionsによる定期実行は `.github/workflows/monitor.yml` を参照。

## 2. メルカリ販売管理・ChatGPT連携（新規）

仕入れ候補〜出品〜売却〜月次分析までを記録し、各段階で**ChatGPTへそのまま貼り付けられる分析用データ**
（テキスト/JSON/CSV/Markdown）を出力します。

```bash
python -m mercari.cli serve   # ダッシュボード（ポート8766）
```

- 役割分担と運用フロー: [docs/mercari_operations.md](docs/mercari_operations.md)
- ChatGPT用プロンプト集: [docs/chatgpt_prompts.md](docs/chatgpt_prompts.md)
- 設定: `mercari_config.yaml`（手数料率・一次判定基準・売れ残り日数など）

主な機能:

- 商品・仕入れ・相場・出品・値下げ・売却・改善履歴の記録（SQLite: `mercari.sqlite3`）
- 利益/ROI/損益分岐の自動計算と、仕入れ候補の一次判定（買い候補/条件付き/見送り/追加確認）
- 「仕入れ判断用」「出品作成用」「売れ残り分析用」「売上分析用」のChatGPT向け出力とコピー用ボタン
- ChatGPTが生成した出品用JSONの下書き取り込み（本出品はユーザーが手動で実施）
- 一次判定を通過した仕入れ候補のDiscord通知（`notify-candidates`）
- バックアップ・CSV書き出しコマンド

テスト:

```bash
python -m unittest tests.test_mercari
```
