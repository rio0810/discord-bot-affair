# 通話部屋テキストchの topic に埋め込む識別子:
#   通常     call_room:<recruiter_id>:<target_id>:<vc_id>
#   お試し   call_room:<recruiter_id>:<target_id>:<vc_id>:trial
#            （VCに最初の人が入ると :start=<unix秒> が付き、警告送信後は :warned が付く）
ROOM_TOPIC_PREFIX = "call_room:"
# 1ページあたりの Select 表示人数（Discord の上限は25）
PAGE_SIZE = 25
# お試し個通の制限時間（分）と、終了前の警告タイミング（残り分数）
TRIAL_DURATION_MINUTES = 30
TRIAL_WARNING_REMAINING = 5
# CheckboxGroup は1グループ最大10個・Modal は最大5コンポーネント → 一度に扱えるのは50人まで
BLOCK_GROUP_SIZE = 10
BLOCK_MAX_CANDIDATES = 50
