import discord
from discord.ext import commands
import logging
import os
import re

logger = logging.getLogger(__name__)

# VC に貼るプロフィール表示 embed のタイトル末尾（掃除時の判定用）
_PROFILE_TITLE_SUFFIX = "さんのプロフィール"
# embed author 名の末尾 "(@username)" からユーザー名を取り出す
_AUTHOR_USERNAME_RE = re.compile(r"\(@([^)]+)\)\s*$")


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
        # VC の置き去りプロフィール掃除を1プロセス1回だけ実行するためのフラグ
        self._swept_voice = False

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

        # 再起動でメモリ上の記録が消えたことによる置き去りプロフィールを掃除し、
        # 現在VCにいる人の分は再追跡する（1プロセス1回だけ）
        if not self._swept_voice:
            self._swept_voice = True
            await self._sweep_voice_profiles()

    def _is_profile_message(self, msg: discord.Message) -> str | None:
        """Bot自身が貼ったプロフィール表示メッセージなら、対象ユーザー名を返す。
        該当しなければ None。"""
        if self.bot.user is None or msg.author.id != self.bot.user.id:
            return None
        if not msg.embeds:
            return None
        embed = msg.embeds[0]
        title = embed.title or ""
        if not title.endswith(_PROFILE_TITLE_SUFFIX):
            return None
        author_name = embed.author.name if embed.author else None
        if not author_name:
            return None
        m = _AUTHOR_USERNAME_RE.search(author_name)
        return m.group(1) if m else None

    async def _sweep_voice_profiles(self):
        """全VCのテキストチャットを走査し、在室していない人のプロフィール表示を削除。
        在室中の人の分は最新1件だけ残して sent_messages に再登録する。"""
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                present = {m.name: m for m in vc.members if not m.bot}
                seen_member_ids: set[int] = set()
                try:
                    async for msg in vc.history(limit=50):
                        username = self._is_profile_message(msg)
                        if username is None:
                            continue
                        member = present.get(username)
                        if member is None:
                            # 在室していない → 置き去り。削除
                            await self._safe_delete(msg)
                            continue
                        # 在室中：履歴は新しい順なので最初の1件が最新。以降は重複として削除
                        if member.id in seen_member_ids:
                            await self._safe_delete(msg)
                        else:
                            seen_member_ids.add(member.id)
                            self.sent_messages[member.id] = msg
                except discord.Forbidden:
                    continue
                except discord.HTTPException as e:
                    logger.warning(f"VCプロフィール掃除の履歴取得に失敗しました（{vc.id}）: {e}")

    @staticmethod
    async def _safe_delete(msg: discord.Message):
        try:
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

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
            await self._safe_delete(bot_msg)

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
            logger.warning(f"チャンネル {after.channel.name} でメッセージ送信権限がありません。")

async def setup(bot):
    await bot.add_cog(VoiceProfile(bot))
