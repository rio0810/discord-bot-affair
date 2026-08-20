import logging

import discord
from discord.ext import commands

from core import config
from core.log_channel import send_log_embed

logger = logging.getLogger(__name__)


class JoinLeaveLog(commands.Cog):
    """サーバーの入退室をログチャンネルに Embed で流す（入室=緑／退室=赤）。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log_channel_id = config.JOIN_LEAVE_LOG_CHANNEL_ID

    async def _send(self, embed: discord.Embed):
        await send_log_embed(self.bot, self.log_channel_id, embed, label="入退室ログ")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        embed = discord.Embed(
            description=f"{member.mention} がサーバーに参加しました",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="⏲ アカウントの年齢:",
            value=(
                f"{discord.utils.format_dt(member.created_at, 'f')}\n"
                f"{discord.utils.format_dt(member.created_at, 'R')}"
            ),
            inline=False,
        )
        await self._send(embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        embed = discord.Embed(
            description=f"{member.mention} が脱退しました",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self._send(embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinLeaveLog(bot))
