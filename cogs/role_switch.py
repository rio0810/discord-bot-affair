import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta

from core.admin_base import AdminCogBase
from core.db_base import DatabaseBase
from ui.profile_wizard import (
    ZERO_ROMANCE_ROLE_ID,
    ROMANCE_ROLE_ID,
    _hide_category_from_role,
)

# ロール切り替えのクールタイム
SWITCH_COOLDOWN = timedelta(days=14)


class RoleSwitchActions(discord.ui.ActionRow):
    """ロール切替パネルのボタン行。"""

    def __init__(self, cog: "RoleSwitch"):
        super().__init__()
        self.cog = cog

    @discord.ui.button(label="雑談", style=discord.ButtonStyle.gray, emoji="💬", custom_id="persistent:role_zero")
    async def zero(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_switch(interaction, to_zero=True)

    @discord.ui.button(label="恋愛", style=discord.ButtonStyle.red, emoji="❤️", custom_id="persistent:role_romance")
    async def romance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_switch(interaction, to_zero=False)


class RoleSwitchPanel(discord.ui.LayoutView):
    """Components V2 のロール切替パネル。"""

    def __init__(self, cog: "RoleSwitch"):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_colour=discord.Colour.pink())
        container.add_item(discord.ui.TextDisplay("## 🔄 ロール切り替え"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(
            discord.ui.TextDisplay(
                "下のボタンで **雑談 / 恋愛** のロールを切り替えられます。\n\n"
                "- 💬 **雑談**：恋愛目的でない方向け（個通部屋は使えなくなります）。\n"
                "- ❤️ **恋愛**：恋愛目的の方向け。\n\n"
                "※ 2つのロールは同時に持てません（切り替えると前のロールは外れます）。\n"
                "※ 切り替えは **2週間に1回まで** です。"
            )
        )
        container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large))
        container.add_item(RoleSwitchActions(cog))
        self.add_item(container)


class RoleSwitch(commands.Cog, DatabaseBase):
    """雑談 / 恋愛 ロールを本人が切り替えるパネル（切り替えは2週間に1回まで）。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RoleSwitchPanel(self))
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS role_switch_cooldowns (
                            user_id BIGINT PRIMARY KEY,
                            last_switch TIMESTAMP NOT NULL
                        )
                    """)
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] role_switch_cooldowns テーブルの作成に失敗しました: {e}")

    def _cooldown_remaining(self, user_id: int):
        """クールタイム中なら残り時間の文字列を返す。使用可能なら None。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT last_switch FROM role_switch_cooldowns WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
        except Exception as e:
            print(f"[ERROR] クールタイム取得に失敗しました: {e}")
            return None
        if not row:
            return None
        elapsed = datetime.now() - row[0]
        if elapsed >= SWITCH_COOLDOWN:
            return None
        rem = SWITCH_COOLDOWN - elapsed
        return f"{rem.days}日{rem.seconds // 3600}時間"

    def _record_switch(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO role_switch_cooldowns (user_id, last_switch) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET last_switch = EXCLUDED.last_switch",
                        (user_id, datetime.now()),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] クールタイム記録に失敗しました: {e}")

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="set_role_panel", description="【管理者専用】雑談/恋愛ロールの切替パネルを設置します")
    async def set_role_panel(self, interaction: discord.Interaction):
        await interaction.channel.send(view=RoleSwitchPanel(self))
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)

    async def handle_switch(self, interaction: discord.Interaction, to_zero: bool):
        guild = interaction.guild
        member = interaction.user
        if not isinstance(member, discord.Member):
            return

        zero_role = guild.get_role(ZERO_ROMANCE_ROLE_ID) if ZERO_ROMANCE_ROLE_ID else None
        romance_role = guild.get_role(ROMANCE_ROLE_ID) if ROMANCE_ROLE_ID else None

        grant, remove = (zero_role, romance_role) if to_zero else (romance_role, zero_role)
        if grant is None:
            await interaction.response.send_message(
                "❌ ロールが設定されていません。管理者にお問い合わせください。", ephemeral=True
            )
            return

        if grant in member.roles:
            await interaction.response.send_message(
                f"すでに「{grant.name}」が付与されています。", ephemeral=True
            )
            return

        # クールタイム判定（実際に切り替わるときだけ消費）
        remaining = self._cooldown_remaining(member.id)
        if remaining is not None:
            await interaction.response.send_message(
                f"❌ ロールの切り替えは **2週間に1回** までです。あと **{remaining}** で再度切り替えできます。",
                ephemeral=True,
            )
            return

        try:
            if remove is not None and remove in member.roles:
                await member.remove_roles(remove, reason="ロール切替パネルによる整理")
            await member.add_roles(grant, reason="ロール切替パネルによる付与")
        except discord.Forbidden:
            print(f"[ERROR] ロール切替に失敗（権限不足）: {grant.id} -> {member.id}")
            await interaction.response.send_message(
                "❌ ロールの変更に失敗しました。管理者にお問い合わせください。", ephemeral=True
            )
            return

        # 雑談ロールのときは指定カテゴリを非表示に
        if to_zero and zero_role is not None:
            await _hide_category_from_role(guild, zero_role)

        self._record_switch(member.id)
        await interaction.response.send_message(
            f"✅ 「{grant.name}」に切り替えました。\n次に切り替えできるのは **2週間後** です。", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleSwitch(bot))
