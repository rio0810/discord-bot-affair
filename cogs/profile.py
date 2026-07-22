import discord
from discord.ext import commands
import os

class VoiceProfile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        env_channels = os.getenv("PROFILE_TARGET_CHANNEL_IDS", "")
        self.profile_target_channel_ids = (
            [int(s.strip()) for s in env_channels.split(",") if s.strip()]
            if env_channels else []
        )
        self.sent_messages: dict[int, discord.Message] = {}
        # member_id -> 最新プロフィールメッセージ のキャッシュ
        self._profile_cache: dict[int, discord.Message] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        """起動時に一度だけ履歴を取得してキャッシュを構築する。"""
        self._profile_cache.clear()
        for channel_id in self.profile_target_channel_ids:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            async for msg in channel.history(limit=100):
                uid = msg.author.id
                cached = self._profile_cache.get(uid)
                if cached is None or msg.created_at > cached.created_at:
                    self._profile_cache[uid] = msg

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """プロフィールチャンネルへの投稿でキャッシュを更新する。"""
        if message.channel.id not in self.profile_target_channel_ids:
            return
        uid = message.author.id
        cached = self._profile_cache.get(uid)
        if cached is None or message.created_at > cached.created_at:
            self._profile_cache[uid] = message

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """キャッシュ中のメッセージが削除されたら再検索して更新する。"""
        if message.channel.id not in self.profile_target_channel_ids:
            return
        uid = message.author.id
        if self._profile_cache.get(uid) != message:
            return
        # 削除されたメッセージがキャッシュ済みだった場合のみ再検索
        latest = None
        for channel_id in self.profile_target_channel_ids:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            async for msg in channel.history(limit=100):
                if msg.author.id == uid:
                    if latest is None or msg.created_at > latest.created_at:
                        latest = msg
                    break
        if latest:
            self._profile_cache[uid] = latest
        else:
            self._profile_cache.pop(uid, None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or before.channel == after.channel:
            return

        # --- 古いメッセージを削除する処理 ---
        bot_msg = self.sent_messages.pop(member.id, None)
        if bot_msg:
            try:
                await bot_msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        # --- 新しいチャンネルにメッセージを送る処理 ---
        if after.channel is None:
            return

        latest_message = self._profile_cache.get(member.id)
        if not latest_message:
            return

        embed = discord.Embed(
            title=f"{member.display_name} さんのプロフィール",
            description=latest_message.content or "（本文なし）",
            color=discord.Color.blue(),
            timestamp=latest_message.created_at,
        )
        embed.set_author(
            name=f"{member.display_name} (@{member.name})",
            icon_url=member.display_avatar.url,
        )
        if latest_message.attachments:
            embed.set_image(url=latest_message.attachments[0].url)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="プロフィールへ移動",
            url=latest_message.jump_url,
            style=discord.ButtonStyle.link,
        ))

        try:
            sent = await after.channel.send(view=view, embed=embed)
            self.sent_messages[member.id] = sent
        except discord.Forbidden:
            print(f"チャンネル {after.channel.name} でメッセージ送信権限がありません。")

async def setup(bot):
    await bot.add_cog(VoiceProfile(bot))
