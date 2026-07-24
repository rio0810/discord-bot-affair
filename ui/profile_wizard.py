import math
import os

import discord

# 男性の面接チャンネルの topic プレフィックス（interview_room.py と揃える）
INTERVIEW_TOPIC_PREFIX = "interview_room:"
# 運営共有（障害申告）の送信先
RECORDING_FORWARD_CHANNEL_ID = int(os.getenv("RECORDING_FORWARD_CHANNEL_ID") or "0")

# 恋愛の割合で「0割」を選んだ人に付与する雑談ロール（未設定なら付与しない）
ZERO_ROMANCE_ROLE_ID = int(os.getenv("ZERO_ROMANCE_ROLE_ID", "0"))
# 恋愛の割合で「1割以上」を選んだ人に付与する恋愛ロール（未設定なら付与しない）
ROMANCE_ROLE_ID = int(os.getenv("ROMANCE_ROLE_ID", "0"))
# 雑談ロール保持者から隠すカテゴリ（未設定なら非表示処理をしない）
ZERO_ROMANCE_HIDDEN_CATEGORY_ID = int(os.getenv("ZERO_ROMANCE_HIDDEN_CATEGORY_ID", "0"))

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
PREFECTURES = PREFECTURES_EAST + PREFECTURES_WEST

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
        "建築関係", "製造業", "自営業", "休職中", "その他",
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
    ("寝落ちの可否", ["可", "仲良くなってから", "恋人になってから", "否"]),
    ("恋愛の割合", [f"{i}割" for i in range(0, 11)]),
    ("遠距離恋愛出来る範囲", ["遠距離", "中距離", "近距離"]),
    ("MBTI", MBTI_TYPES),
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

# 名前付きの分割（機械的な均等分割ではなく、意味のある区分で Select を分けたい項目）
NAMED_CHUNKS: dict[str, list[tuple[str, list[str]]]] = {
    "居住地": [
        ("東日本", PREFECTURES_EAST),
        ("西日本", PREFECTURES_WEST),
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


def _chunk_options(options: list[str]) -> list[list[str]]:
    """25個を超える選択肢を、できるだけ均等な複数の Select に分割する。"""
    n = math.ceil(len(options) / 25)
    per = math.ceil(len(options) / n)
    return [options[i : i + per] for i in range(0, len(options), per)]


async def _hide_category_from_role(guild: discord.Guild, role: discord.Role):
    """指定カテゴリに『このロールは閲覧不可』の上書きを設定する（未設定時のみ）。
    ロール単位なので、以後この上書きが保持者全員に自動適用される。"""
    if not ZERO_ROMANCE_HIDDEN_CATEGORY_ID:
        return
    category = guild.get_channel(ZERO_ROMANCE_HIDDEN_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        return
    if category.overwrites_for(role).view_channel is False:
        return  # 既に設定済み
    try:
        await category.set_permissions(
            role, view_channel=False, reason="恋愛の割合0割ロールからカテゴリを非表示"
        )
    except (discord.Forbidden, discord.HTTPException):
        print(f"[ERROR] カテゴリ非表示の設定に失敗しました: category={ZERO_ROMANCE_HIDDEN_CATEGORY_ID}")


def build_profile_embed(
    user: discord.abc.User, name: str, hobby: str, fav_type: str, answers: dict[str, str]
) -> discord.Embed:
    lines = [f"名前：{name}"]
    for label, _ in FIELDS:
        lines.append(f"{label}：{answers.get(label, '未回答')}")
        # 身長の直後に「好きなタイプ」「趣味」を差し込む（1行表記）
        if label == "身長":
            lines.append(f"好きなタイプ：{fav_type}")
            lines.append(f"趣味：{hobby}")
    description = "\n".join(lines)

    embed = discord.Embed(
        title=f"📋 {name} さんのプロフィール",
        description=description,
        color=discord.Color.pink(),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"作成者: {user}", icon_url=user.display_avatar.url)
    return embed


# ---------------------------------------------------------------------- #
# RadioGroup をまとめた Modal（ウィザードの Modal ステップから開く）
# ---------------------------------------------------------------------- #
class RadioStepModal(discord.ui.Modal):
    def __init__(self, wizard: "ProfileWizardView", group_title: str, labels: list[str]):
        super().__init__(title=f"プロフィール作成：{group_title}", timeout=900)
        self.wizard = wizard
        self.radios: dict[str, discord.ui.RadioGroup] = {}
        for label in labels:
            radio = discord.ui.RadioGroup(
                options=[
                    # 「戻る」で再入力するときは前回の選択をデフォルト表示
                    discord.RadioGroupOption(label=o, default=(wizard.answers.get(label) == o))
                    for o in FIELD_OPTIONS[label]
                ]
            )
            self.radios[label] = radio
            self.add_item(discord.ui.Label(text=label, component=radio))

    async def on_submit(self, interaction: discord.Interaction):
        for label, radio in self.radios.items():
            if radio.value is not None:
                self.wizard.answers[label] = radio.value
        self.wizard.index += 1
        self.wizard._rebuild()
        await self.wizard._refresh(interaction)


# ---------------------------------------------------------------------- #
# 選択ウィザード（ephemeral・1ステップずつ進む）
# ---------------------------------------------------------------------- #
class ProfileWizardView(discord.ui.View):
    def __init__(self, name: str, hobby: str, fav_type: str, disability="", is_male=False):
        super().__init__(timeout=900)
        self.name = name
        self.hobby = hobby
        self.fav_type = fav_type
        # 障害・ハンデの申告（公開せず運営のみで共有）
        self.disability = disability
        # 男性なら録音との待ち合わせ（録音投稿＋プロフ作成で採点パネルを出す）
        self.is_male = is_male
        self.answers: dict[str, str] = {}
        self.index = 0
        # MBTI の2段階選択で、大項目を選んだ後に保持する（None なら大項目の選択中）
        self.mbti_group: str | None = None
        self._rebuild()

    @property
    def content(self) -> str:
        if self.index >= len(STEPS):
            return "内容を確認して、よければ **投稿する** を押してください。"
        step = STEPS[self.index]
        header = f"📋 プロフィール作成（{self.index + 1}/{len(STEPS)}）"
        if step[0] == "modal":
            _, title, labels = step
            return f"{header}\nボタンを押して **{title}**（{'・'.join(labels)}）を入力してください："
        _, label, _ = step
        if label == "MBTI":
            if self.mbti_group is None:
                return f"{header}\n**MBTI** の大項目を選んでください（未診断の方は「やっていない」）："
            return f"{header}\n**MBTI（{self.mbti_group}）** のタイプを選んでください："
        prompt = f"{header}\n**{label}** を選んでください："
        if label == "恋愛の割合" and ZERO_ROMANCE_ROLE_ID:
            prompt += (
                "\n⚠️ **0割** を選ぶと **雑談** ロールが付与され、"
                "**個通部屋が使えなくなります**のでご注意ください。"
            )
        return prompt

    def _rebuild(self):
        self.clear_items()

        if self.index >= len(STEPS):
            # 確認ページ
            submit_btn = discord.ui.Button(label="投稿する", style=discord.ButtonStyle.green, emoji="✅")
            back_btn = discord.ui.Button(label="戻る", style=discord.ButtonStyle.gray, emoji="◀")
            submit_btn.callback = self._submit
            back_btn.callback = self._go_back
            self.add_item(submit_btn)
            self.add_item(back_btn)
            return

        step = STEPS[self.index]

        if step[0] == "modal":
            # RadioGroup をまとめた Modal を開くボタン
            _, title, labels = step
            open_btn = discord.ui.Button(
                label=f"{title}を入力する", style=discord.ButtonStyle.blurple, emoji="📝"
            )

            async def open_modal(interaction: discord.Interaction):
                await interaction.response.send_modal(RadioStepModal(self, title, labels))

            open_btn.callback = open_modal
            self.add_item(open_btn)

            if self.index > 0:
                back_btn = discord.ui.Button(label="戻る", style=discord.ButtonStyle.gray, emoji="◀")
                back_btn.callback = self._go_back
                self.add_item(back_btn)
            return

        _, label, options = step

        if label == "MBTI":
            self._build_mbti_step()
            return

        if label in NAMED_CHUNKS:
            # 名前付き区分（例：居住地 → 東日本 / 西日本）
            named = NAMED_CHUNKS[label]
            chunks = [(f"{label}（{name}）", chunk) for name, chunk in named]
        else:
            plain = _chunk_options(options)
            if len(plain) > 1:
                chunks = [(f"{label}（{c[0]}〜{c[-1]}）", c) for c in plain]
            else:
                chunks = [(f"{label}を選択してください", c) for c in plain]

        for placeholder, chunk in chunks:
            select = discord.ui.Select(
                placeholder=placeholder,
                options=[discord.SelectOption(label=o) for o in chunk],
            )

            async def on_select(interaction: discord.Interaction, s=select):
                self.answers[label] = s.values[0]
                self.index += 1
                self._rebuild()
                await self._refresh(interaction)

            select.callback = on_select
            self.add_item(select)

        if self.index > 0:
            back_btn = discord.ui.Button(label="戻る", style=discord.ButtonStyle.gray, emoji="◀")
            back_btn.callback = self._go_back
            self.add_item(back_btn)

    def _build_mbti_step(self):
        if self.mbti_group is None:
            # 1段階目：大項目の選択（5つめが「やっていない」）
            select = discord.ui.Select(
                placeholder="MBTIの大項目を選んでください...",
                options=[discord.SelectOption(label=o) for o in MBTI_MAJOR_OPTIONS],
            )

            async def on_major(interaction: discord.Interaction, s=select):
                choice = s.values[0]
                if choice == _MBTI_NONE:
                    # 「やっていない」は大項目の選択で確定して次へ
                    self.answers["MBTI"] = _MBTI_NONE
                    self.index += 1
                else:
                    self.mbti_group = choice
                self._rebuild()
                await self._refresh(interaction)

            select.callback = on_major
            self.add_item(select)

            if self.index > 0:
                back_btn = discord.ui.Button(label="戻る", style=discord.ButtonStyle.gray, emoji="◀")
                back_btn.callback = self._go_back
                self.add_item(back_btn)
            return

        # 2段階目：選んだ大項目の中のタイプ
        select = discord.ui.Select(
            placeholder=f"MBTI（{self.mbti_group}）のタイプを選んでください...",
            options=[discord.SelectOption(label=o) for o in MBTI_GROUPS[self.mbti_group]],
        )

        async def on_type(interaction: discord.Interaction, s=select):
            self.answers["MBTI"] = s.values[0]
            self.mbti_group = None
            self.index += 1
            self._rebuild()
            await self._refresh(interaction)

        select.callback = on_type
        self.add_item(select)

        # 「戻る」は大項目の選択へ戻す
        back_btn = discord.ui.Button(label="大項目に戻る", style=discord.ButtonStyle.gray, emoji="◀")

        async def back_to_major(interaction: discord.Interaction):
            self.mbti_group = None
            self._rebuild()
            await self._refresh(interaction)

        back_btn.callback = back_to_major
        self.add_item(back_btn)

    async def _refresh(self, interaction: discord.Interaction):
        embed = None
        if self.index >= len(STEPS):
            embed = build_profile_embed(
                interaction.user, self.name, self.hobby, self.fav_type, self.answers
            )
        await interaction.response.edit_message(content=self.content, embed=embed, view=self)

    async def _go_back(self, interaction: discord.Interaction):
        self.index = max(0, self.index - 1)
        self._rebuild()
        await self._refresh(interaction)

    async def _submit(self, interaction: discord.Interaction):
        embed = build_profile_embed(
            interaction.user, self.name, self.hobby, self.fav_type, self.answers
        )

        # まず応答（3秒以内）。以降の送信・転送は時間がかかっても良い
        self.stop()
        await interaction.response.edit_message(
            content="✅ プロフィールを投稿しました！", embed=None, view=None
        )

        # プロフィールをチャンネルへ投稿
        try:
            await interaction.channel.send(content=interaction.user.mention, embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] プロフィールの投稿に失敗しました: {e}")

        # 審査への送信
        cog = interaction.client.get_cog("RecordingScore")
        if cog is not None:
            # 作成済みとして記録（2回目の作成をブロック）
            cog.mark_profile_created(interaction.user.id)
            if self.is_male:
                # 男性は録音との待ち合わせ（録音が既にあれば審査へ、無ければ待機）
                await cog.on_profile_created(interaction, embed)
            else:
                # 女性は音声不要。プロフィールのみで即審査へ
                await cog.on_profile_only(interaction, embed)

        # 障害・ハンデの申告があれば運営チャンネルにのみ共有（公開しない）
        if self.disability:
            await _send_staff_private_note(interaction, self.disability)

        # 恋愛の割合の選択に応じて 雑談 / 恋愛 ロールを付与
        await self._apply_romance_roles(interaction)

    async def _apply_romance_roles(self, interaction: discord.Interaction):
        """恋愛の割合が「0割」→雑談ロール、「1割以上」→恋愛ロールを付与（両者は排他）。"""
        answer = self.answers.get("恋愛の割合")
        if answer is None:
            return
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        zero_role = guild.get_role(ZERO_ROMANCE_ROLE_ID) if ZERO_ROMANCE_ROLE_ID else None
        romance_role = guild.get_role(ROMANCE_ROLE_ID) if ROMANCE_ROLE_ID else None

        if answer == "0割":
            grant, remove = zero_role, romance_role
        else:
            grant, remove = romance_role, zero_role

        # 反対のロールを外す
        if remove is not None and remove in member.roles:
            try:
                await member.remove_roles(remove, reason="恋愛の割合の選択に伴うロール整理")
            except (discord.Forbidden, discord.HTTPException):
                print(f"[ERROR] ロールの除去に失敗しました: {remove.id} -> {member.id}")

        # 対応するロールを付与
        if grant is not None and grant not in member.roles:
            try:
                await member.add_roles(grant, reason="プロフィールの恋愛の割合の選択によるロール付与")
            except (discord.Forbidden, discord.HTTPException):
                print(f"[ERROR] ロールの付与に失敗しました: {grant.id} -> {member.id}")
                return

        # 雑談ロールのときは指定カテゴリを非表示に
        if answer == "0割" and zero_role is not None:
            await _hide_category_from_role(guild, zero_role)


# ---------------------------------------------------------------------- #
# 名前・趣味・好きなタイプの入力 Modal（ウィザードの入口）
# ---------------------------------------------------------------------- #
class ProfileModal(discord.ui.Modal, title="プロフィール作成"):
    name = discord.ui.TextInput(label="名前", max_length=50, placeholder="サーバーで呼ばれたい名前を入力して下さい")
    hobby = discord.ui.TextInput(
        label="【趣味】", style=discord.TextStyle.paragraph, max_length=300,
        placeholder="好きな趣味を3つ入力して下さい",
    )
    fav_type = discord.ui.TextInput(
        label="【好きなタイプ】", style=discord.TextStyle.paragraph, max_length=300,
        placeholder="例：よく笑う人、価値観の合う人",
    )
    # 公開せず運営のみで共有する任意項目
    disability = discord.ui.TextInput(
        label="【質問】現在抱えている障害やハンデはございますか？",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder=(
            "※この情報は公開しません。運営陣のみで共有し、何かあったときに運営陣がサポートに入れるように"
            "するためにお聞きしています。運営陣以外に他言することは一切ありませんので、ご安心ください。"
        ),
    )

    def __init__(self, is_male: bool = False):
        super().__init__()
        self.is_male = is_male

    async def on_submit(self, interaction: discord.Interaction):
        # 録音はモーダルでは受け取らず、面接チャンネルへの投稿で提出する
        view = ProfileWizardView(
            str(self.name), str(self.hobby), str(self.fav_type),
            disability=str(self.disability).strip(), is_male=self.is_male,
        )
        await interaction.response.send_message(content=view.content, view=view, ephemeral=True)


async def _send_staff_private_note(interaction: discord.Interaction, disability: str):
    """障害・ハンデの申告を運営チャンネルにのみ共有する（公開しない）。"""
    if not RECORDING_FORWARD_CHANNEL_ID:
        return
    channel = interaction.client.get_channel(RECORDING_FORWARD_CHANNEL_ID)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        print(f"⚠️ [ProfileWizard] 運営共有先 {RECORDING_FORWARD_CHANNEL_ID} が見つかりません。")
        return
    embed = discord.Embed(
        title="🔒 【運営共有】障害・ハンデの申告",
        description=disability[:4000],
        color=discord.Color.dark_red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="対象者", value=interaction.user.mention, inline=False)
    embed.set_footer(text="※本人のプロフィールには公開されていません")
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"[ERROR] 障害申告の運営共有に失敗しました: {e}")


# ---------------------------------------------------------------------- #
# 「プロフィールを作成する」ボタン（専用チャンネルに設置される永続ビュー）
# ---------------------------------------------------------------------- #
async def _start_profile_wizard(interaction: discord.Interaction):
    """所有者チェックをして Modal を開く（View / ActionRow 両方のボタンから共用）。"""
    # チャンネルの topic 末尾（<prefix>:<owner_id>）から所有者を判定
    topic = getattr(interaction.channel, "topic", None) or ""
    owner_id = topic.rsplit(":", 1)[-1] if ":" in topic else ""
    if owner_id != str(interaction.user.id):
        await interaction.response.send_message(
            "❌ このボタンはチャンネルの本人のみ使用できます。", ephemeral=True
        )
        return
    # プロフィールは1人1回のみ（作成済みなら2回目を拒否）
    cog = interaction.client.get_cog("RecordingScore")
    if cog is not None and cog.has_profile(interaction.user.id):
        await interaction.response.send_message(
            "❌ プロフィールは既に作成済みです。作り直したい場合は運営にご連絡ください。",
            ephemeral=True,
        )
        return
    # 男性（面接）チャンネルなら録音ファイルの提出欄を出す
    is_male = topic.startswith(INTERVIEW_TOPIC_PREFIX)
    await interaction.response.send_modal(ProfileModal(is_male=is_male))


class ProfileStartView(discord.ui.View):
    """旧メッセージ用の永続ビュー（再起動後のボタン反応もこの登録で捌く）。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="プロフィールを作成する",
        style=discord.ButtonStyle.blurple,
        emoji="📋",
        custom_id="persistent:create_profile",
    )
    async def create_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_profile_wizard(interaction)


class ProfileStartActions(discord.ui.ActionRow):
    """Components V2 レイアウト内に置くボタン行（custom_id は ProfileStartView と共通）。"""

    @discord.ui.button(
        label="プロフィールを作成する",
        style=discord.ButtonStyle.blurple,
        emoji="📋",
        custom_id="persistent:create_profile",
    )
    async def create_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_profile_wizard(interaction)


class RoomPanelView(discord.ui.LayoutView):
    """専用チャンネルに送る案内パネル（タイトル下に Separator の区切り線）。"""

    def __init__(self, mention: str, title: str, description: str, colour: discord.Colour):
        super().__init__(timeout=None)
        self.add_item(discord.ui.TextDisplay(mention))
        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay(f"## {title}"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(discord.ui.TextDisplay(description))
        # 文章とボタンの間の余白（線は表示しない）
        container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large))
        container.add_item(ProfileStartActions())
        self.add_item(container)
