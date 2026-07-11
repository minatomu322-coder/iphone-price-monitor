# MINATO Brand OS

> AIが「今日交流する価値が最も高い人」を選び、Discordへ通知し、**人間がiPhoneで動く**半自動ブランド構築システム。
> 目的はフォロワー増加ではなく **ブランド構築（信頼・仲間・コンサル見込み客）**。フォロワーは結果。

いいねは自動で押しません。AIは「考える時間」を消す係、行動はあなた。

---

## いま動くもの（Phase 1–4：骨格）

| # | 機能 | 状態 |
|---|------|------|
| ① ターゲット収集 | 手動シードCSV → DB取り込み | ✅ |
| ②③ ブランド分析・優先順位 | 9軸採点 → ★1-5 | ✅（ヒューリスティック / Claudeキーで高精度化） |
| ④ Discord 3便通知 | 12:00 / 17:30 / 22:00 | ✅ |
| ⑤ リプ生成 | 22時のみ・コピペ用3案 | ✅（キー無し=雛形 / キー有り=相手投稿に最適化） |
| ⑥ ブランドCRM | 全交流履歴・親密度・次回推奨日 | ✅ |

Phase 5以降（投稿提案・自ブランド分析・実験モード・利益連携）は下部ロードマップ参照。

---

## 運用（iPhoneのみで完結）

### 毎日やること：Discordを見て動くだけ
- **12:00** 便：今日いいねする100人 ＋🔥最重要人物 → タップして👍
- **17:30** 便：進捗リマインド ＋ 追加候補
- **22:00** 便：今日リプする5-10人 ＋ **コピペ用リプ文** → そのまま貼る

### 週1回やること：候補を足す（収集）
`data/seeds/` にCSVを1枚置くだけ（GitHubのwebエディタでiPhoneから可）。
最低 `handle` 列だけあればOK。分かる範囲で他も埋めると採点精度が上がる。

```csv
handle,name,bio,followers,following,genre,recent_posts,engagement
poke_taro,ポケカ太郎,ポケカ投資と副業,3200,1800,ポケカ,今日の相場まとめ,3.2
```

### 交流の記録（CRM）
```bash
python mbos.py record --handle poke_taro --kind reply   # like/reply/follow/dm/meet
```
→ 親密度と次回推奨日が自動更新。（将来: Discordの👍リアクションで自動記録に拡張）

---

## コマンド一覧

```bash
python mbos.py collect                 # シードCSV → DB
python mbos.py analyze                 # 多軸採点 ＋ CRM再計算
python mbos.py notify --slot noon      # 12:00便
python mbos.py notify --slot evening   # 17:30便
python mbos.py notify --slot night     # 22:00便（リプ生成込み）
python mbos.py record --handle x --kind like
python mbos.py daily                   # collect → analyze（毎朝バッチ）
```

---

## セットアップ（サーバー不要・24時間稼働）

GitHub Actions が「24時間サーバー」。PCを開かなくても回ります。

1. **Discord Webhook** を作成し、リポジトリ Secrets に登録
   - `Settings → Secrets and variables → Actions → New repository secret`
   - Name: `MBOS_DISCORD_WEBHOOK_URL` / Value: WebhookのURL
2. （任意）**Claude採点**を有効化するなら Secret `ANTHROPIC_API_KEY` を追加
   - キーは https://console.anthropic.com で発行
   - 未設定でもヒューリスティック採点で動きます
3. スケジュールは `.github/workflows/mbos.yml`（JST 11:00/12:00/17:30/22:00）
   - 手動実行: Actions → MINATO Brand OS → Run workflow → action を選択

DBは `data/brand_os.sqlite3`。実行のたびGitHub Actionsが差分をコミットして状態を永続化します（外部DB不要）。

---

## 採点の仕組み（9軸 → ★）

`config.mbos.yaml` の `scoring.weights` で重み調整。ブランド構築を最優先に配分：

| 軸 | 意味 | 重み |
|----|------|------|
| brand_fit | ブランド相性（最重要） | 25 |
| consult_ltv | 将来コンサル見込み | 20 |
| interaction_value | 仲良くなる価値 | 15 |
| engagement | エンゲージ率 | 12 |
| growth | 伸び代 | 10 |
| personality | 人柄 | 8 |
| values_fit | 価値観の一致 | 5 |
| reply_rate | 絡みやすさ | 3 |
| followers | 規模（軽い＝数は目的でない） | 2 |

フォロワーは「スイートスポット」採点：大きすぎても絡めず、小さすぎても価値薄。
`personality`・`values` など文脈理解が要る軸は、キー無しでは中立値（Claudeキーで精緻化）。

---

## 設計思想（なぜこう作ったか）

- **深く・狭く**：1日100いいねは"認知の網"。主役は1日5-10人への本気の交流。数打ちはブランドを薄める。
- **North Star = コンサル導線に乗った見込み客数 × 深い関係の数**。フォロワー数ではない。
- **収集は抽象化**：いまは手動シード（0円・規約セーフ）。X API予算が付けば `x_client.py` に差し込むだけで無停止自動化へ。
- **コンテンツが主・交流が従**：伸びる投稿がないと交流しても刈り取れない → Phase 6の投稿提案が収益の心臓。

---

## ロードマップ

- [x] **Phase 1** 基盤（DB / config / Discord骨格）
- [x] **Phase 2** CRM（交流履歴・親密度・次回推奨日）
- [x] **Phase 3** AI分析（9軸★採点）
- [x] **Phase 4** 3便通知（12:00 / 17:30 / 22:00）
- [ ] **Phase 5** リプ勝ちパターン学習（返信/フォローに繋がったリプをフィードバック）
- [ ] **Phase 6** 投稿提案（朝/昼/夜3案＋投稿後の交流導線）← 収益の心臓
- [ ] **Phase 7** 自ブランド分析（プロフ閲覧/フォロー率/交流率の週次レポート）
- [ ] **Phase 8** 実験モード（14日ログ→最適時間/ジャンル/人数を提案）
- [ ] **Phase 9** 収集自動化（X API を x_client に差し込み）
- [ ] **Phase 10** 利益連携（iPhone価格監視＋メルカリ＋ポケカを1ダッシュボードへ）
