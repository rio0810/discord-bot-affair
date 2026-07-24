-- 1. ユーザー管理テーブル
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    vc_minutes_total INT DEFAULT 0,
    rank INTEGER DEFAULT 0,
    text_count INT DEFAULT 0,
    text_rank INTEGER DEFAULT 0,
    mp_tickets INT DEFAULT 0,
    user_name TEXT
);

-- 2. お試し通話の誘った履歴（同じ相手を2度誘えないようにする）
CREATE TABLE IF NOT EXISTS trial_invites (
    recruiter_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    invited_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (recruiter_id, target_id)
);

-- 3. 個通のブロック（ブロック関係の相手はお互いにお誘い一覧へ表示されない）
CREATE TABLE IF NOT EXISTS call_blocks (
    blocker_id BIGINT NOT NULL,
    blocked_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (blocker_id, blocked_id)
);

-- 4. 個人ごとの個通部屋数上限（パネルのボタンで1件に制限した人のみ行が存在）
CREATE TABLE IF NOT EXISTS call_room_limits (
    user_id BIGINT PRIMARY KEY,
    max_rooms INT NOT NULL
);

-- 6. 一時VC（トリガーVC入室で作成された個人VC。空になったら削除）
CREATE TABLE IF NOT EXISTS temp_vcs (
    channel_id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL
);

-- 7. 録音の採点（4項目×0〜2点。規定人数で平均を出す）
CREATE TABLE IF NOT EXISTS recording_scores (
    message_id BIGINT NOT NULL,
    reviewer_id BIGINT NOT NULL,
    submitter_id BIGINT NOT NULL,
    s_profile INT NOT NULL,
    s_voice INT,           -- 女性は採点しないため NULL 許可
    s_talk INT,            -- 女性は採点しないため NULL 許可
    s_character INT NOT NULL,
    reason TEXT,
    kind CHAR(1) DEFAULT 'm',  -- 'm'=男性(4項目) / 'f'=女性(2項目)
    PRIMARY KEY (message_id, reviewer_id)
);
CREATE TABLE IF NOT EXISTS recording_results (
    message_id BIGINT PRIMARY KEY
);
-- 録音とプロフィールの待ち合わせ（プロフ先行時に埋め込みを保持）
CREATE TABLE IF NOT EXISTS pending_interview (
    user_id BIGINT PRIMARY KEY,
    embed_json TEXT NOT NULL
);
-- 審査へ回済みの人（録音先行時の催促を止める判定用）
CREATE TABLE IF NOT EXISTS interview_done (
    user_id BIGINT PRIMARY KEY
);
-- 合否判定済みの人（二重判定・二重BAN防止）
CREATE TABLE IF NOT EXISTS interview_verdicts (
    submitter_id BIGINT PRIMARY KEY
);

-- 8. MPチケットで作成した個人テキストチャット（1人1つまで判定用）
CREATE TABLE IF NOT EXISTS mp_text_channels (
    user_id BIGINT PRIMARY KEY,
    channel_id BIGINT NOT NULL
);

-- 9. 雰囲気写真の閲覧権：24h以内の画像投稿ノルマ期限
CREATE TABLE IF NOT EXISTS mood_photo_deadlines (
    user_id BIGINT PRIMARY KEY,
    deadline TIMESTAMP NOT NULL
);

-- 10. 雑談/恋愛ロール切り替えのクールタイム（2週間に1回）
CREATE TABLE IF NOT EXISTS role_switch_cooldowns (
    user_id BIGINT PRIMARY KEY,
    last_switch TIMESTAMP NOT NULL
);
