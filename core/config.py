"""環境変数の一元管理。

各 cog が個別に os.getenv するのをやめ、設定はすべてここで読む。
綴り間違いや既定値の食い違いを防ぎ、「どのIDがどこで使われているか」を
このファイルだけで把握できるようにするのが目的。

使い方::

    from core import config
    role = guild.get_role(config.NEWCOMER_ROLE_ID)

未設定の ID はすべて 0（＝無効）になるので、呼び出し側は
`if config.XXX_ID:` で有効・無効を判定する。
"""

import os
from pathlib import Path

import dotenv

dotenv.load_dotenv()

# --------------------------------------------------------------------- #
# 変換ヘルパー
# --------------------------------------------------------------------- #


def env_int(name: str, default: int = 0) -> int:
    """整数の環境変数を読む（未設定・空文字・不正値なら default）。"""
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float = 0.0) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_id_list(name: str) -> list[int]:
    """カンマ区切りのID列を読む（空要素・不正値は無視）。"""
    ids = []
    for part in (os.getenv(name) or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


# --------------------------------------------------------------------- #
# パス
# --------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
ICON_DIR = ASSETS_DIR / "icons"

# --------------------------------------------------------------------- #
# Bot 本体
# --------------------------------------------------------------------- #
TOKEN = os.environ.get("TOKEN")
MY_GUILD_ID = env_int("MY_GUILD")

# --------------------------------------------------------------------- #
# ロール
# --------------------------------------------------------------------- #
ADMIN_ROLE_ID = env_int("ADMIN_ROLE_ID")
MALE_ROLE_ID = env_int("MALE_ROLE_ID")
FEMALE_ROLE_ID = env_int("FEMALE_ROLE_ID")
NEWCOMER_ROLE_ID = env_int("NEWCOMER_ROLE_ID")
MEMBER_ROLE_ID = env_int("MEMBER_ROLE_ID")
WAITING_ROLE_ID = env_int("WAITING_ROLE_ID")
# 審査ロールは未設定なら待機ロールで代用する
REVIEW_ROLE_ID = env_int("REVIEW_ROLE_ID") or WAITING_ROLE_ID
ZERO_ROMANCE_ROLE_ID = env_int("ZERO_ROMANCE_ROLE_ID")
ROMANCE_ROLE_ID = env_int("ROMANCE_ROLE_ID")
MOOD_PHOTO_ROLE_ID = env_int("MOOD_PHOTO_ROLE_ID")

# DM・フレンド申請の可否（プロフィールウィザードの選択肢 → ロール）
DM_OPEN_ROLE_ID = env_int("DM_OPEN_ROLE_ID")
DM_CLOSED_ROLE_ID = env_int("DM_CLOSED_ROLE_ID")
DM_ACQUAINTED_ROLE_ID = env_int("DM_ACQUAINTED_ROLE_ID")
DM_ASK_ROLE_ID = env_int("DM_ASK_ROLE_ID")

# --------------------------------------------------------------------- #
# カテゴリ
# --------------------------------------------------------------------- #
WAITING_CATEGORY_ID = env_int("WAITING_CATEGORY_ID")
ZERO_ROMANCE_HIDDEN_CATEGORY_ID = env_int("ZERO_ROMANCE_HIDDEN_CATEGORY_ID")
INTERVIEW_ROOM_CATEGORY_ID = env_int("INTERVIEW_ROOM_CATEGORY_ID")
CALL_CATEGORY_ID = env_int("CALL_CATEGORY_ID")
TEMP_VC_CATEGORY_ID = env_int("TEMP_VC_CATEGORY_ID")
MP_TEXT_CATEGORY_ID = env_int("MP_TEXT_CATEGORY_ID")
VC_RANK_REDUCED_CATEGORY_IDS = env_id_list("VC_RANK_REDUCED_CATEGORY_IDS")

# --------------------------------------------------------------------- #
# チャンネル・フォーラム
# --------------------------------------------------------------------- #
ERROR_LOG_CHANNEL_ID = env_int("ERROR_LOG_CHANNEL_ID")
JOIN_LEAVE_LOG_CHANNEL_ID = env_int("JOIN_LEAVE_LOG_CHANNEL_ID", 1530463807418273913)
VC_LOG_CHANNEL_ID = env_int("VC_LOG_CHANNEL_ID")
MESSAGE_LOG_CHANNEL_ID = env_int("MESSAGE_LOG_CHANNEL_ID")
CALL_LOG_CHANNEL_ID = env_int("CALL_LOG_CHANNEL_ID")
MP_LOG_CHANNEL_ID = env_int("MP_LOG_CHANNEL_ID")
PROFILE_TARGET_CHANNEL_IDS = env_id_list("PROFILE_TARGET_CHANNEL_IDS")
RECORDING_FORWARD_CHANNEL_ID = env_int("RECORDING_FORWARD_CHANNEL_ID")
RECORDING_FORUM_MALE_ID = env_int("RECORDING_FORUM_MALE_ID")
RECORDING_FORUM_FEMALE_ID = env_int("RECORDING_FORUM_FEMALE_ID")
NEWCOMER_REVIEW_FORUM_ID = env_int("NEWCOMER_REVIEW_FORUM_ID")
MALE_PROFILE_CHANNEL_ID = env_int("MALE_PROFILE_CHANNEL_ID")
FEMALE_PROFILE_CHANNEL_ID = env_int("FEMALE_PROFILE_CHANNEL_ID")
ZERO_ROMANCE_PROFILE_CHANNEL_ID = env_int("ZERO_ROMANCE_PROFILE_CHANNEL_ID")
GUIDELINE_CHANNEL_ID = env_int("GUIDELINE_CHANNEL_ID")
MOOD_PHOTO_CHANNEL_ID = env_int("MOOD_PHOTO_CHANNEL_ID")

# --------------------------------------------------------------------- #
# ボイスチャンネル
# --------------------------------------------------------------------- #
EXCLUDED_CHANNEL_IDS = env_id_list("EXCLUDED_CHANNEL_IDS")
# VCログを出さないVC・カテゴリ（未設定なら全VCを記録する）
VC_LOG_EXCLUDED_CHANNEL_IDS = env_id_list("VC_LOG_EXCLUDED_CHANNEL_IDS")
MESSAGE_LOG_EXCLUDED_CHANNEL_IDS = env_id_list("MESSAGE_LOG_EXCLUDED_CHANNEL_IDS")
LOBBY_VC_IDS = env_id_list("LOBBY_VC_ID")

# --------------------------------------------------------------------- #
# 個通マッチング
# --------------------------------------------------------------------- #
MAX_ROOMS_PER_FEMALE = env_int("MAX_ROOMS_PER_FEMALE", 2)
MAX_ROOMS_PER_MALE = env_int("MAX_ROOMS_PER_MALE", 2)
TRIAL_WARNING_SOUND = env_str("TRIAL_WARNING_SOUND")

# --------------------------------------------------------------------- #
# 審査
# --------------------------------------------------------------------- #
# 未提出のまま参加から何時間で自動BANするか（0＝無効）
UNSUBMITTED_BAN_HOURS = env_float("UNSUBMITTED_BAN_HOURS")

# --------------------------------------------------------------------- #
# MPショップ
# --------------------------------------------------------------------- #
MOOD_PHOTO_COST = env_int("MOOD_PHOTO_COST", 3)

# --------------------------------------------------------------------- #
# データベース
# --------------------------------------------------------------------- #
DB_CONFIG = {
    "host": env_str("DB_HOST", "db"),
    "port": env_int("DB_PORT", 5432),
    "user": env_str("DB_USER", "user"),
    # パスワードは前後の空白も意味を持ちうるので strip しない
    "password": os.getenv("DB_PASS") or "password",
    "dbname": env_str("DB_NAME", "postgres_db"),
    "sslmode": env_str("DB_SSLMODE", "require"),
}

# コネクションプールの下限・上限（core/db_base.py が使う）。
# 上限は「同時に DB を触りうるスレッド数」の目安で、増やしすぎると Postgres 側の
# max_connections を圧迫する
DB_POOL_MIN = env_int("DB_POOL_MIN", 1)
DB_POOL_MAX = env_int("DB_POOL_MAX", 10)
