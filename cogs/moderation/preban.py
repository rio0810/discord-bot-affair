import logging
import re

import discord
from discord.ext import commands

from core import config

logger = logging.getLogger(__name__)

# 1回のコマンドで処理できるID数の上限（レート制限対策）
MAX_IDS = 20
ID_PATTERN = re.compile(r"[0-9]{15,25}")


class PreBan(commands.Cog):
    """ユーザーIDを指定して、サーバー未参加のユーザーを事前にBANする。

    DiscordのBANはユーザーがサーバーにいなくても登録でき、その後の参加を拒否できる。
    ID・メンション・改行/カンマ/スペース区切りの複数指定に対応する。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.admin_role_id = config.ADMIN_ROLE_ID

    def is_admin(self, member: discord.Member) -> bool:
        if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
            return True
        role = member.guild.get_role(self.admin_role_id) if self.admin_role_id else None
        return role is not None and role in member.roles

    def _parse_ids(self, raw: str):
        """入力からユーザーIDを抽出する（重複は除き、入力順を保つ）。"""
        seen = []
        for m in ID_PATTERN.findall(raw or ""):
            uid = int(m)
            if uid not in seen:
                seen.append(uid)
        return seen

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ サーバー内で実行してください。", ephemeral=True
            )
            return False
        if not self.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ このコマンドは管理者のみ使用できます。", ephemeral=True
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    # 事前BAN
    # ------------------------------------------------------------------ #
    @discord.app_commands.command(
        name="preban", description="管理者: ユーザーIDを指定して事前にBANします（未参加でも可）"
    )
    @discord.app_commands.describe(
        user_ids="ユーザーID（スペース・カンマ・改行区切りで複数指定可）",
        reason="BAN理由（監査ログに残ります）",
    )
    async def preban(
        self,
        interaction: discord.Interaction,
        user_ids: str,
        reason: str | None = None,
    ):
        if not await self._guard(interaction):
            return
        ids = self._parse_ids(user_ids)
        if not ids:
            await interaction.response.send_message(
                "❌ 有効なユーザーIDが見つかりませんでした。", ephemeral=True
            )
            return
        if len(ids) > MAX_IDS:
            await interaction.response.send_message(
                f"❌ 一度に指定できるのは{MAX_IDS}件までです（{len(ids)}件指定されています）。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        audit_reason = f"事前BAN（実行者: {interaction.user}）" + (f" 理由: {reason}" if reason else "")

        banned, already, failed = [], [], []
        for uid in ids:
            try:
                await guild.fetch_ban(discord.Object(id=uid))
                already.append(uid)
                continue
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                failed.append((uid, str(e)))
                continue
            try:
                await guild.ban(discord.Object(id=uid), reason=audit_reason, delete_message_seconds=0)
                banned.append(uid)
                logger.info(f"事前BAN: {uid}（実行者: {interaction.user.id} / 理由: {reason or 'なし'}）")
            except discord.Forbidden:
                failed.append((uid, "Botに権限がありません"))
            except discord.HTTPException as e:
                failed.append((uid, str(e)))

        embed = discord.Embed(
            title="🔨 事前BANの結果",
            color=discord.Color.red() if banned else discord.Color.greyple(),
            timestamp=discord.utils.utcnow(),
        )
        if reason:
            embed.description = f"**理由：**{reason}"
        if banned:
            embed.add_field(
                name=f"✅ BANしました（{len(banned)}件）",
                value="\n".join(f"<@{u}> `{u}`" for u in banned),
                inline=False,
            )
        if already:
            embed.add_field(
                name=f"⚠️ 既にBAN済み（{len(already)}件）",
                value="\n".join(f"`{u}`" for u in already),
                inline=False,
            )
        if failed:
            embed.add_field(
                name=f"❌ 失敗（{len(failed)}件）",
                value="\n".join(f"`{u}`：{err}" for u, err in failed)[:1024],
                inline=False,
            )
        embed.set_footer(text=f"実行者: {interaction.user}")
        await interaction.followup.send(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # ------------------------------------------------------------------ #
    # BAN解除
    # ------------------------------------------------------------------ #
    @discord.app_commands.command(
        name="preban_remove", description="管理者: ユーザーIDを指定してBANを解除します"
    )
    @discord.app_commands.describe(user_ids="ユーザーID（スペース・カンマ・改行区切りで複数指定可）")
    async def preban_remove(self, interaction: discord.Interaction, user_ids: str):
        if not await self._guard(interaction):
            return
        ids = self._parse_ids(user_ids)
        if not ids:
            await interaction.response.send_message(
                "❌ 有効なユーザーIDが見つかりませんでした。", ephemeral=True
            )
            return
        if len(ids) > MAX_IDS:
            await interaction.response.send_message(
                f"❌ 一度に指定できるのは{MAX_IDS}件までです（{len(ids)}件指定されています）。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        unbanned, missing, failed = [], [], []
        for uid in ids:
            try:
                await guild.unban(
                    discord.Object(id=uid), reason=f"BAN解除（実行者: {interaction.user}）"
                )
                unbanned.append(uid)
                logger.info(f"BAN解除: {uid}（実行者: {interaction.user.id}）")
            except discord.NotFound:
                missing.append(uid)
            except discord.Forbidden:
                failed.append((uid, "Botに権限がありません"))
            except discord.HTTPException as e:
                failed.append((uid, str(e)))

        lines = []
        if unbanned:
            lines.append(f"✅ 解除しました（{len(unbanned)}件）：" + " ".join(f"`{u}`" for u in unbanned))
        if missing:
            lines.append(f"⚠️ BANされていません（{len(missing)}件）：" + " ".join(f"`{u}`" for u in missing))
        if failed:
            lines.append(
                f"❌ 失敗（{len(failed)}件）：" + " ".join(f"`{u}`({err})" for u, err in failed)
            )
        await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)

    # ------------------------------------------------------------------ #
    # BAN一覧
    # ------------------------------------------------------------------ #
    @discord.app_commands.command(
        name="preban_list", description="管理者: BANされているユーザーを一覧表示します"
    )
    @discord.app_commands.describe(limit="表示件数（既定: 50、最大: 200）")
    async def preban_list(self, interaction: discord.Interaction, limit: int = 50):
        if not await self._guard(interaction):
            return
        limit = max(1, min(limit, 200))
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            entries = [entry async for entry in interaction.guild.bans(limit=limit)]
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ BAN一覧の取得に失敗しました（BotにBAN権限がありません）。", ephemeral=True
            )
            return
        if not entries:
            await interaction.followup.send("BANされているユーザーはいません。", ephemeral=True)
            return
        lines = [f"- `{e.user.id}` {e.user}（{e.reason or '理由なし'}）" for e in entries]
        text = f"**BAN一覧（{len(entries)}件）**\n" + "\n".join(lines)
        await interaction.followup.send(
            text[:2000], ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PreBan(bot))
