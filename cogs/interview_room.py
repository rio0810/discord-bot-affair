import discord
from discord.ext import commands
from discord import app_commands
import os

from core.admin_base import AdminCogBase
from ui.profile_wizard import ProfileStartView, RoomPanelView
from ui.recording_score import is_audio

# 作成したチャンネルの topic に埋め込むプレフィックス（種別・所有者の識別用）
INTERVIEW_TOPIC_PREFIX = "interview_room:"  # Aボタン：アピール録音用
PROFILE_TOPIC_PREFIX = "profile_room:"      # Bボタン：プロフィール記載用


class AppealPanelActions(discord.ui.ActionRow):
    """受付パネルのボタン行。custom_id は旧パネルと共通なので既設パネルも動く。"""

    def __init__(self, cog: "InterviewRoomCog"):
        super().__init__()
        self.cog = cog

    @discord.ui.button(label="男性", style=discord.ButtonStyle.green, emoji="♂", custom_id="persistent:appeal_a")
    async def button_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_appeal(interaction)

    @discord.ui.button(label="女性", style=discord.ButtonStyle.blurple, emoji="♀", custom_id="persistent:appeal_b")
    async def button_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_profile(interaction)


class AppealPanelView(discord.ui.LayoutView):
    """受付パネル"""

    def __init__(self, cog: "InterviewRoomCog"):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        container.add_item(discord.ui.TextDisplay("## 📮 面接・案内パネル"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(
            discord.ui.TextDisplay(
                "下のボタンを押すと、あなた専用のチャンネルが作成されます。\n\n"
                "🎤 **男性**：録音での面接あり\n"
                "📝 **女性**：プロフィール審査のみ"
            )
        )
        # 文章とボタンの間の余白（線は表示しない）
        container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large))
        container.add_item(AppealPanelActions(cog))
        self.add_item(container)


class InterviewRoomCog(commands.Cog):
    """コマンドで A/B ボタンのパネルを設置し、
    A：アピール録音提出用チャンネル（投稿音声は管理者チャンネルへ転送）、
    B：プロフィール記載用チャンネル、を押した人ごとに作成する。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID", "0"))
        self.male_role_id = int(os.getenv("MALE_ROLE_ID", "0"))
        self.female_role_id = int(os.getenv("FEMALE_ROLE_ID", "0"))
        self.category_id = int(os.getenv("INTERVIEW_ROOM_CATEGORY_ID", "0"))
        # 録音の転送先（未設定なら転送は行われない）
        self.forward_channel_id = int(os.getenv("RECORDING_FORWARD_CHANNEL_ID", "0"))

    async def cog_load(self):
        # 再起動後もボタンが反応するよう永続ビューを登録
        self.bot.add_view(AppealPanelView(self))
        self.bot.add_view(ProfileStartView())

    # ------------------------------------------------------------------ #
    # パネル設置コマンド
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="set_appeal_panel", description="【管理者専用】面接・面談パネルを設置します")
    async def set_appeal_panel(self, interaction: discord.Interaction):
        # Components V2（LayoutView）は embed / content と併用不可のため view のみ送信
        await interaction.channel.send(view=AppealPanelView(self))
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)

    # ------------------------------------------------------------------ #
    # ボタン処理
    # ------------------------------------------------------------------ #
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
                print(f"[ERROR] ロール付与の権限がありません: {role.id} -> {user.id}")
                await interaction.followup.send(
                    "❌ ロールの付与に失敗しました。管理者にお問い合わせください。", ephemeral=True
                )
                return

        # 既に同種のチャンネルがあれば再利用（重複作成を防止）
        existing = discord.utils.get(guild.text_channels, topic=f"{topic_prefix}{user.id}")
        if existing is not None:
            await interaction.followup.send(f"既にあなた用の {existing.mention} があります。", ephemeral=True)
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
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 専用チャンネル作成に失敗しました ({user.id}): {e}")
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

        # 採点はプロフィール作成と揃った時点で行う（RecordingScore が待ち合わせ）
        cog = self.bot.get_cog("RecordingScore")
        if cog is not None:
            await cog.on_interview_audio(message, audio_attachments)

    def _is_interview_room(self, channel: discord.abc.GuildChannel) -> bool:
        return (
            isinstance(channel, discord.TextChannel)
            and channel.topic is not None
            and channel.topic.startswith(INTERVIEW_TOPIC_PREFIX)
        )

    # ------------------------------------------------------------------ #
    # 退出時：本人の専用チャンネルを掃除
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        targets = {f"{INTERVIEW_TOPIC_PREFIX}{member.id}", f"{PROFILE_TOPIC_PREFIX}{member.id}"}
        for channel in list(guild.text_channels):
            if channel.topic in targets:
                try:
                    await channel.delete(reason=f"{member} がサーバーを退出したため専用チャンネルを削除")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(InterviewRoomCog(bot))
