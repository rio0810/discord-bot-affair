import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from core import config

from .db import RecordingDBMixin
from .embeds import profile_received_embed, recording_received_embed, submitted_embed
from .ui import (
    PASS_THRESHOLD,
    AudioConfirmView,
    ScoreButton,
    ScoreStatusButton,
    VerdictButton,
    categories_for,
    forward_recording,
    is_audio,
)

logger = logging.getLogger(__name__)

# 提出から未採点者へメンションするまでの経過時間
REMIND_AFTER = timedelta(hours=12)
# 提出から強制的に結果を出すまでの経過時間（案内する24時間期限の3時間前）
FORCE_AFTER = timedelta(hours=21)
# 未提出者の自動BANで対象にする参加からの最大経過時間。
# これより古いメンバーは対象外にして、機能を有効化した直後の一括BAN事故を防ぐ。
UNSUBMITTED_MAX_AGE = timedelta(days=7)


class RecordingScore(commands.Cog, RecordingDBMixin):
    """提出された録音を4項目（各0〜2）で採点し、規定人数が採点したら平均を出して
    管理者にメンションする。

    男性は「録音の投稿」と「プロフィール作成」が両方揃った時点で、
    プロフィール＋音声＋採点パネルを運営チャンネルへ転送する（順序不問）。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.admin_role_id = config.ADMIN_ROLE_ID
        self.forward_channel_id = config.RECORDING_FORWARD_CHANNEL_ID
        # 審査の送信先フォーラム（設定時はユーザー名で新規ポストを作成・男女別）
        self.forum_male_id = config.RECORDING_FORUM_MALE_ID
        self.forum_female_id = config.RECORDING_FORUM_FEMALE_ID
        # 合否判定で操作するロール（審査ロールは未設定なら待機ロールを使う）
        self.review_role_id = config.REVIEW_ROLE_ID
        self.newcomer_role_id = config.NEWCOMER_ROLE_ID
        # 性別判定用ロール
        self.male_role_id = config.MALE_ROLE_ID
        self.female_role_id = config.FEMALE_ROLE_ID
        # 合格後にプロフィールを書いてもらう性別別チャンネル
        self.male_profile_channel_id = config.MALE_PROFILE_CHANNEL_ID
        self.female_profile_channel_id = config.FEMALE_PROFILE_CHANNEL_ID
        # 雑談ロール保持者は性別別ではなく雑談ユーザー専用チャンネルへ案内する
        self.zero_romance_role_id = config.ZERO_ROMANCE_ROLE_ID
        self.zero_romance_profile_channel_id = config.ZERO_ROMANCE_PROFILE_CHANNEL_ID
        # 合格案内で確認してもらうガイドラインチャンネル
        self.guideline_channel_id = config.GUIDELINE_CHANNEL_ID
        # 審査未提出のまま参加から一定時間が経ったメンバーの自動BAN（未設定・0なら無効）
        self.unsubmitted_ban_hours = config.UNSUBMITTED_BAN_HOURS

    async def cog_load(self):
        self._ensure_tables()
        self.bot.add_dynamic_items(ScoreButton)
        self.bot.add_dynamic_items(ScoreStatusButton)
        self.bot.add_dynamic_items(VerdictButton)
        self.review_deadline_loop.start()
        if self.unsubmitted_ban_hours > 0 and self.review_role_id:
            self.unsubmitted_ban_loop.start()

    async def cog_unload(self):
        self.review_deadline_loop.cancel()
        self.unsubmitted_ban_loop.cancel()

    def is_admin(self, member: discord.Member) -> bool:
        if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
            return True
        role = member.guild.get_role(self.admin_role_id) if self.admin_role_id else None
        return role is not None and role in member.roles

    # ------------------------------------------------------------------ #
    # 審査メンバー（採点者）の登録・管理コマンド
    # ------------------------------------------------------------------ #
    @discord.app_commands.command(
        name="reviewer_add", description="管理者: プロフ審査を担当するメンバーを登録します"
    )
    @discord.app_commands.describe(member="審査メンバーに登録するメンバー")
    async def reviewer_add(self, interaction: discord.Interaction, member: discord.Member):
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        if self._add_reviewer(member.id):
            count = self._reviewer_count()
            await interaction.response.send_message(
                f"✅ {member.mention} を審査メンバーに登録しました。（現在 {count}人）", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"ℹ️ {member.mention} は既に審査メンバーです。", ephemeral=True
            )

    @discord.app_commands.command(
        name="reviewer_remove", description="管理者: プロフ審査メンバーの登録を解除します"
    )
    @discord.app_commands.describe(member="登録を解除するメンバー")
    async def reviewer_remove(self, interaction: discord.Interaction, member: discord.Member):
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        if self._remove_reviewer(member.id):
            count = self._reviewer_count()
            await interaction.response.send_message(
                f"✅ {member.mention} の審査メンバー登録を解除しました。（現在 {count}人）", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"ℹ️ {member.mention} は審査メンバーに登録されていません。", ephemeral=True
            )

    @discord.app_commands.command(
        name="reviewer_list", description="管理者: 登録済みのプロフ審査メンバーを一覧表示します"
    )
    async def reviewer_list(self, interaction: discord.Interaction):
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ このコマンドは管理者のみ使用できます。", ephemeral=True)
            return
        ids = self._list_reviewers()
        if not ids:
            await interaction.response.send_message(
                "審査メンバーはまだ登録されていません。", ephemeral=True
            )
            return
        lines = [f"- <@{uid}>" for uid in ids]
        await interaction.response.send_message(
            f"**審査メンバー（{len(ids)}人）**\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ------------------------------------------------------------------ #
    # 合否判定
    # ------------------------------------------------------------------ #
    async def apply_verdict(self, interaction: discord.Interaction, submitter_id: int, verdict: str):
        guild = interaction.guild
        # ロール変更・BAN・案内送信で3秒を超えることがあるため、先に応答を保留する
        if not interaction.response.is_done():
            await interaction.response.defer()
        # 二重判定を防ぐ（最初の1回だけ通す）
        if not self._claim_verdict(submitter_id):
            await interaction.followup.send(
                "この人の合否は既に処理済みです。", ephemeral=True
            )
            return

        if verdict == "pass":
            member = guild.get_member(submitter_id)
            if member is None:
                self._unclaim_verdict(submitter_id)
                await interaction.followup.send(
                    "❌ 対象者がサーバーにいないため、ロールを変更できませんでした。", ephemeral=True
                )
                return
            review_role = guild.get_role(self.review_role_id) if self.review_role_id else None
            newcomer_role = guild.get_role(self.newcomer_role_id) if self.newcomer_role_id else None
            try:
                if review_role is not None and review_role in member.roles:
                    await member.remove_roles(review_role, reason="審査合格：審査ロール解除")
                if newcomer_role is not None and newcomer_role not in member.roles:
                    await member.add_roles(newcomer_role, reason="審査合格：新人ロール付与")
            except discord.Forbidden:
                self._unclaim_verdict(submitter_id)
                await interaction.followup.send(
                    "❌ ロールの変更に失敗しました（Botの権限・ロール順を確認してください）。", ephemeral=True
                )
                return
            # 合格者本人のチャンネルへ、性別ごとのプロフィールチャンネルへの案内を送る
            await self._notify_profile_channel(guild, member)
            await interaction.followup.send(
                f"✅ {member.mention} を **合格** にしました。（審査ロール解除・新人ロール付与）\n"
                f"**判定者：**{interaction.user.mention}"
            )
        else:  # fail → BAN
            try:
                await guild.ban(
                    discord.Object(id=submitter_id),
                    reason=f"審査 不合格（判定者: {interaction.user}）",
                )
            except discord.Forbidden:
                self._unclaim_verdict(submitter_id)
                await interaction.followup.send(
                    "❌ BANに失敗しました（Botのban権限・ロール順を確認してください）。", ephemeral=True
                )
                return
            except discord.HTTPException as e:
                self._unclaim_verdict(submitter_id)
                await interaction.followup.send(f"❌ BANに失敗しました：{e}", ephemeral=True)
                return
            await interaction.followup.send(
                f"🔨 <@{submitter_id}> を **不合格** としてBANしました。\n"
                f"**判定者：**{interaction.user.mention}"
            )

    async def _notify_profile_channel(self, guild: discord.Guild, member: discord.Member):
        """合格者に、性別ごとのプロフィールチャンネルへの記入を案内する。
        本人の面接・プロフ用チャンネルへ送る（無ければ送らない。DMには送らない）。"""
        # 雑談ロール保持者は性別を問わず雑談ユーザー専用チャンネルへ案内
        if self.zero_romance_role_id and guild.get_role(self.zero_romance_role_id) in member.roles:
            channel_id = self.zero_romance_profile_channel_id
        # 性別に応じた投稿先プロフィールチャンネルを決定
        elif self.female_role_id and guild.get_role(self.female_role_id) in member.roles:
            channel_id = self.female_profile_channel_id
        elif self.male_role_id and guild.get_role(self.male_role_id) in member.roles:
            channel_id = self.male_profile_channel_id
        else:
            channel_id = 0
        profile_channel = guild.get_channel(channel_id) if channel_id else None
        guideline = guild.get_channel(self.guideline_channel_id) if self.guideline_channel_id else None

        where = f"{profile_channel.mention} に" if profile_channel is not None else ""
        Sep = discord.ui.Separator
        large = discord.SeparatorSpacing.large
        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(accent_colour=discord.Colour.green())
        container.add_item(discord.ui.TextDisplay("## 🎉 審査に合格しました！"))
        container.add_item(Sep(spacing=large))
        container.add_item(discord.ui.TextDisplay(
            f"{member.mention} おめでとうございます🎉\n"
            f"次は {where}あなたのプロフィールを記入してください。"
        ))
        if guideline is not None:
            container.add_item(Sep(spacing=large))
            container.add_item(discord.ui.TextDisplay(
                f"📖 サーバーについては {guideline.mention} を確認するようにお願いします。"
            ))
        view.add_item(container)

        allowed = discord.AllowedMentions(users=[member])
        # 送信先：本人の面接・プロフ用チャンネル（topic で判定）。DMには送らない
        targets = {f"interview_room:{member.id}", f"profile_room:{member.id}"}
        personal = discord.utils.find(lambda c: c.topic in targets, guild.text_channels)
        if personal is None:
            return
        try:
            await personal.send(view=view, allowed_mentions=allowed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"合格案内の送信に失敗しました ({member.id}): {e}")

    def _forward_channel(self, kind: str = "m"):
        # 種別に応じた男女別フォーラムを優先。無ければテキストチャンネル。
        forum_id = self.forum_female_id if kind == "f" else self.forum_male_id
        if forum_id:
            ch = self.bot.get_channel(forum_id)
            if isinstance(ch, discord.ForumChannel):
                return ch
        ch = self.bot.get_channel(self.forward_channel_id) if self.forward_channel_id else None
        return ch if isinstance(ch, (discord.TextChannel, discord.Thread)) else None

    # ------------------------------------------------------------------ #
    # 録音とプロフィールの待ち合わせ
    # ------------------------------------------------------------------ #
    async def on_profile_created(self, interaction: discord.Interaction, embed: discord.Embed):
        """男性のプロフィール作成時に呼ぶ。録音が既にあれば転送、無ければ待機登録。"""
        audio = await self._find_recent_audio(interaction.channel, interaction.user.id)
        fch = self._forward_channel("m")
        if fch is None:
            return
        if audio:
            result = await forward_recording(
                fch, interaction.user, audio, embed=embed,
                source_channel=interaction.channel, kind="m",
            )
            self._register_review(result, interaction.user.id, "m")
            self._mark_done(interaction.user.id)
            try:
                await interaction.channel.send(embed=submitted_embed())
            except (discord.Forbidden, discord.HTTPException):
                pass
        else:
            self._store_pending(interaction.user.id, embed)
            try:
                await interaction.channel.send(embed=profile_received_embed())
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def on_profile_only(
        self, interaction: discord.Interaction, embed: discord.Embed, kind: str = "f"
    ):
        """音声不要の審査。プロフィールのみで即座に審査へ送る。

        kind は投稿先フォーラムの振り分け（"m"=男性 / "f"=女性）。女性のほか、
        プロフ審査のみの男性ルートからも呼ばれる。"""
        fch = self._forward_channel(kind)
        if fch is None:
            return
        result = await forward_recording(
            fch, interaction.user, [], embed=embed, source_channel=interaction.channel, kind=kind,
        )
        self._register_review(result, interaction.user.id, kind)
        self._mark_done(interaction.user.id)
        try:
            await interaction.channel.send(embed=submitted_embed())
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def confirm_interview_audio(self, message: discord.Message, audio_attachments: list):
        """面接チャンネルに録音が投稿されたとき、まず本人に提出確認を出す。
        「提出する」を押されたら on_interview_audio へ進む。"""
        # 既に審査へ回済みの人には確認も催促も出さない
        if self._is_done(message.author.id):
            return
        try:
            await message.reply(
                "🎧 この音声を提出しますか？内容をご確認のうえ、下のボタンで選んでください。",
                view=AudioConfirmView(message, audio_attachments),
                mention_author=False,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def on_interview_audio(self, message: discord.Message, audio_attachments: list):
        """面接チャンネルに録音が投稿されたとき呼ぶ。プロフィールが揃っていれば転送。"""
        pending = self._pop_pending(message.author.id)
        if pending is None:
            if self._is_done(message.author.id):
                return  # 既に審査へ回済み → 催促しない
            # プロフィール未作成 → 録音は受け付けつつ、プロフィール作成を催促
            try:
                await message.channel.send(
                    content=message.author.mention,
                    embed=recording_received_embed(),
                    allowed_mentions=discord.AllowedMentions(users=[message.author]),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return  # プロフィール作成時に録音を拾って審査へ回す
        fch = self._forward_channel("m")
        if fch is None:
            return
        embed = discord.Embed.from_dict(pending)
        result = await forward_recording(
            fch, message.author, audio_attachments, embed=embed,
            source_channel=message.channel, kind="m",
        )
        self._register_review(result, message.author.id, "m")
        self._mark_done(message.author.id)
        try:
            await message.channel.send(embed=submitted_embed())
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _find_recent_audio(self, channel, user_id: int):
        try:
            async for msg in channel.history(limit=50):
                if msg.author.id == user_id:
                    auds = [a for a in msg.attachments if is_audio(a)]
                    if auds:
                        return auds
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    # ------------------------------------------------------------------ #
    # 採点の記録と集計
    # ------------------------------------------------------------------ #
    async def submit_score(
        self, interaction: discord.Interaction, message_id: int, submitter_id: int,
        scores: dict, reason: str = "", kind: str = "m",
    ):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO recording_scores "
                        "(message_id, reviewer_id, submitter_id, s_profile, s_voice, s_talk, s_character, reason, kind) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (message_id, reviewer_id) DO UPDATE SET "
                        "s_profile = EXCLUDED.s_profile, s_voice = EXCLUDED.s_voice, "
                        "s_talk = EXCLUDED.s_talk, s_character = EXCLUDED.s_character, "
                        "reason = EXCLUDED.reason, kind = EXCLUDED.kind",
                        (message_id, interaction.user.id, submitter_id,
                         scores.get("profile"), scores.get("voice"), scores.get("talk"),
                         scores.get("character"), reason or None, kind),
                    )
                    cur.execute(
                        "SELECT COUNT(*) FROM recording_scores WHERE message_id = %s", (message_id,)
                    )
                    count = cur.fetchone()[0]
                    conn.commit()
        except Exception as e:
            logger.error(f"採点の記録に失敗しました: {e}")
            await interaction.response.send_message("❌ 採点の記録に失敗しました。", ephemeral=True)
            return

        required = self._reviewer_count()
        await interaction.response.send_message(
            f"✅ 採点を受け付けました（{count}/{required}人）。", ephemeral=True
        )

        # 審査メンバーが登録されていない間は結果を出さない
        if required > 0 and count >= required and self._claim_result(message_id):
            await self._post_result(interaction.channel, message_id, submitter_id)
            self._delete_review(message_id)

    # ------------------------------------------------------------------ #
    # 審査の期限管理（未採点メンション・強制結果）
    # ------------------------------------------------------------------ #
    async def _resolve_channel(self, channel_id: int):
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    @tasks.loop(minutes=10)
    async def review_deadline_loop(self):
        rows = self._list_pending_reviews()
        if not rows:
            return
        now = datetime.now(timezone.utc)
        for message_id, channel_id, submitter_id, _kind, created_at, reminded in rows:
            age = now - created_at
            if age >= FORCE_AFTER:
                await self._force_result(message_id, channel_id, submitter_id)
            elif age >= REMIND_AFTER and not reminded:
                await self._remind_unscored(message_id, channel_id)

    @review_deadline_loop.before_loop
    async def before_review_deadline_loop(self):
        await self.bot.wait_until_ready()

    async def _remind_unscored(self, message_id: int, channel_id: int):
        """12時間経っても採点していない審査メンバーにメンションする。"""
        reviewers = self._list_reviewers()
        scored = self.scored_reviewer_ids(message_id)
        pending = [uid for uid in reviewers if uid not in scored]
        # 対象がいない（未登録 or 全員採点済み）なら再チェック不要にして終了
        if not pending:
            self._mark_reminded(message_id)
            return
        channel = await self._resolve_channel(channel_id)
        if channel is None:
            return  # 一時的に取得できないだけかもしれないので次回に持ち越す
        mentions = " ".join(f"<@{uid}>" for uid in pending)
        try:
            await channel.send(
                f"{mentions}\n"
                "⏰ 提出から12時間が経過しましたが、まだ採点が完了していません。\n"
                "審査期限（提出から24時間）まで残りわずかです。採点をお願いします🙏",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"未採点メンションの送信に失敗しました: {e}")
        self._mark_reminded(message_id)

    async def _force_result(self, message_id: int, channel_id: int, submitter_id: int):
        """審査期限（残り3時間）に達したら、集まった採点で強制的に結果を出す。"""
        # 既に通常フローで結果が出ていれば行を消して終了
        if not self._claim_result(message_id):
            self._delete_review(message_id)
            return
        channel = await self._resolve_channel(channel_id)
        scored = self.scored_reviewer_ids(message_id)
        if channel is not None:
            if scored:
                await self._post_result(channel, message_id, submitter_id, forced=True)
            elif isinstance(channel, discord.Thread):
                admin_role = channel.guild.get_role(self.admin_role_id) if self.admin_role_id else None
                mention = admin_role.mention if admin_role else ""
                allowed = (
                    discord.AllowedMentions(roles=[admin_role]) if admin_role
                    else discord.AllowedMentions.none()
                )
                try:
                    await channel.send(
                        f"{mention}\n⏰ 審査期限に達しましたが、採点が1件も集まりませんでした。",
                        allowed_mentions=allowed,
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.error(f"強制結果（採点なし）の送信に失敗しました: {e}")
        self._delete_review(message_id)

    async def _post_result(self, channel, message_id: int, submitter_id: int, forced: bool = False):
        # 審査結果はフォーラムの審査ポスト（スレッド）内にだけ出す。テキストチャンネルには出さない。
        if not isinstance(channel, discord.Thread):
            return
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT reviewer_id, s_profile, s_voice, s_talk, s_character, reason, kind "
                        "FROM recording_scores WHERE message_id = %s", (message_id,)
                    )
                    rows = cur.fetchall()
        except Exception as e:
            logger.error(f"採点集計の取得に失敗しました: {e}")
            return
        if not rows:
            return

        guild = channel.guild
        n = len(rows)
        kind = rows[0][6] or "m"
        cats = categories_for(kind)  # 種別に応じた採点項目
        col_of = {"profile": 1, "voice": 2, "talk": 3, "character": 4}

        # 項目別平均（該当項目のみ）
        avgs = [(label, sum(r[col_of[key]] for r in rows) / n) for key, label in cats]
        total_avg = sum(a for _, a in avgs)
        max_total = len(cats) * 2
        threshold = PASS_THRESHOLD / 8 * max_total  # 満点に対する比率で合格ラインを算出
        passed = total_avg >= threshold

        # 0点をつけた人：名前 + 対象項目 + 理由
        zero_lines = []
        for r in rows:
            reviewer_id, reason = r[0], r[5]
            zero_labels = [label for key, label in cats if r[col_of[key]] == 0]
            if not zero_labels:
                continue
            reviewer = guild.get_member(reviewer_id)
            who = reviewer.mention if reviewer else f"ID: {reviewer_id}"
            line = f"{who} … {'、'.join(zero_labels)}"
            if reason:
                line += f"\n> {reason}"
            zero_lines.append(line)

        submitter = guild.get_member(submitter_id)
        submitter_txt = submitter.mention if submitter else f"ID: {submitter_id}"

        result_line = "✅ **合格**" if passed else "❌ **不合格**"
        admin_role = guild.get_role(self.admin_role_id) if self.admin_role_id else None

        # Components V2 で Separator 区切りの見やすいパネルにする
        Sep = discord.ui.Separator
        large = discord.SeparatorSpacing.large
        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(
            accent_colour=discord.Color.green() if passed else discord.Color.red()
        )
        if admin_role is not None:
            container.add_item(discord.ui.TextDisplay(admin_role.mention))
        container.add_item(discord.ui.TextDisplay("## 📊 審査結果"))
        if forced:
            container.add_item(discord.ui.TextDisplay(
                "⏰ 審査期限に達したため、集まった採点で自動集計しました。"
            ))
        container.add_item(Sep(spacing=large))
        container.add_item(discord.ui.TextDisplay(
            f"**提出者：**{submitter_txt}\n"
            f"**採点人数：**{n}人\n"
            f"**判定：**{result_line}（合計 {total_avg:.2f} / {max_total}・合格ライン {threshold:.2f}点）"
        ))
        container.add_item(Sep(spacing=large))
        score_lines = "\n".join(f"- {label}：**{avg:.2f}** / 2" for label, avg in avgs)
        container.add_item(discord.ui.TextDisplay(
            f"### 項目別スコア\n{score_lines}\n\n**合計（平均）：{total_avg:.2f} / {max_total}**"
        ))
        if zero_lines:
            container.add_item(Sep(spacing=large))
            container.add_item(discord.ui.TextDisplay(
                "### ⚠️ 0点をつけた人\n" + "\n".join(zero_lines)
            ))
        # 合否を出すボタン
        container.add_item(Sep(spacing=large))
        row = discord.ui.ActionRow()
        row.add_item(VerdictButton(submitter_id))
        container.add_item(row)
        view.add_item(container)

        allowed = discord.AllowedMentions(roles=[admin_role]) if admin_role else discord.AllowedMentions.none()
        try:
            await channel.send(view=view, allowed_mentions=allowed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"採点結果の送信に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 未提出者の自動BAN
    # ------------------------------------------------------------------ #
    @tasks.loop(minutes=10)
    async def unsubmitted_ban_loop(self):
        """審査ロールを持ったまま、参加から規定時間が過ぎても未提出のメンバーをBANする。"""
        deadline = timedelta(hours=self.unsubmitted_ban_hours)
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            role = guild.get_role(self.review_role_id)
            if role is None:
                continue
            for member in list(role.members):
                if member.bot or member.joined_at is None:
                    continue
                age = now - member.joined_at
                # 期限前、または古すぎるメンバー（機能有効化前からの滞留）は対象外
                if age < deadline or age > UNSUBMITTED_MAX_AGE:
                    continue
                if self.is_admin(member) or self.is_reviewer(member.id):
                    continue
                if self._has_submitted(member.id):
                    continue
                await self._ban_unsubmitted(member, age)

    @unsubmitted_ban_loop.before_loop
    async def before_unsubmitted_ban_loop(self):
        await self.bot.wait_until_ready()

    async def _ban_unsubmitted(self, member: discord.Member, age: timedelta):
        hours = self.unsubmitted_ban_hours
        try:
            await member.ban(
                reason=f"審査未提出のまま参加から{hours:g}時間経過（自動BAN）",
                delete_message_seconds=0,
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"未提出者の自動BANに失敗しました ({member.id}): {e}")
            return
        logger.info(
            f"審査未提出のため自動BANしました: {member} ({member.id})"
            f" / 参加から {age.total_seconds() / 3600:.1f} 時間"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecordingScore(bot))
