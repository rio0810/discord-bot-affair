import asyncio
import logging

import discord

from .data import (
    CASUAL_STEPS,
    DM_CRITERIA_FIELD,
    FIELD_OPTIONS,
    INTERVIEW_TOPIC_PREFIX,
    MALE_ROLE_ID,
    MBTI_GROUPS,
    MBTI_MAJOR_OPTIONS,
    NAMED_CHUNKS,
    OPTIONAL_FIELDS,
    RECORDING_FORWARD_CHANNEL_ID,
    STEPS,
    _MBTI_NONE,
    _chunk_options,
    build_profile_embed,
    build_profile_text,
)
from .roles import _apply_choice_role, _apply_dm_criteria_role

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# RadioGroup をまとめた Modal（ウィザードの Modal ステップから開く）
# ---------------------------------------------------------------------- #
class RadioStepModal(discord.ui.Modal):
    def __init__(self, wizard: "ProfileWizardView", group_title: str, labels: list[str]):
        super().__init__(title=f"プロフィール作成：{group_title}", timeout=900)
        self.wizard = wizard
        self.radios: dict[str, discord.ui.RadioGroup] = {}
        for label in labels:
            optional = label in OPTIONAL_FIELDS
            radio = discord.ui.RadioGroup(
                options=[
                    # 「戻る」で再入力するときは前回の選択をデフォルト表示
                    discord.RadioGroupOption(label=o, default=(wizard.answers.get(label) == o))
                    for o in FIELD_OPTIONS[label]
                ],
                required=not optional,
            )
            self.radios[label] = radio
            text = f"{label}（任意）" if optional else label
            self.add_item(discord.ui.Label(text=text, component=radio))

    async def on_submit(self, interaction: discord.Interaction):
        def mutate():
            for label, radio in self.radios.items():
                if radio.value is not None:
                    self.wizard.answers[label] = radio.value
            self.wizard.index += 1

        await self.wizard._apply(interaction, mutate)


# ---------------------------------------------------------------------- #
# 選択ウィザード（ephemeral・1ステップずつ進む）
# ---------------------------------------------------------------------- #
class ProfileWizardView(discord.ui.View):
    def __init__(self, name: str, hobby: str, fav_type: str, disability="",
                 is_male=False, casual=False, wait_audio=False):
        super().__init__(timeout=900)
        self.name = name
        self.hobby = hobby
        self.fav_type = fav_type
        # 障害・ハンデの申告（公開せず運営のみで共有）
        self.disability = disability
        # 男性かどうか（審査の投稿先フォーラムの振り分けに使う）
        self.is_male = is_male
        # 録音との待ち合わせをするか（面接チャンネル経由のときだけ True）
        self.wait_audio = wait_audio
        # 雑談ロールなら短縮プロフ（名前/年齢/血液型/居住地/趣味/MBTI）
        self.casual = casual
        self.steps = CASUAL_STEPS if casual else STEPS
        self.answers: dict[str, str] = {}
        self.index = 0
        # MBTI の2段階選択で、大項目を選んだ後に保持する（None なら大項目の選択中）
        self.mbti_group: str | None = None
        self._rebuild()

    @property
    def content(self) -> str:
        if self.index >= len(self.steps):
            return "内容を確認して、よければ **投稿する** を押してください。"
        step = self.steps[self.index]
        header = f"📋 プロフィール作成（{self.index + 1}/{len(self.steps)}）"
        if step[0] == "modal":
            _, title, labels = step
            return f"{header}\nボタンを押して **{title}**（{'・'.join(labels)}）を入力してください："
        _, label, _ = step
        if label == "MBTI":
            if self.mbti_group is None:
                return f"{header}\n**MBTI** の大項目を選んでください（未診断の方は「やっていない」）："
            return f"{header}\n**MBTI（{self.mbti_group}）** のタイプを選んでください："
        optional_note = "**（任意・スキップ可）**" if label in OPTIONAL_FIELDS else ""
        return f"{header}\n**{label}** を選んでください{optional_note}："

    def _rebuild(self):
        self.clear_items()

        if self.index >= len(self.steps):
            # 確認ページ
            submit_btn = discord.ui.Button(label="投稿する", style=discord.ButtonStyle.green, emoji="✅")
            back_btn = discord.ui.Button(label="戻る", style=discord.ButtonStyle.gray, emoji="◀")
            submit_btn.callback = self._submit
            back_btn.callback = self._go_back
            self.add_item(submit_btn)
            self.add_item(back_btn)
            return

        step = self.steps[self.index]

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
                def mutate():
                    self.answers[label] = s.values[0]
                    self.index += 1

                await self._apply(interaction, mutate)

            select.callback = on_select
            self.add_item(select)

        # 任意項目は「スキップ」ボタンで未選択のまま次へ
        if label in OPTIONAL_FIELDS:
            skip_btn = discord.ui.Button(label="スキップ", style=discord.ButtonStyle.gray, emoji="⏭️")

            async def on_skip(interaction: discord.Interaction):
                def mutate():
                    self.answers.pop(label, None)
                    self.index += 1

                await self._apply(interaction, mutate)

            skip_btn.callback = on_skip
            self.add_item(skip_btn)

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
                def mutate():
                    choice = s.values[0]
                    if choice == _MBTI_NONE:
                        # 「やっていない」は大項目の選択で確定して次へ
                        self.answers["MBTI"] = _MBTI_NONE
                        self.index += 1
                    else:
                        self.mbti_group = choice

                await self._apply(interaction, mutate)

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
            def mutate():
                self.answers["MBTI"] = s.values[0]
                self.mbti_group = None
                self.index += 1

            await self._apply(interaction, mutate)

        select.callback = on_type
        self.add_item(select)

        # 「戻る」は大項目の選択へ戻す
        back_btn = discord.ui.Button(label="大項目に戻る", style=discord.ButtonStyle.gray, emoji="◀")

        async def back_to_major(interaction: discord.Interaction):
            def mutate():
                self.mbti_group = None

            await self._apply(interaction, mutate)

        back_btn.callback = back_to_major
        self.add_item(back_btn)

    async def _refresh(self, interaction: discord.Interaction) -> bool:
        """ウィザードの表示を今の状態に更新する。更新できたら True。

        Discord の応答期限（3秒）を過ぎるとトークンが無効になり 404(10062) が返る。
        その場合は画面が前のステップのままなので、呼び出し側で状態を巻き戻す。"""
        embed = None
        if self.index >= len(self.steps):
            embed = build_profile_embed(
                interaction.user, self.name, self.hobby, self.fav_type, self.answers, self.casual
            )
        try:
            await interaction.response.edit_message(content=self.content, embed=embed, view=self)
        except discord.NotFound:
            # 応答期限切れ。ユーザーがもう一度選び直せば進めるので警告のみ
            logger.warning("プロフィールウィザードの更新に失敗しました（応答期限切れ）")
            return False
        except discord.HTTPException as e:
            logger.warning(f"プロフィールウィザードの更新に失敗しました: {e}")
            return False
        return True

    async def _apply(self, interaction: discord.Interaction, mutate) -> None:
        """状態を変更して画面を更新する。更新できなければ変更を巻き戻す。

        巻き戻さないと、表示は前のステップのまま内部だけ進み、次の選択が
        別の項目の答えとして記録されてしまう。"""
        snapshot = (self.index, self.mbti_group, dict(self.answers))
        mutate()
        self._rebuild()
        if not await self._refresh(interaction):
            self.index, self.mbti_group, self.answers = snapshot[0], snapshot[1], snapshot[2]
            self._rebuild()

    async def _go_back(self, interaction: discord.Interaction):
        def mutate():
            self.index = max(0, self.index - 1)

        await self._apply(interaction, mutate)

    async def _submit(self, interaction: discord.Interaction):
        embed = build_profile_embed(
            interaction.user, self.name, self.hobby, self.fav_type, self.answers, self.casual
        )

        # まず応答（3秒以内）。以降の送信・転送は時間がかかっても良い
        self.stop()
        try:
            await interaction.response.edit_message(
                content="✅ プロフィールを投稿しました！", embed=None, view=None
            )
        except discord.HTTPException as e:
            # 応答期限切れなどで表示を更新できなくても、投稿処理自体は続行する
            logger.warning(f"プロフィール投稿の完了表示に失敗しました: {e}")

        # 本人のチャンネルへは、コピペしやすいよう素のテキストで投稿（embed は審査用に温存）
        profile_text = build_profile_text(self.name, self.hobby, self.fav_type, self.answers, self.casual)
        try:
            await interaction.channel.send(
                content=profile_text, allowed_mentions=discord.AllowedMentions.none()
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"プロフィールの投稿に失敗しました: {e}")

        # 審査への送信
        cog = interaction.client.get_cog("RecordingScore")
        if cog is not None:
            # 作成済みとして記録（2回目の作成をブロック）
            cog.mark_profile_created(interaction.user.id)
            if self.wait_audio:
                # 面接ルートは録音との待ち合わせ（録音が既にあれば審査へ、無ければ待機）
                await cog.on_profile_created(interaction, embed)
            else:
                # 音声不要のルート。プロフィールのみで即審査へ（男女で投稿先を分ける）
                await cog.on_profile_only(interaction, embed, kind="m" if self.is_male else "f")

        # 1週間レビュー用にプロフィール embed を保存（新人ロール昇格審査で再掲する）
        nrc = interaction.client.get_cog("NewcomerReview")
        if nrc is not None:
            nrc.store_profile(interaction.user.id, embed)

        # DM・フレンド申請の可否の選択に応じたロールを付与（雑談・恋愛共通）
        dm_choice = self.answers.get(DM_CRITERIA_FIELD)
        if dm_choice:
            await _apply_dm_criteria_role(interaction.guild, interaction.user, dm_choice)

        # 障害・ハンデの申告があれば運営チャンネルにのみ共有（公開しない）
        if self.disability:
            await _send_staff_private_note(interaction, self.disability)
        # 雑談/恋愛の種別ロールは入口の選択時に済ませているのでここでは行わない


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
        label="【任意】現在抱えている障害やハンデはございますか？",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder=(
            "※この情報は公開しません。運営陣のみで共有し、何かあったときに運営陣がサポートに入れるように"
            "するためにお聞きしています。運営陣以外に他言することは一切ありませんので、ご安心ください。"
        ),
    )

    def __init__(self, is_male: bool = False, casual: bool = False, wait_audio: bool = False):
        super().__init__()
        self.is_male = is_male
        self.casual = casual
        self.wait_audio = wait_audio
        # 雑談ロールは「好きなタイプ」を尋ねない
        if casual:
            self.remove_item(self.fav_type)

    async def on_submit(self, interaction: discord.Interaction):
        # 録音はモーダルでは受け取らず、面接チャンネルへの投稿で提出する
        fav = "" if self.casual else str(self.fav_type)
        view = ProfileWizardView(
            str(self.name), str(self.hobby), fav,
            disability=str(self.disability).strip(), is_male=self.is_male, casual=self.casual,
            wait_audio=self.wait_audio,
        )
        await interaction.response.send_message(content=view.content, view=view, ephemeral=True)


async def _send_staff_private_note(interaction: discord.Interaction, disability: str):
    """障害・ハンデの申告を運営チャンネルにのみ共有する（公開しない）。"""
    if not RECORDING_FORWARD_CHANNEL_ID:
        return
    channel = interaction.client.get_channel(RECORDING_FORWARD_CHANNEL_ID)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        logger.warning(f"⚠️ [ProfileWizard] 運営共有先 {RECORDING_FORWARD_CHANNEL_ID} が見つかりません。")
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
        logger.error(f"障害申告の運営共有に失敗しました: {e}")


# ---------------------------------------------------------------------- #
# 「プロフィールを作成する」ボタン（専用チャンネルに設置される永続ビュー）
# ---------------------------------------------------------------------- #
class ProfileTypeChoiceView(discord.ui.View):
    """作成ボタンの後に「雑談 / 恋愛」を選ばせる（ephemeral）。
    雑談 → 短縮プロフィール、恋愛 → 通常プロフィール。"""

    def __init__(self, is_male: bool, wait_audio: bool = False):
        super().__init__(timeout=300)
        self.is_male = is_male
        self.wait_audio = wait_audio

    @discord.ui.button(label="雑談", style=discord.ButtonStyle.gray, emoji="💬")
    async def casual_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ロール付与はここで実施（モーダル表示を遅らせないよう背景で処理）
        asyncio.create_task(_apply_choice_role(interaction.guild, interaction.user, casual=True))
        await interaction.response.send_modal(
            ProfileModal(is_male=self.is_male, casual=True, wait_audio=self.wait_audio)
        )

    @discord.ui.button(label="恋愛", style=discord.ButtonStyle.red, emoji="❤️")
    async def romance_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        asyncio.create_task(_apply_choice_role(interaction.guild, interaction.user, casual=False))
        await interaction.response.send_modal(
            ProfileModal(is_male=self.is_male, casual=False, wait_audio=self.wait_audio)
        )


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
    # 面接チャンネル（録音あり）なら、録音が揃うまで審査へ回さず待ち合わせる
    wait_audio = topic.startswith(INTERVIEW_TOPIC_PREFIX)
    # 性別は面接ルートか、男性ロールの有無で判定（プロフのみの男性ルート用）
    is_male = wait_audio or (
        MALE_ROLE_ID != 0
        and isinstance(interaction.user, discord.Member)
        and interaction.user.get_role(MALE_ROLE_ID) is not None
    )
    # 先に「雑談 / 恋愛」を選ばせ、雑談なら短縮プロフィールにする
    await interaction.response.send_message(
        "このサーバーでの目的を選んでください：\n"
        "💬 **雑談**：雑談のみを楽しみたい方\n"
        "❤️ **恋愛**：雑談しつつ恋愛も楽しみたい方\n\n"
        "⚠️ **雑談** を選ぶと **雑談ロール** が付与され、"
        "**個通部屋が使えなくなります**のでご注意ください。",
        view=ProfileTypeChoiceView(is_male=is_male, wait_audio=wait_audio),
        ephemeral=True,
    )


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
