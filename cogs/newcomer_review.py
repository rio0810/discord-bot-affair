import json
import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.db_base import DatabaseBase

logger = logging.getLogger(__name__)

# サーバー加入から審査（1週間レビュー）までの経過時間
REVIEW_AFTER = timedelta(days=7)


# ---------------------------------------------------------------------- #
# 判定（判定ボタン → RadioGroup モーダル。プロフ審査の合否と同じ方式）
# ---------------------------------------------------------------------- #
class NewcomerVerdictModal(discord.ui.Modal, title="新人の判定"):
    def __init__(self, uid: int):
        super().__init__()
        self.uid = uid
        self.verdict = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(label="✅ メンバーにする", value="promote"),
                discord.RadioGroupOption(label="🔨 BANする", value="ban"),
            ],
            required=True,
        )
        self.add_item(discord.ui.Label(text="判定を選択してください", component=self.verdict))

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("NewcomerReview")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        await cog.handle_verdict(interaction, self.uid, self.verdict.value)


class NewcomerVerdictButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"nc_verdict:(?P<uid>[0-9]+)",
):
    def __init__(self, uid: int):
        self.uid = uid
        super().__init__(
            discord.ui.Button(
                label="判定する", style=discord.ButtonStyle.blurple, emoji="⚖️",
                custom_id=f"nc_verdict:{uid}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NewcomerVerdictModal(self.uid))


def make_review_view(uid: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(NewcomerVerdictButton(uid))
    return view


class NewcomerReview(commands.Cog, DatabaseBase):
    """新人ロール保持者が「サーバー加入から1週間」経過したら、保存済みプロフィールと
    一緒に指定フォーラムへ投稿し、メンバー昇格 / BAN を判定できるようにする。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.newcomer_role_id = int(os.getenv("NEWCOMER_ROLE_ID") or "0")
        # 昇格時に付与するメンバーロール
        self.member_role_id = int(os.getenv("MEMBER_ROLE_ID") or "0")
        # 1週間レビューの投稿先フォーラム
        self.forum_id = int(os.getenv("NEWCOMER_REVIEW_FORUM_ID") or "0")
        # 判定結果のメンション用（任意）
        self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID") or "0")

    async def cog_load(self):
        self._ensure_tables()
        self.bot.add_dynamic_items(NewcomerVerdictButton)
        self.review_loop.start()

    async def cog_unload(self):
        self.review_loop.cancel()

    def is_admin(self, member: discord.Member) -> bool:
        if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
            return True
        role = member.guild.get_role(self.admin_role_id) if self.admin_role_id else None
        return role is not None and role in member.roles

    @app_commands.command(
        name="newcomer_review_now",
        description="管理者: 指定メンバーの1週間レビューを今すぐ投稿します（テスト・手動用）",
    )
    @app_commands.describe(member="レビューを投稿するメンバー")
    async def newcomer_review_now(self, interaction: discord.Interaction, member: discord.Member):
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        if self._forum() is None:
            await interaction.response.send_message(
                "❌ 投稿先フォーラム（NEWCOMER_REVIEW_FORUM_ID）が未設定か、フォーラムチャンネルではありません。",
                ephemeral=True,
            )
            return
        # DB操作＋スレッド作成で3秒を超えることがあるので先に defer する
        await interaction.response.defer(ephemeral=True)
        # 再テストできるよう、投稿済み・判定済みフラグをリセットしてから投稿する
        self._unclaim_review(member.id)
        self._unclaim_verdict(member.id)
        # 投稿権を取ってから投稿（ループと同じ扱いにして二重投稿を防ぐ）
        self._claim_review(member.id)
        await self._post_review(interaction.guild, member)
        await interaction.followup.send(
            f"✅ {member.mention} の1週間レビューを投稿しました（テスト・7日経過やプロフ有無は問いません）。",
            ephemeral=True,
        )

    def _ensure_tables(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    # 作成時に保存するプロフィール embed（1週間後の再掲用）
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS member_profiles (
                            user_id BIGINT PRIMARY KEY,
                            embed_json TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    # 1週間レビューを投稿済みの人（二重投稿防止）
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS newcomer_reviews (
                            user_id BIGINT PRIMARY KEY,
                            posted_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    # 判定済みの人（二重判定防止）
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS newcomer_verdicts (
                            user_id BIGINT PRIMARY KEY
                        )
                    """)
                    conn.commit()
        except Exception as e:
            logger.error(f"newcomer_review テーブルの作成に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # プロフィールの保存（プロフィールウィザードから呼ばれる）
    # ------------------------------------------------------------------ #
    def store_profile(self, user_id: int, embed: discord.Embed):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO member_profiles (user_id, embed_json) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET embed_json = EXCLUDED.embed_json, created_at = now()",
                        (user_id, json.dumps(embed.to_dict())),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"プロフィールの保存に失敗しました: {e}")

    def _get_profile(self, user_id: int) -> dict | None:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT embed_json FROM member_profiles WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    return json.loads(row[0]) if row else None
        except Exception as e:
            logger.error(f"プロフィールの取得に失敗しました: {e}")
            return None

    def _claim_review(self, user_id: int) -> bool:
        """1週間レビューの投稿権を取る（最初の1回だけ True）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO newcomer_reviews (user_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING RETURNING user_id",
                        (user_id,),
                    )
                    claimed = cur.fetchone() is not None
                    conn.commit()
                    return claimed
        except Exception as e:
            logger.error(f"レビュー投稿権の取得に失敗しました: {e}")
            return False

    def _unclaim_review(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM newcomer_reviews WHERE user_id = %s", (user_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"レビュー投稿権の解除に失敗しました: {e}")

    def _claim_verdict(self, user_id: int) -> bool:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO newcomer_verdicts (user_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING RETURNING user_id",
                        (user_id,),
                    )
                    claimed = cur.fetchone() is not None
                    conn.commit()
                    return claimed
        except Exception as e:
            logger.error(f"判定フラグの取得に失敗しました: {e}")
            return False

    def _unclaim_verdict(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM newcomer_verdicts WHERE user_id = %s", (user_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"判定フラグの解除に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 1週間経過チェック（1時間ごと）
    # ------------------------------------------------------------------ #
    @tasks.loop(hours=1)
    async def review_loop(self):
        if not self.newcomer_role_id or not self.forum_id:
            return
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            role = guild.get_role(self.newcomer_role_id)
            if role is None:
                continue
            for member in list(role.members):
                if member.bot or member.joined_at is None:
                    continue
                if now - member.joined_at < REVIEW_AFTER:
                    continue
                # 投稿権を取れた最初の1回だけ投稿（再起動・二重実行に強い）
                if not self._claim_review(member.id):
                    continue
                await self._post_review(guild, member)

    @review_loop.before_loop
    async def before_review_loop(self):
        await self.bot.wait_until_ready()

    def _forum(self):
        ch = self.bot.get_channel(self.forum_id) if self.forum_id else None
        return ch if isinstance(ch, discord.ForumChannel) else None

    def _review_embed(self, member: discord.Member) -> discord.Embed:
        """レビュー投稿の内容を1つの embed にまとめる（案内文＋プロフィール）。"""
        stored = self._get_profile(member.id)
        profile_text = ""
        if stored:
            profile_text = stored.get("description") or ""
        else:
            profile_text = "（プロフィールが保存されていません）"

        embed = discord.Embed(
            title="⏳ 新人審査",
            description=(
                f"{member.mention} さんがサーバー加入から **1週間** 経過しました。\n"
                "下の **「⚖️ 判定する」** ボタンから、**メンバーへの昇格** または **BAN** を選んでください。"
            ),
            color=discord.Color.orange(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="対象メンバー", value=member.mention, inline=True)
        if member.joined_at is not None:
            embed.add_field(name="加入日", value=discord.utils.format_dt(member.joined_at, "D"), inline=True)
        embed.add_field(name="📋 プロフィール", value=profile_text[:1024] or "（なし）", inline=False)
        return embed

    async def _post_review(self, guild: discord.Guild, member: discord.Member):
        forum = self._forum()
        if forum is None:
            logger.warning(f"新人レビューのフォーラム {self.forum_id} が見つかりません。")
            self._unclaim_review(member.id)  # フォーラム未設定なら次回に持ち越す
            return
        embed = self._review_embed(member)
        view = make_review_view(member.id)
        try:
            await forum.create_thread(
                name=member.display_name[:100],
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"新人レビューの投稿に失敗しました ({member.id}): {e}")
            self._unclaim_review(member.id)  # 失敗時は再投稿できるよう権利を戻す

    async def _send_promotion_dm(self, member: discord.Member) -> bool:
        """正式メンバー昇格を本人へDMで連絡する。送信できたら True。"""
        embed = discord.Embed(
            title="🎉 正式メンバーになりました！",
            description=(
                f"正式メンバーになりました。おめでとうございます🎊\n\n"
                "これから、次の機能が使えるようになりました：\n"
                "🎫 **MPチケット** … VCレベルアップで貯めて、ショップで各種アイテムと交換できます\n"
                "📞 **個通部屋（1対1通話）** … 気になる相手を誘って、2人だけの通話部屋を作れます\n\n"
            ),
            color=discord.Color.green(),
        )
        try:
            await member.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ------------------------------------------------------------------ #
    # 判定（メンバー昇格 / BAN）
    # ------------------------------------------------------------------ #
    async def handle_verdict(self, interaction: discord.Interaction, uid: int, action: str):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ サーバー内でのみ操作できます。", ephemeral=True)
            return
        # 二重判定を防ぐ（最初の1回だけ通す）
        if not self._claim_verdict(uid):
            await interaction.response.send_message("この人の判定は既に処理済みです。", ephemeral=True)
            return

        # ロール変更・BAN・DM で3秒を超えることがあるので先に defer する
        await interaction.response.defer()

        if action == "promote":
            member = guild.get_member(uid)
            if member is None:
                self._unclaim_verdict(uid)
                await interaction.followup.send(
                    "❌ 対象者がサーバーにいないため、ロールを変更できませんでした。", ephemeral=True
                )
                return
            newcomer_role = guild.get_role(self.newcomer_role_id) if self.newcomer_role_id else None
            member_role = guild.get_role(self.member_role_id) if self.member_role_id else None
            try:
                if newcomer_role is not None and newcomer_role in member.roles:
                    await member.remove_roles(newcomer_role, reason="1週間レビュー：メンバー昇格")
                if member_role is not None and member_role not in member.roles:
                    await member.add_roles(member_role, reason="1週間レビュー：メンバー昇格")
            except discord.Forbidden:
                self._unclaim_verdict(uid)
                await interaction.followup.send(
                    "❌ ロールの変更に失敗しました（Botの権限・ロール順を確認してください）。", ephemeral=True
                )
                return
            # 正式メンバーになったことを本人にDMで連絡（DM不可なら黙ってスキップ）
            dm_sent = await self._send_promotion_dm(member)
            note = "" if dm_sent else "\n（※本人へのDM送信はできませんでした）"
            await interaction.followup.send(
                f"✅ {member.mention} を **メンバー** にしました。（新人ロール解除・メンバーロール付与）{note}"
            )
        else:  # ban
            try:
                await guild.ban(
                    discord.Object(id=uid),
                    reason=f"1週間レビュー BAN（実行者: {interaction.user}）",
                )
            except discord.Forbidden:
                self._unclaim_verdict(uid)
                await interaction.followup.send(
                    "❌ BANに失敗しました（Botのban権限・ロール順を確認してください）。", ephemeral=True
                )
                return
            except discord.HTTPException as e:
                self._unclaim_verdict(uid)
                await interaction.followup.send(f"❌ BANに失敗しました：{e}", ephemeral=True)
                return
            await interaction.followup.send(f"🔨 <@{uid}> を **BAN** しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(NewcomerReview(bot))
