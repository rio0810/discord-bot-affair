import logging

import discord
from discord.ext import commands

from core import config
from core.log_channel import send_log_embed

logger = logging.getLogger(__name__)

# Embed の1フィールドは1024文字まで。リンク行などの余白を見て少し余裕を持たせる
MAX_FIELD_LEN = 1000


def _clip(text: str) -> str:
    """Embed のフィールドに収まる長さへ切り詰める。"""
    if not text:
        return "（本文なし）"
    if len(text) <= MAX_FIELD_LEN:
        return text
    return text[:MAX_FIELD_LEN] + "…"


class MessageLog(commands.Cog):
    """メッセージの編集・削除をログチャンネルに Embed で流す（編集=金／削除=赤）。

    どちらも `MESSAGE_LOG_CHANNEL_ID` へ送る。未設定なら何もしない。
    埋め込みの遅延生成など本文が変わらない編集や、Bot・DMは対象外。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log_channel_id = config.MESSAGE_LOG_CHANNEL_ID
        self.excluded_channel_ids = set(config.MESSAGE_LOG_EXCLUDED_CHANNEL_IDS)

    def _is_excluded(self, channel: discord.abc.GuildChannel | None) -> bool:
        if channel is None:
            return False
        return (
            channel.id in self.excluded_channel_ids
            or (getattr(channel, "category_id", 0) or 0) in self.excluded_channel_ids
            # スレッドは親チャンネル単位でも除外できるようにする
            or (getattr(channel, "parent_id", 0) or 0) in self.excluded_channel_ids
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not self.log_channel_id:
            return
        if after.guild is None or after.author.bot:
            return
        # 埋め込みプレビューの後付けなど、本文が変わっていない更新は無視
        if before.content == after.content:
            return
        if self._is_excluded(after.channel):
            return

        embed = discord.Embed(
            description=(
                f"{after.author.mention} が "
                f"{after.channel.mention} のメッセージを編集しました\n"
                f"[メッセージへ移動]({after.jump_url})"
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(after.author), icon_url=after.author.display_avatar.url)
        embed.set_footer(text=f"ユーザーID: {after.author.id}")
        embed.add_field(name="編集前", value=_clip(before.content), inline=False)
        embed.add_field(name="編集後", value=_clip(after.content), inline=False)

        await send_log_embed(self.bot, self.log_channel_id, embed, label="メッセージログ")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not self.log_channel_id:
            return
        if message.guild is None or message.author.bot:
            return
        if self._is_excluded(message.channel):
            return

        embed = discord.Embed(
            description=(
                f"{message.author.mention} の "
                f"{message.channel.mention} のメッセージが削除されました"
            ),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=str(message.author), icon_url=message.author.display_avatar.url
        )
        embed.set_footer(text=f"ユーザーID: {message.author.id}")
        embed.add_field(name="本文", value=_clip(message.content), inline=False)
        if message.attachments:
            # 添付そのものは削除済みで取得できないため、ファイル名だけ残す
            names = "\n".join(a.filename for a in message.attachments)
            embed.add_field(name="添付ファイル", value=_clip(names), inline=False)

        await send_log_embed(
            self.bot, self.log_channel_id, embed, label="メッセージ削除ログ"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageLog(bot))
