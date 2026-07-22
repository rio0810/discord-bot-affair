import discord
from discord.ext import commands, tasks
import os

from core.db_base import DatabaseBase


class RenameModal(discord.ui.Modal, title="VC名を変更"):
    new_name = discord.ui.TextInput(
        label="新しいVC名",
        max_length=100,
        placeholder="例：まったり雑談",
    )

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.channel.edit(name=str(self.new_name), reason="一時VNの名前変更")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] VC名の変更に失敗しました: {e}")
            await interaction.followup.send("❌ 名前の変更に失敗しました。", ephemeral=True)
            return
        await interaction.followup.send(f"✅ VC名を「**{self.new_name}**」に変更しました。", ephemeral=True)


class TempVCPanel(discord.ui.View):
    """一時VCのテキストチャットに置く操作パネル（永続ビュー）。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="VC名を変更する", style=discord.ButtonStyle.blurple, emoji="✏️", custom_id="persistent:temp_vc_rename")
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "TempVC" = interaction.client.get_cog("TempVC")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ この操作はここでは使えません。", ephemeral=True)
            return

        owner_id = cog.get_owner(channel.id)
        admin_role = interaction.guild.get_role(cog.admin_role_id) if cog.admin_role_id else None
        is_owner = owner_id is not None and interaction.user.id == owner_id
        is_admin = admin_role is not None and admin_role in interaction.user.roles
        if not (is_owner or is_admin):
            await interaction.response.send_message(
                "❌ このVCの作成者（または管理者）のみ名前を変更できます。", ephemeral=True
            )
            return

        await interaction.response.send_modal(RenameModal(channel))


class TempVC(commands.Cog, DatabaseBase):
    """トリガーVCに入ると個人用のVCを作成し、そのVCのテキストチャットに
    名前変更パネルを表示する。VCが空になったら自動削除する。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        # トリガーVCはカンマ区切りで複数指定可
        env_lobby = os.getenv("LOBBY_VC_ID", "")
        self.lobby_vc_ids = {int(i.strip()) for i in env_lobby.split(",") if i.strip().isdigit()}
        self.category_id = int(os.getenv("TEMP_VC_CATEGORY_ID", "0"))
        self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID", "0"))

    async def cog_load(self):
        self._ensure_table()
        self.bot.add_view(TempVCPanel())
        self.empty_sweep.start()

    def cog_unload(self):
        self.empty_sweep.cancel()

    # ------------------------------------------------------------------ #
    # DB
    # ------------------------------------------------------------------ #
    def _ensure_table(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS temp_vcs (
                            channel_id BIGINT PRIMARY KEY,
                            owner_id BIGINT NOT NULL,
                            guild_id BIGINT NOT NULL
                        )
                    """)
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] temp_vcs テーブルの作成に失敗しました: {e}")

    def _register(self, channel_id: int, owner_id: int, guild_id: int):
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO temp_vcs (channel_id, owner_id, guild_id) VALUES (%s, %s, %s) "
                    "ON CONFLICT (channel_id) DO NOTHING",
                    (channel_id, owner_id, guild_id),
                )
                conn.commit()

    def _unregister(self, channel_id: int):
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM temp_vcs WHERE channel_id = %s", (channel_id,))
                conn.commit()

    def get_owner(self, channel_id: int) -> int | None:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT owner_id FROM temp_vcs WHERE channel_id = %s", (channel_id,))
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            print(f"[ERROR] 一時VCの所有者取得に失敗しました: {e}")
            return None

    def _all_temp_ids(self) -> list[int]:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT channel_id FROM temp_vcs")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            print(f"[ERROR] 一時VC一覧の取得に失敗しました: {e}")
            return []

    # ------------------------------------------------------------------ #
    # VC の作成／削除
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # トリガーVCに入った → 個人用VCを作成して移動
        if (
            after.channel is not None
            and after.channel.id in self.lobby_vc_ids
            and before.channel != after.channel
        ):
            await self._create_temp_vc(member, after.channel)

        # VCから抜けた／移動した → 元が一時VCで空なら削除
        if before.channel is not None and before.channel != after.channel:
            await self._delete_if_empty(before.channel)

    async def _create_temp_vc(self, member: discord.Member, lobby: discord.VoiceChannel):
        guild = member.guild
        category = guild.get_channel(self.category_id) if self.category_id else None
        if not isinstance(category, discord.CategoryChannel):
            # カテゴリ未指定なら、入ったトリガーVCと同じカテゴリに作成
            category = lobby.category if isinstance(lobby, discord.VoiceChannel) else None

        name = f"{member.display_name}の部屋"[:100]
        try:
            new_vc = await guild.create_voice_channel(name=name, category=category, reason="一時VCの作成")
            await member.move_to(new_vc, reason="一時VCへ移動")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 一時VCの作成/移動に失敗しました ({member.id}): {e}")
            return

        self._register(new_vc.id, member.id, guild.id)

        embed = discord.Embed(
            title="🔊 あなた専用のVCを作成しました",
            description=(
                f"{member.mention} さんの個人用VCです。\n"
                "下のボタンからVCの名前を自由に変更できます。\n"
                "全員が退出すると自動的に削除されます。"
            ),
            color=discord.Color.blurple(),
        )
        try:
            # VoiceChannel の埋め込みテキストチャットにパネルを送信（作成者を確実にメンション）
            await new_vc.send(
                content=f"{member.mention} さんの個人部屋を作成しました！",
                embed=embed,
                view=TempVCPanel(),
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 一時VCへのパネル送信に失敗しました: {e}")

    async def _delete_if_empty(self, channel: discord.abc.GuildChannel):
        if not isinstance(channel, discord.VoiceChannel):
            return
        if self.get_owner(channel.id) is None:
            return  # 一時VCではない
        if any(not m.bot for m in channel.members):
            return  # まだ人がいる
        try:
            await channel.delete(reason="一時VCが空になったため削除")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        self._unregister(channel.id)

    # ------------------------------------------------------------------ #
    # 保険：空の一時VCを定期的に掃除（Bot停止中に空になった分の回収）
    # ------------------------------------------------------------------ #
    @tasks.loop(minutes=5.0)
    async def empty_sweep(self):
        for channel_id in self._all_temp_ids():
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                self._unregister(channel_id)  # 既に消えている
                continue
            if isinstance(channel, discord.VoiceChannel) and not any(not m.bot for m in channel.members):
                try:
                    await channel.delete(reason="一時VCが空のため削除（定期掃除）")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                self._unregister(channel_id)

    @empty_sweep.before_loop
    async def before_empty_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVC(bot))
