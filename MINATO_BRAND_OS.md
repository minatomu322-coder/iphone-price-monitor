# MINATO Brand OS

> **証明を毎日生産し、交流で届ける。** AIが投稿候補と交流先を選び、Discordへ通知し、**人間がiPhoneで動く**半自動ブランド構築システム。
> 目的はフォロワー増加ではなく **ブランド構築（信頼・仲間・コンサル見込み客）**。フォロワーは結果。

いいねも投稿も自動化しません。AIは「考える時間」を消す係、行動はあなた。

---

## 心臓部：Proof & Personality Engine

このリポジトリで24時間動いている **iPhone買取価格監視の実データ** から、毎日「証明できる投稿」を自動生成します。数字だけでなく **根拠・判断・学び** をセットで。

| 投稿タイプ | 内容 | 便 |
|---|---|---|
| 📊 Proof | 利益・価格・ROI（機械が記録した実測値） | 朝 |
| 🧠 Decision | なぜ買った/売った/待ったか | 朝 |
| 📝 Learning | 今日の学び・改善 | 朝 |
| 🫶 Personality | 失敗・実話・考え方（あなたの1行メモが素材） | 夜 |

**数字は機械が保証する。判断はあなたのブランド。** テンプレの「→」行を一言で埋めれば投稿完成。Claudeキーがあれば全文生成に昇格。

---

## いま動くもの

| # | 機能 | 状態 |
|---|------|------|
| ★ Proof Engine | 価格監視DB→Proof/Decision/Learning候補（朝便） | ✅ |
| ★ Personality Engine | 1行メモ→人柄投稿候補（夜便）。素材切れなら質問が届く | ✅ |
| ★ 投稿履歴・KPI・実験記録 | posted / kpi / metrics / report | ✅ |
| ① ターゲット収集 | 手動シードCSV → DB取り込み | ✅ |
| ②③ ブランド分析・優先順位 | 9軸採点 → ★1-5 | ✅ |
| ④ Discord通知 | 朝6:30 / 12:00 / 17:30 / 夜20:30 / 22:00 | ✅ |
| ⑤ リプ生成 | 22時のみ・コピペ用3案 | ✅ |
| ⑥ ブランドCRM | 全交流履歴・親密度・次回推奨日 | ✅ |

---

## 運用（iPhoneのみで完結）

### 毎日やること：Discordを見て動くだけ
- **06:30 朝便**：Proof候補3件（実データ入り）→ →行を埋めて **7-8時に投稿** → 添付の「絡むべき人」にいいね/リプ
- **12:00** 便：今日いいねする候補 ＋🔥最重要人物
- **17:30** 便：進捗リマインド ＋ 追加候補
- **20:30 夜便**：Personality候補3件 → **21-22時に投稿**
- **22:00** 便：今日リプする5-10人 ＋ **コピペ用リプ文**

### すきま時間：1行メモ（夜便の素材）
思いついたら `data/memos.csv` にiPhoneのGitHub webから1行追加（kind: fail/learn/story/thought）:
```csv
kind,text
fail,値下げ交渉を即OKしたら直後に定価で売れてた
```
PCなら: `python mbos.py memo --kind fail --text "..."`

### 投稿したら（実験データになる）
```bash
python mbos.py posted --draft 12                     # 投稿済みに
python mbos.py kpi --draft 12 --imp 4200 --likes 18 --views 45 --follows 2
python mbos.py metrics --followers 480 --views 120 --dms 1   # 週2回でOK
python mbos.py report    # タイプ別×時間帯別の成績（14日実験の答え）
```

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

## Proofソースの拡張（メルカリ・ポケカを足すには）

`minato_brand_os/proof/` にモジュールを1つ作り（`collect_facts() -> list[ProofFact]`）、
`proof/__init__.py` の `SOURCES` に1行足すだけ。朝便に自動で組み込まれます。

## ロードマップ

- [x] **Phase 1** 基盤（DB / config / Discord骨格）
- [x] **Phase 2** CRM（交流履歴・親密度・次回推奨日）
- [x] **Phase 3** AI分析（9軸★採点）
- [x] **Phase 4** 3便通知（12:00 / 17:30 / 22:00）
- [x] **Phase 6改** Proof & Personality Engine（朝便/夜便・投稿履歴・4タイプ管理）← 心臓
- [x] **Phase 8骨格** 実験記録（posted/kpi/metrics/report。14日回して判断）
- [ ] **Phase 7** 自ブランド分析の週次AIレポート（データが溜まり次第）
- [ ] **Phase 5** リプ勝ちパターン学習（リプ実績が数百件溜まる2ヶ月後・凍結中）
- [ ] **Phase 9** 収集自動化（X API。売上20万/月超 or ボトルネック実証まで契約禁止）
- [ ] **Phase 10** 利益統合ダッシュボード（メルカリ/ポケカのProofソース追加とセット）
