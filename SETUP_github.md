# GitHub Actions でクラウド自動実行する手順

このツールを **自分のMacを起動していなくても** 30分ごとに自動で動かすための設定手順です。
専門知識がなくても進められるよう、順番どおりに進めてください。所要 15〜20分程度。

---

## 全体の流れ

1. GitHubで新しいリポジトリ（保管場所）を作る
2. このフォルダのファイルをアップロードする
3. Discordの通知先（Webhook URL）を「秘密の設定」として登録する
4. Actions（自動実行の仕組み）を有効にする
5. 手動で1回動かして、Discordに通知が来るか確認する

---

## 手順1：新しいリポジトリを作る

1. ブラウザで https://github.com を開いてログイン。
2. 右上の「＋」→「New repository」。
3. Repository name に例：`iphone-price-monitor`。
4. **「Private」（非公開）を選択**（買取価格の履歴などを公開しないため推奨）。
5. 「Create repository」をクリック。

---

## 手順2：ファイルをアップロードする

作成直後のページにある「uploading an existing file」リンク、または
「Add file」→「Upload files」から、このフォルダの中身をドラッグ＆ドロップします。

### アップロードする（必要なファイル）

- `main.py` `scraper.py` `notifier.py` `decision.py` `database.py` `app.py`
- `config.yaml`
- `requirements.txt`
- `prices.sqlite3`（価格履歴のDB。これまでの履歴を引き継ぐためアップロード推奨）
- `README.md`
- `.gitignore`
- `.github` フォルダ（中の `workflows/monitor.yml` が自動実行の設定）

> `.github` はドット始まりのフォルダです。ドラッグ＆ドロップでフォルダごと入れれば中身も一緒に上がります。

### 絶対にアップロードしない（重要）

- **`webhook.txt`** … Discordの秘密URLが入っています。**公開厳禁**。通知先は手順3で「秘密の設定」として登録します。
- `*.bak` `*.bak2` `*.bak_ipv4` などの **バックアップ類**（不要）
- `__pycache__` フォルダ、`*.pyc`（自動生成キャッシュ）
- `*.pid` `*.log`（`app.log` `tunnel.log` など実行時のゴミ）
- `テスト実行.command` `お掃除.command`（Mac専用。クラウドでは不要）

> ※ アップロードは「必要なファイルだけを選んで」ドラッグするのが安全です。フォルダ全体をドラッグする場合は、上の「アップロードしない」ものを外してください。

最後に緑の「Commit changes」をクリック。

---

## 手順3：Discordの通知先を「秘密の設定」に登録する

1. リポジトリの「Settings」（歯車）タブを開く。
2. 左メニュー「Secrets and variables」→「Actions」。
3. 「New repository secret」をクリック。
4. **Name** に正確に： `DISCORD_WEBHOOK_URL`
5. **Secret** に、あなたのDiscord Webhook URL（`https://discord.com/api/webhooks/…`）を貼り付け。
   - この値は `webhook.txt` に入れているものと同じでOK。
6. 「Add secret」をクリック。

> この値は暗号化されて保存され、画面上で再表示されません。安全です。

---

## 手順4：Actions を有効にする

1. リポジトリの「Actions」タブを開く。
2. 「I understand my workflows, go ahead and enable them」などの案内が出たら有効化。
3. 左に「iPhone買取価格モニター」というワークフローが表示されればOK。

---

## 手順5：手動で1回動かして確認する

1. 「Actions」タブ →「iPhone買取価格モニター」を選択。
2. 右側「Run workflow」→「Run workflow」（緑ボタン）をクリック。
3. 1〜2分で実行が始まります。実行をクリックするとログが見られます。
4. うまくいけば **Discordのチャンネルにアラート通知** が届きます。
   - ログの「価格監視を実行」ステップで、各サイトの取得状況が確認できます。
   - `prices.sqlite3` に変化があれば、最後のステップで自動的にリポジトリへ保存されます（コミット履歴に `price data update` が増えます）。

---

## その後（自動実行について）

- 設定後は **30分ごとに自動実行** されます（GitHubの時計はUTC基準ですが、価格の日付処理は日本時間で行われます）。
- GitHubの無料枠でも十分動きます（混雑時は数分遅れる場合があります）。
- 通知先を変えたいときは、手順3のSecretの値を更新するだけです。
- 監視対象や価格の設定を変えたいときは `config.yaml` をGitHub上で編集すればOK（次回実行から反映）。

---

## うまくいかないとき

- **Discordに来ない**：手順3のSecret名が `DISCORD_WEBHOOK_URL` と完全一致しているか確認。値が正しいWebhook URLか確認。
- **実行が赤（失敗）**：Actionsのログを開き、どのステップで失敗したか確認。`海峡通信`など一部サイトの取得失敗は、他サイトが取れていれば通知自体は動きます。
- 困ったらログ画面のテキストをそのまま共有してください。
