import logging
import os

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

DEFAULT_LOG_CHANNEL_ID = 1530463807418273913


class JoinLeaveLog(commands.Cog):
    """サーバーの入退室をログチャンネルに Embed で流す（入室=緑／退室=赤）。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log_channel_id = int(
            os.getenv("JOIN_LEAVE_LOG_CHANNEL_ID") or DEFAULT_LOG_CHANNEL_ID
        )

    async def _send(self, embed: discord.Embed):
        if not self.log_channel_id:
            return
        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.log_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"入退室ログチャンネルを取得できませんでした: {e}")
                return
        if not isinstance(channel, discord.abc.Messageable):
            logger.error("入退室ログチャンネルがテキストチャンネルではありません")
            return
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"入退室ログの送信に失敗しました: {e}")

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
