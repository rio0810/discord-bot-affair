import logging
import discord
from discord.ext import commands
from discord import app_commands

from core import config
from core.admin_base import AdminCogBase
from cogs.profile.wizard import ProfileStartView, RoomPanelView
from cogs.onboarding.recording_score.ui import is_audio


logger = logging.getLogger(__name__)

# 男性の審査方式（サーバー全体の設定。bot_settings テーブルに保存する）
MALE_MODE_AUDIO = "audio"      # 録音での面接あり
MALE_MODE_PROFILE = "profile"  # 録音なし・プロフィールのみ
MALE_MODE_KEY = "male_review_mode"
MALE_MODE_LABELS = {
    MALE_MODE_AUDIO: "録音あり（面接）",
    MALE_MODE_PROFILE: "プロフ審査のみ",
}

# 作成したチャンネルの topic に埋め込むプレフィックス（種別・所有者の識別用）
INTERVIEW_TOPIC_PREFIX = "interview_room:"  # Aボタン：アピール録音用
PROFILE_TOPIC_PREFIX = "profile_room:"      # Bボタン：プロフィール記載用


def result_embed(description: str, *, title: str, colour: discord.Colour) -> discord.Embed:
    """コマンド応答用の Embed。"""
    return discord.Embed(title=title, description=description, colour=colour)


def success_embed(description: str, *, title: str = "✅ 完了") -> discord.Embed:
    return result_embed(description, title=title, colour=discord.Colour.green())


def error_embed(description: str, *, title: str = "❌ エラー") -> discord.Embed:
    return result_embed(description, title=title, colour=discord.Colour.red())


def info_embed(description: str, *, title: str = "ℹ️ 現在の設定") -> discord.Embed:
    return result_embed(description, title=title, colour=discord.Colour.blurple())


class AppealPanelActions(discord.ui.ActionRow):
    """受付パネルのボタン行。custom_id は旧パネルと共通なので既設パネルも動く。"""

    def __init__(self, cog: "InterviewRoomCog"):
        super().__init__()
        self.cog = cog

    @discord.ui.button(label="男性", style=discord.ButtonStyle.green, emoji="♂", custom_id="persistent:appeal_a")
    async def button_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 現在の男性モード（録音あり / プロフのみ）に従う
        await self.cog.handle_male(interaction)

    @discord.ui.button(label="女性", style=discord.ButtonStyle.blurple, emoji="♀", custom_id="persistent:appeal_b")
    async def button_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_profile(interaction)


class LegacyMaleProfileView(discord.ui.View):
    """3ボタン版パネルを設置済みの場合に、旧「男性（プロフ審査のみ）」ボタンを
    受け止めるための永続ビュー。押されたら現在のモードに従う。"""

    def __init__(self, cog: "InterviewRoomCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="男性（プロフ審査のみ）", style=discord.ButtonStyle.gray, emoji="📝",
        custom_id="persistent:appeal_c",
    )
    async def button_c(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_male(interaction)


class AppealPanelView(discord.ui.LayoutView):
    """受付パネル"""

    def __init__(self, cog: "InterviewRoomCog"):
        super().__init__(timeout=None)
        male_note = (
            "🎤 **男性**：録音での面接あり"
            if cog.male_mode == MALE_MODE_AUDIO
            else "📝 **男性**：録音なし・プロフィールのみで審査"
        )
        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        container.add_item(discord.ui.TextDisplay("## 📮 面接・案内パネル"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(
            discord.ui.TextDisplay(
                "下のボタンを押すと、あなた専用のチャンネルが作成されます。\n\n"
                f"{male_note}\n"
                "📝 **女性**：プロフィール審査のみ"
            )
        )
        # 文章とボタンの間の余白（線は表示しない）
        container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large))
        container.add_item(AppealPanelActions(cog))
        self.add_item(container)


class InterviewRoomCog(AdminCogBase):
    """コマンドで A/B ボタンのパネルを設置し、
    A：男性（現在のモードに応じて録音面接 or プロフのみ）、
    B：プロフィール記載用チャンネル、を押した人ごとに作成する。

    男性の審査方式は `/set_male_mode` でサーバー全体を切り替え、
    bot_settings テーブルに保存するので再起動しても維持される。"""

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.admin_role_id = config.ADMIN_ROLE_ID
        self.male_role_id = config.MALE_ROLE_ID
        self.female_role_id = config.FEMALE_ROLE_ID
        self.category_id = config.INTERVIEW_ROOM_CATEGORY_ID
        # 録音の転送先（未設定なら転送は行われない）
        self.forward_channel_id = config.RECORDING_FORWARD_CHANNEL_ID
        # 男性の審査方式（DBから読み込むまでは従来どおり録音あり）
        self.male_mode = MALE_MODE_AUDIO

    async def cog_load(self):
        await self.run_db(self._ensure_settings_table)
        self.male_mode = await self.run_db(self._load_male_mode)
        logger.info(f"男性の審査方式: {MALE_MODE_LABELS[self.male_mode]}")
        # 再起動後もボタンが反応するよう永続ビューを登録
        self.bot.add_view(AppealPanelView(self))
        self.bot.add_view(LegacyMaleProfileView(self))
        self.bot.add_view(ProfileStartView())

    # ------------------------------------------------------------------ #
    # 男性の審査方式（サーバー全体の設定）
    # ------------------------------------------------------------------ #
    def _ensure_settings_table(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS bot_settings (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        )
                    """)
        except Exception as e:
            logger.error(f"bot_settings の準備に失敗しました: {e}")

    def _load_male_mode(self) -> str:
        """保存済みの審査方式を読む。読めなければ従来どおり録音あり。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM bot_settings WHERE key = %s", (MALE_MODE_KEY,))
                    row = cur.fetchone()
        except Exception as e:
            logger.error(f"男性の審査方式の読み込みに失敗しました: {e}")
            return MALE_MODE_AUDIO
        value = row[0] if row else None
        return value if value in MALE_MODE_LABELS else MALE_MODE_AUDIO

    def _save_male_mode(self, mode: str):
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bot_settings (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (MALE_MODE_KEY, mode))

    # ------------------------------------------------------------------ #
    # パネル設置コマンド
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="set_appeal_panel", description="【管理者専用】面接・面談パネルを設置します")
    async def set_appeal_panel(self, interaction: discord.Interaction):
        # Components V2（LayoutView）は embed / content と併用不可のため view のみ送信
        await interaction.channel.send(view=AppealPanelView(self))
        await interaction.response.send_message(
            embed=success_embed(
                f"{interaction.channel.mention} にパネルを設置しました。\n"
                f"男性の審査方式：**{MALE_MODE_LABELS[self.male_mode]}**",
                title="✅ パネルを設置しました",
            ),
            ephemeral=True,
        )

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(
        name="set_male_mode", description="【管理者専用】男性の審査方式（録音あり / プロフのみ）を切り替えます"
    )
    @app_commands.describe(mode="男性がどの方法で審査を受けるか")
    @app_commands.choices(mode=[
        app_commands.Choice(name="録音あり（面接）", value=MALE_MODE_AUDIO),
        app_commands.Choice(name="プロフ審査のみ", value=MALE_MODE_PROFILE),
    ])
    async def set_male_mode(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.run_db(self._save_male_mode, mode.value)
        except Exception as e:
            logger.error(f"男性の審査方式の保存に失敗しました: {e}")
            await interaction.followup.send(
                embed=error_embed("設定の保存に失敗しました。しばらく待って再度お試しください。"),
                ephemeral=True,
            )
            return
        self.male_mode = mode.value
        logger.info(f"男性の審査方式を {MALE_MODE_LABELS[mode.value]} に変更しました（{interaction.user.id}）")
        embed = success_embed(
            f"男性の審査方式を **{MALE_MODE_LABELS[mode.value]}** に切り替えました。",
            title="✅ 審査方式を変更しました",
        )
        embed.add_field(
            name="設置済みパネルについて",
            value=(
                "ボタンの動作は即座に切り替わりますが、説明文は設置時のままです。\n"
                "文言も更新したい場合は `/set_appeal_panel` で置き直してください。"
            ),
            inline=False,
        )
        embed.set_footer(text=f"変更者: {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="male_mode", description="【管理者専用】現在の男性の審査方式を表示します")
    async def show_male_mode(self, interaction: discord.Interaction):
        embed = info_embed(
            f"男性の審査方式：**{MALE_MODE_LABELS[self.male_mode]}**",
            title="ℹ️ 男性の審査方式",
        )
        embed.add_field(
            name="切り替え",
            value="`/set_male_mode` で「録音あり（面接）」「プロフ審査のみ」を変更できます。",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # ボタン処理
    # ------------------------------------------------------------------ #
    async def handle_male(self, interaction: discord.Interaction):
        """男性ボタン。現在のモードに応じて録音面接 or プロフのみへ振り分ける。"""
        if self.male_mode == MALE_MODE_PROFILE:
            await self.handle_male_profile(interaction)
        else:
            await self.handle_appeal(interaction)

    async def handle_appeal(self, interaction: discord.Interaction):
        await self._handle_button(
            interaction, topic_prefix=INTERVIEW_TOPIC_PREFIX, name_emoji="🎤",
            title="🎤 面接で行っていただく事",
            description=(
                "・**mp3などの録音ファイル**、または**Discordの録音機能**で以下の内容に沿って回答する\n"
                "・下にある**プロフィールを作成**を押してプロフィールを作成する\n\n"
                "**🗣️ 録音で話していただく内容**\n"
                "・簡単な自己紹介をお願いします（名前・年齢〔生年月日〕・在住 など）\n"
                "・趣味に関して一つお話ししてください（ここが好き・おすすめしたい など）\n"
                "・夢や野心、目標などを一つお話ししてください\n\n"
                "※1 録音ファイルで回答した場合はこのチャンネルに投稿してください。\n"
                "※2 投稿された音声は自動的に担当者へ共有されます。\n"
                "※3 分からない点がありましたらこちらのチャットで質問をお願いします。\n"
            ),
            colour=discord.Colour.green(),
            role_id=self.male_role_id, opposite_role_id=self.female_role_id,
            conflict_prefixes=(PROFILE_TOPIC_PREFIX,),
        )

    async def handle_male_profile(self, interaction: discord.Interaction):
        """男性で録音なし・プロフィールのみで審査を受けるルート。
        チャンネル種別はプロフィール用（PROFILE_TOPIC_PREFIX）にするので、
        ウィザードは録音を待たずそのまま審査へ回す（投稿先は男性フォーラム）。"""
        await self._handle_button(
            interaction, topic_prefix=PROFILE_TOPIC_PREFIX, name_emoji="📝",
            title="📝 プロフィールの記載",
            description=(
                "**プロフィールを記載して下さい。**\n\n"
                "下のボタンを押してプロフィールを投稿してください。\n"
                "録音の提出は不要です。確認し次第運営から連絡させていただきます。"
            ),
            colour=discord.Colour.green(),
            role_id=self.male_role_id, opposite_role_id=self.female_role_id,
            conflict_prefixes=(INTERVIEW_TOPIC_PREFIX,),
        )

    async def handle_profile(self, interaction: discord.Interaction):
        await self._handle_button(
            interaction, topic_prefix=PROFILE_TOPIC_PREFIX, name_emoji="📝",
            title="📝 プロフィールの記載",
            description=(
                "**プロフィールを記載して下さい。**\n\n"
                "下のボタンを押してプロフィールを投稿してください。\n"
                "確認し次第運営から連絡させていただきます。"
            ),
            colour=discord.Colour.blurple(),
            role_id=self.female_role_id, opposite_role_id=self.male_role_id,
        )

    async def _handle_button(
        self,
        interaction: discord.Interaction,
        *,
        topic_prefix: str,
        name_emoji: str,
        title: str,
        description: str,
        colour: discord.Colour,
        role_id: int = 0,
        opposite_role_id: int = 0,
        conflict_prefixes: tuple[str, ...] = (),
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user

        # 反対側のロールを既に持っている場合は誤操作として弾く
        opposite_role = guild.get_role(opposite_role_id) if opposite_role_id else None
        if opposite_role is not None and opposite_role in user.roles:
            await interaction.followup.send(
                f"❌ あなたは既に「{opposite_role.name}」ロールが付与されているため、このボタンは使用できません。",
                ephemeral=True,
            )
            return

        # 対応するロールを付与
        role = guild.get_role(role_id) if role_id else None
        if role is not None and role not in user.roles:
            try:
                await user.add_roles(role, reason="面接・案内パネルのボタン押下によるロール付与")
            except discord.Forbidden:
                logger.error(f"ロール付与の権限がありません: {role.id} -> {user.id}")
                await interaction.followup.send(
                    "❌ ロールの付与に失敗しました。管理者にお問い合わせください。", ephemeral=True
                )
                return

        # 既に同種のチャンネルがあれば再利用（重複作成を防止）
        existing = discord.utils.get(guild.text_channels, topic=f"{topic_prefix}{user.id}")
        if existing is not None:
            await interaction.followup.send(f"既にあなた用の {existing.mention} があります。", ephemeral=True)
            return

        # 別ルート（面接／プロフのみ）で既に受付済みなら二重提出になるので弾く
        for prefix in conflict_prefixes:
            other = discord.utils.get(guild.text_channels, topic=f"{prefix}{user.id}")
            if other is not None:
                await interaction.followup.send(
                    f"❌ 既に別の方法で受付済みです（{other.mention}）。"
                    "変更したい場合は運営にご連絡ください。",
                    ephemeral=True,
                )
                return

        channel = await self._create_personal_channel(guild, user, topic_prefix, name_emoji)
        if channel is None:
            await interaction.followup.send(
                "❌ チャンネルの作成に失敗しました。Botの権限をご確認ください。", ephemeral=True
            )
            return

        await channel.send(view=RoomPanelView(user.mention, title, description, colour))
        await interaction.followup.send(f"✅ {channel.mention} を作成しました。", ephemeral=True)

    async def _create_personal_channel(
        self, guild: discord.Guild, user: discord.Member, topic_prefix: str, name_emoji: str
    ) -> discord.TextChannel | None:
        admin_role = guild.get_role(self.admin_role_id) if self.admin_role_id else None
        category = guild.get_channel(self.category_id) if self.category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        # 全員非表示 → 本人とBotを許可、管理者ロールがあれば許可
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        try:
            return await guild.create_text_channel(
                name=f"{name_emoji}｜{user.display_name}",
                category=category,
                overwrites=overwrites,
                topic=f"{topic_prefix}{user.id}",
                slowmode_delay=60,  # 1分に1通までのスローモード
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"専用チャンネル作成に失敗しました ({user.id}): {e}")
            return None

    # ------------------------------------------------------------------ #
    # 投稿時：面接チャンネルへの録音を待ち合わせコグへ渡す
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not self._is_interview_room(message.channel):
            return

        audio_attachments = [a for a in message.attachments if is_audio(a)]
        if not audio_attachments:
            return

        # まず「この音声でいいか」を本人に確認してから審査へ回す（RecordingScore が処理）
        cog = self.bot.get_cog("RecordingScore")
        if cog is not None:
            await cog.confirm_interview_audio(message, audio_attachments)

    def _is_interview_room(self, channel: discord.abc.GuildChannel) -> bool:
        return (
            isinstance(channel, discord.TextChannel)
            and channel.topic is not None
            and channel.topic.startswith(INTERVIEW_TOPIC_PREFIX)
        )

    # 退出時に本人の面接/プロフ用チャンネルは削除しない（審査記録として残す）。
    # 以前は on_member_remove で topic 一致のチャンネルを削除していたが、
    # 審査対象が退出しても審査内容が消えないよう、削除は行わない。


async def setup(bot: commands.Bot):
    await bot.add_cog(InterviewRoomCog(bot))
