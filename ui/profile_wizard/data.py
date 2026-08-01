import math
import os

import discord

# 男性の面接チャンネルの topic プレフィックス（interview_room.py と揃える）
INTERVIEW_TOPIC_PREFIX = "interview_room:"
# 運営共有（障害申告）の送信先
RECORDING_FORWARD_CHANNEL_ID = int(os.getenv("RECORDING_FORWARD_CHANNEL_ID") or "0")

# DM・フレンド申請の可否（雑談・恋愛共通の質問）。選択に応じてロールを付与する
DM_CRITERIA_FIELD = "DM・フレンド申請の可否"
DM_CRITERIA_OPTIONS = ["🙆 誰でもOK", "🙅 DM・フレンド申請☓", "🙋 話したことあるなら", "🗣️ 直接聞いてもらってから"]
# 各選択肢に対応するロールID（未設定＝付与しない）。相互排他で付け替える
DM_CRITERIA_ROLE_IDS: dict[str, int] = {
    "🙆 誰でもOK": int(os.getenv("DM_OPEN_ROLE_ID", "0")),
    "🙅 DM・フレンド申請☓": int(os.getenv("DM_CLOSED_ROLE_ID", "0")),
    "🙋 話したことあるなら": int(os.getenv("DM_ACQUAINTED_ROLE_ID", "0")),
    "🗣️ 直接聞いてもらってから": int(os.getenv("DM_ASK_ROLE_ID", "0")),
}

# ---------------------------------------------------------------------- #
# 選択肢の定義
# ---------------------------------------------------------------------- #
PREFECTURES_EAST = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県",
]
PREFECTURES_WEST = [
    "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
PREFECTURES = PREFECTURES_EAST + PREFECTURES_WEST + ["海外"]

# 職種はジャンル別に Select を分ける（下の OCCUPATION_GENRES が実体）
OCCUPATION_GENRES: list[tuple[str, list[str]]] = [
    ("オフィス・ビジネス系", [
        "金融", "コンサル", "外資", "商社", "広告", "マスコミ",
        "営業・販売", "保険", "不動産", "IT関係", "通信",
    ]),
    ("公務・専門職系", [
        "公務員", "自衛隊", "消防署", "弁護士", "税理士",
        "医療関係", "製薬", "教育関係", "保育士",
    ]),
    ("サービス・クリエイティブ系", [
        "飲食業", "食品関係", "旅行関係", "航空関係", "流通",
        "サービス業", "美容関係", "アパレル", "クリエイター",
    ]),
    ("技術・その他", [
        "建築関係", "製造業", "自営業", "学生", "休職中", "求職中", "その他",
    ]),
]
OCCUPATIONS = [o for _, opts in OCCUPATION_GENRES for o in opts]

MBTI_TYPES = [
    "やっていない", "INTJ（建築家）", "INTP（論理学者）", "ENTJ（指揮官）", "ENTP（討論者）",
    "INFJ（提唱者）", "INFP（仲介者）", "ENFJ（主人公）", "ENFP（運動家）",
    "ISTJ（管理者）", "ISFJ（擁護者）", "ESTJ（幹部）", "ESFJ（領事）",
    "ISTP（巨匠）", "ISFP（冒険家）", "ESTP（起業家）", "ESFP（エンターテイナー）",
]

# ウィザードで順番に選択させる項目: (項目名, 選択肢リスト)
FIELDS: list[tuple[str, list[str]]] = [
    ("年齢", [f"{i}歳" for i in range(20, 36)]),
    ("血液型", ["A型", "B型", "O型", "AB型"]),
    ("居住地", PREFECTURES),
    ("職種", OCCUPATIONS),
    ("身長", ["157cm以下"] + [f"{i}cm" for i in range(158, 173)] + ["173cm以上"]),
    ("結婚に対する意思", ["すぐにでもしたい", "2〜3年のうちに", "いい人がいれば", "したくない"]),
    ("出会うまでの希望", ["できればすぐに会いたい", "気が合えば会いたい", "個通で交流を深めてから会いたい"]),
    ("同居人", ["1人暮らし", "ルームシェア", "ペットがいます", "実家暮らし"]),
    ("休日", ["土日", "平日", "不定期", "休職中"]),
    ("お酒", ["飲まない", "飲む", "時々飲む"]),
    ("タバコ", ["吸わない", "吸う", "相手が嫌ならやめる"]),
    ("寝落ちの可否", ["可", "否", "仲良くなってから", "恋人になってから"]),
    ("恋愛の割合", [f"{i}割" for i in range(1, 11)]),
    ("遠距離恋愛出来る範囲", ["遠距離", "中距離", "近距離"]),
    ("MBTI", MBTI_TYPES),
    (DM_CRITERIA_FIELD, DM_CRITERIA_OPTIONS),
]


# MBTI は2段階選択：まず大項目（下記4つ＋「やっていない」）→ 選んだ大項目の中のタイプ
_MBTI_NONE = "やっていない"
MBTI_GROUPS: dict[str, list[str]] = {
    "分析家": ["ENTJ（指揮官）", "ENTP（討論者）", "INTJ（建築家）", "INTP（論理学者）"],
    "外交官": ["ENFJ（主人公）", "ENFP（運動家）", "INFJ（提唱者）", "INFP（仲介者）"],
    "番人": ["ESFJ（領事）", "ESTJ（幹部）", "ISFJ（擁護者）", "ISTJ（管理者）"],
    "探検家": ["ESFP（エンターテイナー）", "ESTP（起業家）", "ISFP（冒険家）", "ISTP（巨匠）"],
}
# 大項目の選択肢（5つめが「やっていない」）
MBTI_MAJOR_OPTIONS: list[str] = list(MBTI_GROUPS.keys()) + [_MBTI_NONE]

# 任意項目（選択せずスキップ可能）。未選択なら「未回答」表示になる
OPTIONAL_FIELDS: set[str] = {"職種", "同居人"}

# 名前付きの分割（機械的な均等分割ではなく、意味のある区分で Select を分けたい項目）
NAMED_CHUNKS: dict[str, list[tuple[str, list[str]]]] = {
    "居住地": [
        ("東日本", PREFECTURES_EAST),
        ("西日本", PREFECTURES_WEST),
        ("海外", ["海外"]),
    ],
    "職種": OCCUPATION_GENRES,
}

# Modal 内の RadioGroup でまとめて選ばせる項目。
# Modal は最大5コンポーネントなので1グループ5項目まで
RADIO_MODAL_GROUPS: list[tuple[str, list[str]]] = [
    ("基本情報", ["血液型", "結婚に対する意思", "出会うまでの希望", "同居人"]),
    ("ライフスタイル", ["休日", "お酒", "タバコ", "遠距離恋愛出来る範囲", "寝落ちの可否"]),
]

FIELD_OPTIONS: dict[str, list[str]] = dict(FIELDS)


def _build_steps() -> list[tuple]:
    """FIELDS の並び順を保ちつつ、RadioGroup 対象の項目をグループ単位の
    Modal ステップ（グループ先頭の項目の位置）に置き換えたステップ列を作る。"""
    group_of = {label: (title, labels) for title, labels in RADIO_MODAL_GROUPS for label in labels}
    steps: list[tuple] = []
    seen: set[str] = set()
    for label, options in FIELDS:
        if label in group_of:
            title, labels = group_of[label]
            if title not in seen:
                seen.add(title)
                steps.append(("modal", title, labels))
        else:
            steps.append(("select", label, options))
    return steps


STEPS = _build_steps()

# 雑談ロール向けの短縮ステップ（名前・趣味は入口Modal、残りをここで選択）
CASUAL_STEP_LABELS = ["年齢", "血液型", "居住地", "MBTI", DM_CRITERIA_FIELD]
CASUAL_STEPS = [("select", label, FIELD_OPTIONS[label]) for label in CASUAL_STEP_LABELS]


def _chunk_options(options: list[str]) -> list[list[str]]:
    """25個を超える選択肢を、できるだけ均等な複数の Select に分割する。"""
    n = math.ceil(len(options) / 25)
    per = math.ceil(len(options) / n)
    return [options[i : i + per] for i in range(0, len(options), per)]


def build_profile_text(
    name: str, hobby: str, fav_type: str, answers: dict[str, str], casual: bool = False
) -> str:
    """プロフィールの本文テキスト（コピペ用・embed の description と共通）。
    casual（雑談ロール）は 名前/年齢/血液型/居住地/趣味/MBTI の短縮版。"""
    if casual:
        return "\n".join([
            f"【名前】{name}",
            f"【年齢】{answers.get('年齢', '未回答')}",
            f"【血液型】{answers.get('血液型', '未回答')}",
            f"【居住地】{answers.get('居住地', '未回答')}",
            f"【趣味】{hobby}",
            f"【MBTI】{answers.get('MBTI', '未回答')}",
            f"【{DM_CRITERIA_FIELD}】{answers.get(DM_CRITERIA_FIELD, '未回答')}",
        ])

    lines = [f"【名前】{name}"]
    for label, _ in FIELDS:
        lines.append(f"【{label}】{answers.get(label, '未回答')}")
        # 身長の直後に「好きなタイプ」「趣味」を差し込む（1行表記）
        if label == "身長":
            lines.append(f"【好きなタイプ】{fav_type}")
            lines.append(f"【趣味】{hobby}")
    return "\n".join(lines)


def build_profile_embed(
    user: discord.abc.User, name: str, hobby: str, fav_type: str,
    answers: dict[str, str], casual: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {name} さんのプロフィール",
        description=build_profile_text(name, hobby, fav_type, answers, casual),
        color=discord.Color.pink(),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"作成者: {user}", icon_url=user.display_avatar.url)
    return embed
