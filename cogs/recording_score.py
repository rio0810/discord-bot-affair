import discord
from discord.ext import commands
import os
import json

from core.db_base import DatabaseBase
from ui.recording_score import (
    ScoreButton,
    SCORE_REVIEWER_COUNT,
    PASS_THRESHOLD,
    categories_for,
    forward_recording,
    is_audio,
)


class RecordingScore(commands.Cog, DatabaseBase):
    """提出された録音を4項目（各0〜2）で採点し、規定人数が採点したら平均を出して
    管理者にメンションする。

    男性は「録音の投稿」と「プロフィール作成」が両方揃った時点で、
    プロフィール＋音声＋採点パネルを運営チャンネルへ転送する（順序不問）。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID", "0"))
        self.forward_channel_id = int(os.getenv("RECORDING_FORWARD_CHANNEL_ID") or "0")
        # 審査の送信先フォーラム（設定時はユーザー名で新規ポストを作成）
        # 男女で分けたい場合は MALE/FEMALE を設定。共通で使う場合は RECORDING_FORUM_CHANNEL_ID
        self.forum_channel_id = int(os.getenv("RECORDING_FORUM_CHANNEL_ID") or "0")
        self.forum_male_id = int(os.getenv("RECORDING_FORUM_MALE_ID") or "0")
        self.forum_female_id = int(os.getenv("RECORDING_FORUM_FEMALE_ID") or "0")

    async def cog_load(self):
        self._ensure_tables()
        self.bot.add_dynamic_items(ScoreButton)

    def _forward_channel(self, kind: str = "m"):
        # 種別に応じたフォーラムを優先。無ければ共通フォーラム、最後にテキストチャンネル。
        forum_id = self.forum_female_id if kind == "f" else self.forum_male_id
        for fid in (forum_id, self.forum_channel_id):
            if fid:
                ch = self.bot.get_channel(fid)
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
            await forward_recording(
                fch, interaction.user, audio, embed=embed,
                source_channel=interaction.channel, kind="m",
            )
            self._mark_done(interaction.user.id)
        else:
            self._store_pending(interaction.user.id, embed)
            try:
                await interaction.channel.send(
                    "🎙️ プロフィールを受け付けました。**音声ファイル、または Discordの録音機能**で"
                    "録音をこのチャンネルに投稿すると、運営の審査に回ります。"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def on_profile_only(self, interaction: discord.Interaction, embed: discord.Embed):
        """女性など音声不要の審査。プロフィールのみで即座に審査へ送る。"""
        fch = self._forward_channel("f")
        if fch is None:
            return
        await forward_recording(
            fch, interaction.user, [], embed=embed, source_channel=interaction.channel, kind="f",
        )

    async def on_interview_audio(self, message: discord.Message, audio_attachments: list):
        """面接チャンネルに録音が投稿されたとき呼ぶ。プロフィールが揃っていれば転送。"""
        pending = self._pop_pending(message.author.id)
        if pending is None:
            if self._is_done(message.author.id):
                return  # 既に審査へ回済み → 催促しない
            # プロフィール未作成 → 録音は受け付けつつ、プロフィール作成を催促
            try:
                await message.channel.send(
                    f"{message.author.mention} 🎤 録音を受け付けました！\n"
                    "続いて **「📝 プロフィールを作成する」** ボタンからプロフィールを作成してください。\n"
                    "（プロフィールの作成が完了すると、運営の審査に回ります）"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return  # プロフィール作成時に録音を拾って審査へ回す
        fch = self._forward_channel("m")
        if fch is None:
            return
        embed = discord.Embed.from_dict(pending)
        await forward_recording(
            fch, message.author, audio_attachments, embed=embed,
            source_channel=message.channel, kind="m",
        )
        self._mark_done(message.author.id)
        try:
            await message.channel.send("✅ 録音を受け付けました。運営の審査に回りました。")
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

    def _store_pending(self, user_id: int, embed: discord.Embed):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO pending_interview (user_id, embed_json) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET embed_json = EXCLUDED.embed_json",
                        (user_id, json.dumps(embed.to_dict())),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 待機プロフィールの保存に失敗しました: {e}")

    def _mark_done(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO interview_done (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                        (user_id,),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 審査済みフラグの記録に失敗しました: {e}")

    def _is_done(self, user_id: int) -> bool:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM interview_done WHERE user_id = %s", (user_id,))
                    return cur.fetchone() is not None
        except Exception as e:
            print(f"[ERROR] 審査済みフラグの取得に失敗しました: {e}")
            return False

    def _pop_pending(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT embed_json FROM pending_interview WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    cur.execute("DELETE FROM pending_interview WHERE user_id = %s", (user_id,))
                    conn.commit()
                    return json.loads(row[0])
        except Exception as e:
            print(f"[ERROR] 待機プロフィールの取得に失敗しました: {e}")
            return None

    def _ensure_tables(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS recording_scores (
                            message_id BIGINT NOT NULL,
                            reviewer_id BIGINT NOT NULL,
                            submitter_id BIGINT NOT NULL,
                            s_profile INT NOT NULL,
                            s_voice INT NOT NULL,
                            s_talk INT NOT NULL,
                            s_character INT NOT NULL,
                            reason TEXT,
                            PRIMARY KEY (message_id, reviewer_id)
                        )
                    """)
                    cur.execute("ALTER TABLE recording_scores ADD COLUMN IF NOT EXISTS reason TEXT")
                    cur.execute("ALTER TABLE recording_scores ADD COLUMN IF NOT EXISTS kind CHAR(1) DEFAULT 'm'")
                    # 女性は voice/talk を採点しないため NULL 許可にする
                    cur.execute("ALTER TABLE recording_scores ALTER COLUMN s_voice DROP NOT NULL")
                    cur.execute("ALTER TABLE recording_scores ALTER COLUMN s_talk DROP NOT NULL")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS recording_results (
                            message_id BIGINT PRIMARY KEY
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS pending_interview (
                            user_id BIGINT PRIMARY KEY,
                            embed_json TEXT NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS interview_done (
                            user_id BIGINT PRIMARY KEY
                        )
                    """)
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] recording_scores テーブルの作成に失敗しました: {e}")

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
            print(f"[ERROR] 採点の記録に失敗しました: {e}")
            await interaction.response.send_message("❌ 採点の記録に失敗しました。", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ 採点を受け付けました（{count}/{SCORE_REVIEWER_COUNT}人）。", ephemeral=True
        )

        if count >= SCORE_REVIEWER_COUNT and self._claim_result(message_id):
            await self._post_result(interaction, message_id, submitter_id)

    def _claim_result(self, message_id: int) -> bool:
        """結果出力の権利を取る（複数回出力しないよう最初の1回だけ True）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO recording_results (message_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING RETURNING message_id",
                        (message_id,),
                    )
                    claimed = cur.fetchone() is not None
                    conn.commit()
                    return claimed
        except Exception as e:
            print(f"[ERROR] 結果権利の取得に失敗しました: {e}")
            return False

    async def _post_result(self, interaction: discord.Interaction, message_id: int, submitter_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT reviewer_id, s_profile, s_voice, s_talk, s_character, reason, kind "
                        "FROM recording_scores WHERE message_id = %s", (message_id,)
                    )
                    rows = cur.fetchall()
        except Exception as e:
            print(f"[ERROR] 採点集計の取得に失敗しました: {e}")
            return
        if not rows:
            return

        guild = interaction.guild
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
        view.add_item(container)

        # 審査結果はフォーラムの審査ポスト（スレッド）内にだけ出す。テキストチャンネルには出さない。
        if not isinstance(interaction.channel, discord.Thread):
            return

        allowed = discord.AllowedMentions(roles=[admin_role]) if admin_role else discord.AllowedMentions.none()
        try:
            await interaction.channel.send(view=view, allowed_mentions=allowed)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 採点結果の送信に失敗しました: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RecordingScore(bot))
