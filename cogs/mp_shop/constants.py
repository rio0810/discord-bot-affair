import os

# 交換に必要なチケット枚数
TRIAL_RESET_COST = 20
TEXT_CHANNEL_COST = 10
COLOR_ROLE_COST = 15
EMOJI_COST = 5

# Discord のホログラフィック配色（この3色の組み合わせでのみホログラフィック表示になる固定値）
HOLOGRAPHIC_COLORS = (11127295, 16759788, 16761760)
# グラデーションのベース配色（性別ごと）: (primary, secondary)
GRADIENT_COLORS: dict[str, tuple[int, int]] = {
    "m": (0x3B82F6, 0x22D3EE),  # 青 → 水色
    "f": (0xE74C3C, 0xF368E0),  # 赤 → ピンク
}
# 絵文字画像の上限（Discord仕様：256KB）
EMOJI_MAX_BYTES = 256 * 1024
MOOD_PHOTO_COST = int(os.getenv("MOOD_PHOTO_COST") or "3")
# 雰囲気写真の閲覧権を得てから画像投稿までの猶予（時間）
MOOD_PHOTO_HOURS = 24
# 画像とみなす拡張子
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic")
