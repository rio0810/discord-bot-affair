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

    def _base_embed(self, member: discord.Member, title: str, color: discord.Color):
        embed = discord.Embed(
            title=title,
            description=f"{member.mention}（{member}）",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ユーザーID", value=str(member.id), inline=False)
        embed.add_field(
            name="アカウント作成日",
            value=discord.utils.format_dt(member.created_at, "F"),
            inline=False,
        )
        embed.set_footer(text=f"現在のメンバー数: {member.guild.member_count}")
        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        embed = self._base_embed(member, "🟢 サーバーに参加しました", discord.Color.green())
        await self._send(embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        embed = self._base_embed(member, "🔴 サーバーから退出しました", discord.Color.red())
        if member.joined_at:
            embed.add_field(
                name="参加日",
                value=discord.utils.format_dt(member.joined_at, "F"),
                inline=False,
            )
        await self._send(embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinLeaveLog(bot))
