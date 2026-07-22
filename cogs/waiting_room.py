import discord
from discord.ext import commands
import os


class WaitingRoom(commands.Cog):
    """待機ロールが付いている人を、指定カテゴリ以外から隔離する。

    ロール単位の拒否は他のロールの許可に負けるため、メンバー個別の上書き
    （最優先）で「対象カテゴリ以外を非表示」にする。ロールが外れたら解除。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.waiting_role_id = int(os.getenv("WAITING_ROLE_ID") or "0")
        self.visible_category_id = int(os.getenv("WAITING_CATEGORY_ID") or "0")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.waiting_role_id or not self.visible_category_id:
            return
        had = any(r.id == self.waiting_role_id for r in before.roles)
        has = any(r.id == self.waiting_role_id for r in after.roles)
        if has and not had:
            await self._isolate(after)
        elif had and not has:
            await self._release(after)

    def _hidden_targets(self, guild: discord.Guild):
        """隠す対象：対象カテゴリ以外の全カテゴリ ＋ カテゴリに属さないトップレベルチャンネル。"""
        targets = []
        for cat in guild.categories:
            if cat.id != self.visible_category_id:
                targets.append(cat)
        for ch in guild.channels:
            if not isinstance(ch, discord.CategoryChannel) and ch.category_id is None:
                targets.append(ch)
        return targets

    async def _isolate(self, member: discord.Member):
        guild = member.guild
        for target in self._hidden_targets(guild):
            try:
                await target.set_permissions(
                    member, view_channel=False, reason="待機ロール：対象カテゴリ以外を非表示"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        # 対象カテゴリは確実に見えるよう個別許可
        visible = guild.get_channel(self.visible_category_id)
        if isinstance(visible, discord.CategoryChannel):
            try:
                await visible.set_permissions(
                    member, view_channel=True, reason="待機ロール：対象カテゴリを表示"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _release(self, member: discord.Member):
        guild = member.guild
        targets = self._hidden_targets(guild)
        visible = guild.get_channel(self.visible_category_id)
        if isinstance(visible, discord.CategoryChannel):
            targets.append(visible)
        for target in targets:
            try:
                await target.set_permissions(member, overwrite=None, reason="待機ロール解除")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(WaitingRoom(bot))
