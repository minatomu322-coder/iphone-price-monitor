# LINE 抽選管理BOT

LINE Messaging API を使った抽選管理BOTです。ユーザーがトークで「応募」と送るとエントリーされ、主催者（管理者）が「抽選 N」で当選者をランダム選出、「当選通知」で当選者へ自動通知します。

- **技術構成**: Python / Flask / LINE Messaging API (line-bot-sdk v3) / SQLAlchemy
- **DB**: PostgreSQL（Render）※ ローカルでは SQLite に自動フォールバック
- **デプロイ**: Render 無料プラン（`render.yaml` Blueprint 対応）

## ファイル構成

```
line_lottery_bot/
├── app.py            # Flask アプリ本体（Webhook・コマンド処理・DB モデル）
├── requirements.txt  # 依存パッケージ
├── render.yaml       # Render Blueprint（Web Service + PostgreSQL）
├── .env.example      # 環境変数サンプル
└── README.md
```

## コマンド一覧

### 一般ユーザー
| コマンド | 動作 |
|---------|------|
| `応募` | 抽選にエントリー（1人1回・重複不可） |
| `ヘルプ` / `help` | 使い方を表示 |

### 管理者（`ADMIN_USER_IDS` に含まれる userId のみ）
| コマンド | 動作 |
|---------|------|
| `締め切り` | 応募受付を終了 |
| `再開` | 応募受付を再開 |
| `抽選 3` | 3名をランダム選出して結果を返信（再抽選可） |
| `当選通知` | 当選者全員へ push message を送信 |
| `応募一覧` | エントリー済み一覧 |
| `当選者一覧` | 当選者一覧 |
| `リセット` | 全データ初期化（受付中に戻す） |

## 環境変数

| 変数 | 必須 | 説明 |
|------|------|------|
| `CHANNEL_SECRET` | ✅ | LINE チャネルシークレット |
| `CHANNEL_ACCESS_TOKEN` | ✅ | LINE チャネルアクセストークン（長期） |
| `ADMIN_USER_IDS` | ✅ | 管理者の LINE userId（カンマ区切り） |
| `DATABASE_URL` | － | PostgreSQL 接続文字列。未設定なら SQLite |

## ローカルでの動作確認

```bash
cd line_lottery_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 値を編集
export $(grep -v '^#' .env | xargs)

python app.py          # http://localhost:8000 で起動
```

Webhook を外部公開するには [ngrok](https://ngrok.com/) などでトンネルを張り、
`https://<公開URL>/callback` を LINE の Webhook URL に設定します。

## Render へのデプロイ手順（PostgreSQL 構成）

### 1. LINE 側の準備
1. [LINE Developers](https://developers.line.biz/) で **Messaging API チャネル** を作成
2. **チャネルシークレット** と **チャネルアクセストークン（長期）** を控える
3. 「応答メッセージ」を **オフ**、「Webhook」を **オン** にする

### 2. このリポジトリを Render へ
1. コードを GitHub に push
2. Render ダッシュボード → **New +** → **Blueprint** を選択
3. 対象リポジトリを選ぶと `render.yaml` が読み込まれ、
   Web Service（`line-lottery-bot`）と PostgreSQL（`line-lottery-db`）がまとめて作成される
4. Web Service の **Environment** で以下を設定（`sync: false` の項目）
   - `CHANNEL_SECRET`
   - `CHANNEL_ACCESS_TOKEN`
   - `ADMIN_USER_IDS`
   - （`DATABASE_URL` は Blueprint により自動注入）
5. デプロイ完了後、`https://<サービス名>.onrender.com/` にアクセスして
   `LINE lottery bot is running.` が表示されれば起動 OK

### 3. Webhook URL の設定
LINE Developers の Messaging API 設定で、Webhook URL を次のように設定して「検証」を実行：

```
https://<サービス名>.onrender.com/callback
```

### 4. 動作確認
1. BOT を友だち追加
2. `応募` と送る → 受付メッセージが返る
3. 管理者アカウントから `抽選 1` → 当選者が返信される
4. `当選通知` → 当選者へ push 通知が届く

## 補足

- **管理者 userId の調べ方**: 一度 BOT にメッセージを送り、Render のログに出る Webhook の `userId`、または LINE Developers コンソールの「あなたのユーザーID」で確認できます。
- **無料プランの制約**: Render 無料プランは一定時間アクセスがないとスリープし、次回アクセス時に起動に数十秒かかります。無料 PostgreSQL にも保持期間の制限があります（本番運用では有料プラン推奨）。
- **再抽選**: `抽選 N` を再実行すると、それまでの当選者はクリアされ再選出されます。
