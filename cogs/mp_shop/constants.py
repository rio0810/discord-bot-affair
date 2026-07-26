import os

# 交換に必要なチケット枚数
TRIAL_RESET_COST = 20
TEXT_CHANNEL_COST = 10
ROLE_CREATE_COST = 5
EMOJI_COST = 5
# 絵文字画像の上限（Discord仕様：256KB）
EMOJI_MAX_BYTES = 256 * 1024
MOOD_PHOTO_COST = int(os.getenv("MOOD_PHOTO_COST") or "3")
# 雰囲気写真の閲覧権を得てから画像投稿までの猶予（時間）
MOOD_PHOTO_HOURS = 24
# 画像とみなす拡張子
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic")
