import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from core import config
from core.log_channel import send_log_embed

logger = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    """滞在時間を「1時間23分45秒」の形にする。"""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours}時間")
    if minutes:
        parts.append(f"{minutes}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


class VCLog(commands.Cog):
    """VCの入退室・移動をログチャンネルに Embed で流す（参加=緑／退出=赤／移動=青）。

    `VC_LOG_CHANNEL_ID` が未設定なら何もしない。
    ミュートやカメラON/OFFなど、チャンネルが変わらない状態変化は無視する。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log_channel_id = config.VC_LOG_CHANNEL_ID
        self.excluded_channel_ids = set(config.VC_LOG_EXCLUDED_CHANNEL_IDS)
        # (member_id, channel_id) -> そのチャンネルに入った時刻。滞在時間の算出に使う
        self._joined_at: dict[tuple[int, int], datetime] = {}

    def _is_excluded(self, channel: discord.abc.GuildChannel | None) -> bool:
        if channel is None:
            return False
        return (
            channel.id in self.excluded_channel_ids
            or (channel.category_id or 0) in self.excluded_channel_ids
        )

    def _base_embed(self, member: discord.Member, description: str, color: discord.Color):
        embed = discord.Embed(
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_footer(text=f"ユーザーID: {member.id}")
        return embed

    def _stay_value(self, member_id: int, channel_id: int) -> str | None:
        """記録した入室時刻から滞在時間の文字列を作る（記録が無ければ None）。"""
        started = self._joined_at.pop((member_id, channel_id), None)
        if started is None:
            return None
        seconds = (datetime.now(timezone.utc) - started).total_seconds()
        return _format_duration(seconds)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot or not self.log_channel_id:
            return
        # ミュート・スピーカーミュート・配信開始などチャンネル移動を伴わない変化は対象外
        if before.channel == after.channel:
            return

        now = datetime.now(timezone.utc)
        if after.channel is not None:
            self._joined_at[(member.id, after.channel.id)] = now

        before_excluded = self._is_excluded(before.channel)
        after_excluded = self._is_excluded(after.channel)

        if before.channel is None:
            # 参加
            if after_excluded:
                return
            embed = self._base_embed(
                member,
                f"{member.mention} が {after.channel.mention} に参加しました",
                discord.Color.green(),
            )
        elif after.channel is None:
            # 退出
            stay = self._stay_value(member.id, before.channel.id)
            if before_excluded:
                return
            embed = self._base_embed(
                member,
                f"{member.mention} が {before.channel.mention} から退出しました",
                discord.Color.red(),
            )
            if stay:
                embed.add_field(name="⏱ 滞在時間", value=stay, inline=False)
        else:
            # 移動
            stay = self._stay_value(member.id, before.channel.id)
            if before_excluded and after_excluded:
                return
            embed = self._base_embed(
                member,
                f"{member.mention} がVCを移動しました",
                discord.Color.blurple(),
            )
            embed.add_field(
                name="🔀 移動",
                value=f"{before.channel.mention} → {after.channel.mention}",
                inline=False,
            )
            if stay:
                embed.add_field(name="⏱ 移動前の滞在時間", value=stay, inline=False)

        await send_log_embed(self.bot, self.log_channel_id, embed, label="VCログ")


async def setup(bot: commands.Bot):
    await bot.add_cog(VCLog(bot))
