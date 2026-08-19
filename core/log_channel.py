"""ログ用テキストチャンネルへ Embed を送る共通ヘルパー。

入退室ログ・VCログのように「設定されたチャンネルIDに Embed を流すだけ」の
処理を1箇所にまとめる。チャンネル未設定・取得失敗・送信失敗はすべて
握りつぶして呼び出し側の処理を止めない（ログ出力のみ）。
"""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


async def send_log_embed(
    bot: commands.Bot,
    channel_id: int,
    embed: discord.Embed,
    *,
    label: str = "ログ",
) -> bool:
    """指定チャンネルへ Embed を送る。送れたら True。

    label はエラーメッセージに出す用途の名前（例: "VCログ"）。
    """
    if not channel_id:
        return False
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"{label}チャンネルを取得できませんでした: {e}")
            return False
    if not isinstance(channel, discord.abc.Messageable):
        logger.error(f"{label}チャンネルがテキストチャンネルではありません")
        return False
    try:
        await channel.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.error(f"{label}の送信に失敗しました: {e}")
        return False
