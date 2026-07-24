# Mimoza Bot 仕様書

Discord サーバー運営向けの多機能 Bot。マッチング（個通）、VC/テキストのランク、MPチケット経済、面接・プロフィール審査などを提供する。

---

## 1. 構成・技術

- **言語 / ライブラリ**: Python 3.12 / discord.py 2.7.1
- **DB**: PostgreSQL（Docker サービス名 `db`）
- **画像生成**: Pillow（VCランクカード）＋ Noto CJK フォント
- **音声**: PyNaCl（お試し個通のサウンドボード送信で使用）
- **ヘルスチェック**: `server.py`（FastAPI, ポート8080）をバックグラウンドスレッドで起動

### 起動方法
```bash
docker compose up -d          # Docker（推奨）
# または
pip install -r requirements.txt && python main.py
```

### エントリポイント / Cog 読み込み
`main.py` が Bot を生成し、`cogs/` 配下の `.py`（`setup(bot)` を持つもの）を再帰的に自動読み込み。スラッシュコマンドをグローバル＋`MY_GUILD`へ同期し、`server.py` を起動する。新機能は新しい cog ファイルとして追加する。

### DBアクセス基盤
`core/db_base.py` の `DatabaseBase` が PostgreSQL 接続（`get_db()`）を提供。DBを触る cog はこれを継承する。`core/admin_base.py` の `AdminCogBase` は `ADMIN_ROLE_ID` 定数を持ち、管理者限定コマンドで使う。

---

## 2. 環境変数

`.env.example` を `.env` にコピーして設定する。

### 基本
- `TOKEN` … Bot トークン（必須）
- `MY_GUILD` … スラッシュコマンド即時同期の対象サーバーID
- `ADMIN_ROLE_ID` … 管理者コマンドを使えるロール
- `PROFILE_TARGET_CHANNEL_IDS` … プロフィール自動表示の対象チャンネル（カンマ区切り）

### VCランク
- `EXCLUDED_CHANNEL_IDS` … VC時間の計測から除外するVC（カンマ区切り）
- `VC_RANK_REDUCED_CATEGORY_IDS` … VC時間の加算が 1/3 になるカテゴリ（カンマ区切り）

### ロール（雑談/恋愛）
- `ZERO_ROMANCE_ROLE_ID` … 恋愛の割合「0割」で付与される雑談ロール
- `ROMANCE_ROLE_ID` … 恋愛の割合「1割以上」で付与される恋愛ロール
- `ZERO_ROMANCE_HIDDEN_CATEGORY_ID` … 雑談ロール保持者から隠すカテゴリ
- `MALE_ROLE_ID` / `FEMALE_ROLE_ID` … 男女ロール
- `NEWCOMER_ROLE_ID` … 新人ロール（付与中は個通を使えない）

### 面接・審査
- `INTERVIEW_ROOM_CATEGORY_ID` … 面接/プロフィール用チャンネルを作成するカテゴリ
- `RECORDING_FORWARD_CHANNEL_ID` … 録音・審査・障害申告の運営転送先
- `SCORE_REVIEWER_COUNT` … 審査結果を出すのに必要な採点人数（既定4）

### 個通（1対1通話）
- `CALL_CATEGORY_ID` … 個通ルームのカテゴリ
- `CALL_LOG_CHANNEL_ID` … 個通の承認/辞退ログ先（未設定ならログなし）
- `MAX_ROOMS_PER_FEMALE` / `MAX_ROOMS_PER_MALE` … 同時に持てる部屋数の上限（既定3）
- `TRIAL_WARNING_SOUND` … お試し個通 残り5分で鳴らすサウンド（IDまたは名前）

### 一時VC（フリールーム）
- `LOBBY_VC_ID` … トリガーVC（カンマ区切りで複数可）
- `TEMP_VC_CATEGORY_ID` … 作成先カテゴリ（未指定ならトリガーVCと同じカテゴリ）

### MPショップ
- `MP_TEXT_CATEGORY_ID` … 個人専用テキストチャットの作成先カテゴリ
- `MP_LOG_CHANNEL_ID` … `/mp_give`・`/mp_take` のログ先
- `MOOD_PHOTO_ROLE_ID` … 雰囲気写真の閲覧ロール
- `MOOD_PHOTO_CHANNEL_ID` … 雰囲気写真チャンネル（未設定なら名前「雰囲気写真」で検索）
- `MOOD_PHOTO_COST` … 雰囲気写真の閲覧権のチケット消費枚数（既定3）

### DB接続（任意・未設定ならDockerの`db`向け既定値）
`DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASS` / `DB_NAME` / `DB_SSLMODE`

---

## 3. データベース

`init.sql`（新規ボリューム時のみ実行、既存DBには各cogが起動時にテーブル/カラムを補完）

- `users(user_id, vc_minutes_total, rank, text_count, text_rank, mp_tickets)` … ユーザーの累計VC時間・VCランク・テキスト数・テキストランク・MPチケット
- `trial_invites(recruiter_id, target_id, invited_at)` … お試し個通の誘い履歴
- `call_blocks(blocker_id, blocked_id, created_at)` … 個通のブロック
- `call_room_limits(user_id, max_rooms)` … 個人ごとの部屋数上限（1件制限）
- `temp_vcs(channel_id, owner_id, guild_id)` … 一時VC
- `recording_scores(message_id, reviewer_id, submitter_id, s_profile, s_voice, s_talk, s_character, reason)` … 録音採点
- `recording_results(message_id)` … 審査結果出力済みフラグ
- `pending_interview(user_id, embed_json)` … 録音とプロフィールの待ち合わせ用
- `mp_text_channels(user_id, channel_id)` … MPチケットで作った個人テキストチャット（1人1つ判定）
- `mood_photo_deadlines(user_id, deadline)` … 雰囲気写真の画像投稿ノルマ期限
- `role_switch_cooldowns(user_id, last_switch)` … 雑談/恋愛切替のクールタイム

---

## 4. スラッシュコマンド

### 一般ユーザー向け
- `/topic` … 雑談のネタをランダムに出す
- `/rank` … VC/テキストのランクカード（Pillow画像）を表示

### 管理者専用（`ADMIN_ROLE_ID` が必要）
- `/set_appeal_panel` … 面接・面談パネルを設置
- `/set_call_panel` … 個通募集パネルを設置
- `/set_role_panel` … 雑談/恋愛ロールの切替パネルを設置
- `/set_mp_panel` … MPチケット交換パネルを設置
- `/mp_give` / `/mp_take` … MPチケットの配布 / 没収（`MP_LOG_CHANNEL_ID` にログ）
- `/mp_list` … メンバーのMPチケット所持数を一覧表示
- `/admin_block_list` … 個通のブロック状況を確認

---

## 5. 機能詳細

### 5.1 VCランク / テキストレベル（`vc_rank.py`）
- **VC時間の計測**: 1分ごとの `tasks.loop` でVC在室者の時間を加算。起動時に既に在室しているメンバーも計測対象に登録する。
- **VCランク**: 5時間（300分）ごとに1レベル。`VC_RANK_REDUCED_CATEGORY_IDS` のカテゴリでは加算が 1/3（端数は持ち越し）。`EXCLUDED_CHANNEL_IDS` は計測対象外。
- **MPチケット**: VCランクが1レベル上がるごとに1枚配布。
- **テキストレベル**: メッセージ投稿で加算（スパム防止に60秒クールダウン）。
- **`/rank`**: アバター・VCレベル・テキストレベルの2本の進捗バー・リーダーボード順位を描いたカード画像を返す。

### 5.2 面接・プロフィール・審査（`interview_room.py` / `profile_wizard.py` / `recording_score.py`）
- **`/set_appeal_panel`**: 男性＝面接録音チャンネル / 女性＝プロフィール記載チャンネルを、押した人ごとに作成。両チャンネルに「プロフィールを作成する」ボタンを設置。
- **プロフィール作成ウィザード**: 名前・趣味・タイプ・障害欄の入力モーダル → 年齢/居住地/職種などのセレクト、基本情報/ライフスタイルの RadioGroup モーダル、MBTI の2段階選択（大項目→タイプ）を経てプロフィールを投稿。
- **障害・ハンデ欄**: 任意入力。公開せず `RECORDING_FORWARD_CHANNEL_ID`（運営）にのみ共有。
- **録音提出（男性）**: モーダルではなく、面接チャンネルへ音声ファイル or Discordの録音機能で投稿する。
- **審査への流れ**: 「録音の投稿」と「プロフィール作成」が両方揃った時点（順序不問。プロフ先行時は `pending_interview` で待機）で、プロフィール＋音声＋採点ボタンを運営チャンネルへ転送。
- **採点**: レビュアーが4項目（プロフとの整合性・聞き取りやすさ/イケボ・トーク力・人柄、各0〜2点）を RadioGroup モーダルで採点。0点をつけたら理由が必須（0点なしなのに理由があると弾く）。
- **審査結果**: `SCORE_REVIEWER_COUNT` 人が採点したら、平均点・合否（合計5点以上で合格）・0点をつけた人（誰が/どの項目/理由）を Components V2 の区切り線付きパネルで、管理者メンション付きで投稿。

### 5.3 個通（1対1通話マッチング）（`call_matching.py`）
- **`/set_call_panel`**: 募集パネルを設置。男↔女で相手を選び、Botが代理でDMお誘いを送信。相手はDMボタンで受ける/断る。承認で2人専用のVC＋テキストルームを作成。
- **ログ**: 承認/辞退を `CALL_LOG_CHANNEL_ID` に記録。
- **お試し個通**: VC入室から30分で自動終了。残り5分でメンション警告＋Botが一時的にVCへ入りサウンドボードを鳴らす（`TRIAL_WARNING_SOUND`）。同じ相手には1回まで（`trial_invites`）。
- **ブロック編集**: `CheckboxGroup` モーダルでブロックの追加/解除。ブロック関係の相手は双方向でお誘い一覧から消える（`call_blocks`）。お誘いを断ると相手を自動ブロック。
- **人数制限**: 自分の同時部屋数を1件に制限（部屋を1件以上持っているときのみ設定可）。
- **部屋数上限**: 男女それぞれ既定3件。上限到達者はお誘い一覧に出ない。
- **新人ロール**: `NEWCOMER_ROLE_ID` 保持者は個通（募集・被お誘い・承認・ブロック編集）を利用不可。

### 5.4 一時VC / フリールーム（`temp_vc.py`）
- `LOBBY_VC_ID`（複数可）に入ると個人用VCを作成し本人を移動。VCのテキストチャットに作成者メンション＋「VC名を変更する」パネルを表示。
- 全員が退出すると自動削除（即時＋5分ごとの掃除タスク）。

### 5.5 ロール切り替え（`role_switch.py`）
- `/set_role_panel` で雑談/恋愛の切替パネルを設置。2ロールは排他。
- **クールタイム**: 切り替えはユーザーごとに2週間に1回まで（`role_switch_cooldowns`）。プロフィール作成でのロール付与はクールタイム対象外。
- 雑談ロールにすると `ZERO_ROMANCE_HIDDEN_CATEGORY_ID` のカテゴリが非表示になる。

### 5.6 MPチケット / MPショップ（`mp_shop.py`）
- **チケット入手**: VCレベルアップで1枚（`users.mp_tickets`）。
- **`/set_mp_panel`**: 「🎫 チケットを確認」ボタン＋商品セレクト。
- **商品**:
  - お試し個通のリセット … 10枚（`trial_invites` を削除）
  - 個人専用テキストチャット作成 … 6枚（名前＋閲覧ロール〔男/女から1つ以上必須〕。1人1つ）
  - ロール作成 … 1枚（名前＋色。最下位に作成して本人に付与）
  - 雰囲気写真の閲覧権 … `MOOD_PHOTO_COST`枚（付与後24時間以内に「雰囲気写真」チャンネルへ画像投稿しないと没収）
  - サーバー絵文字を追加 … 1枚（画像アップロードで絵文字化。256KB以下）
- **管理**: `/mp_give`・`/mp_take`（`MP_LOG_CHANNEL_ID` にログ）、`/mp_list`。

### 5.7 その他
- **`talk.py`**: `/topic`（`topics.json` からランダム）。
- **`profile.py`**: プロフィールの自動表示（`PROFILE_TARGET_CHANNEL_IDS`）など。

---

## 6. バックグラウンドタスク（`@tasks.loop`）
- `vc_rank.py` … 1分ごとにVC時間を計測・ランク更新
- `call_matching.py` … お試し個通の残り時間監視（1分ごと・残り5分で警告、30分で終了）
- `temp_vc.py` … 空の一時VCの掃除（5分ごと）
- `mp_shop.py` … 雰囲気写真ノルマ未達の没収（5分ごと）

---

## 7. 運用メモ
- 各種テーブル/カラムは既存DBにも起動時に自動追加されるため、原則マイグレーション不要。
- パネル（個通・面接・ロール切替・MP）は設置時のメッセージに固定されるため、説明文や商品を変更したら `/set_*_panel` で置き直す。
- Bot には「チャンネルの管理」「メンバーの移動」「ロールの管理」「絵文字の管理」など、機能に応じた権限が必要。
