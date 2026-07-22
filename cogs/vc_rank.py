import discord
from discord import app_commands
from discord.ext import commands, tasks
import psycopg2.extras
from datetime import datetime
import asyncio
import io
import math, os
from core.db_base import DatabaseBase
from ui.rank_card import render_rank_card

class VCRank(commands.Cog, DatabaseBase):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        # ユーザーID: 開始時間の辞書
        self.vc_start_times = {}
        # テキストレベルの計測クールダウン（ユーザーID: 最終カウント時刻）
        self.text_cooldown = {}
        # 加算率が1/3のカテゴリで生じる端数を持ち越す（ユーザーID: 未計上の分数）
        self.pending_minutes = {}

        # --- 除外チャンネル設定 ---
        env_excluded = os.getenv("EXCLUDED_CHANNEL_IDS")
        self.excluded_channel_ids = [int(i.strip()) for i in env_excluded.split(",")] if env_excluded else []

        # --- 加算率が1/3になるカテゴリ（カンマ区切りで複数可） ---
        env_reduced = os.getenv("VC_RANK_REDUCED_CATEGORY_IDS", "")
        self.reduced_category_ids = {int(i.strip()) for i in env_reduced.split(",") if i.strip().isdigit()}
        self.reduced_rate = 1 / 3

        # 定期更新タスクの開始
        self.update_vc_status.start()

        # 絵文字
        self.EMOJI_RANK = "<a:SuperBowlFootballStickerbyHoller:1474204834466103427>"
        self.EMOJI_TIME = "<a:SaleCountdownStickerbyprettylitt:1474213657515786241>"
        self.EMOJI_HEADER = "<a:StarGlowStickerbyMaraWearsStripe:1474207483114557583>"
        self.EMOJI_PROGRESS = "<a:CryptoCryptocurrencyStickerbyKra:1474215295089377471>"

    async def cog_load(self):
        # 既存DBにもテキスト用カラムが無ければ追加（init.sql は新規ボリューム時しか走らない）
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS text_count INT DEFAULT 0")
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS text_rank INTEGER DEFAULT 0")
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mp_tickets INT DEFAULT 0")
                    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS user_name TEXT")
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] users のスキーマ準備に失敗しました: {e}")

    def cog_unload(self):
        # Cogがリロードされた時にタスクを停止させる
        self.update_vc_status.cancel()

    # 1レベルに必要なVC時間（分）：5時間 = 300分ごとに1レベル
    MINUTES_PER_RANK = 300

    def get_required_minutes(self, rank):
        """ランク n に到達するために必要な累計VC時間（分）。5時間ごとに1レベル。"""
        if rank <= 0: return 0
        return rank * self.MINUTES_PER_RANK

    def get_required_messages(self, rank):
        """テキストレベル n に到達するために必要な累計メッセージ数。
        Lv1: 50 / Lv2: 142 / Lv3: 260 ..."""
        if rank <= 0: return 0
        return math.ceil(50 * math.pow(rank, 1.5))

    # --- 1分ごとに実行されるリアルタイム更新処理 ---
    @tasks.loop(minutes=1.0)
    async def update_vc_status(self):
        now = datetime.now()
        # 辞書のコピーを作成してループ（RuntimeError防止）
        for member_id in list(self.vc_start_times.keys()):
            # 全サーバーからメンバーを探す
            member = None
            for guild in self.bot.guilds:
                m = guild.get_member(member_id)
                if m and m.voice:
                    member = m
                    break
            
            if member and member.voice.channel.id not in self.excluded_channel_ids:
                # 1分以上の経過を確認
                join_time = self.vc_start_times.get(member_id)
                if not join_time: continue

                minutes = int((now - join_time).total_seconds() / 60)

                if minutes >= 1:
                    # カテゴリに応じた加算率で計上（端数は持ち越し）
                    await self._credit(member, member.voice.channel, minutes)
                    self.vc_start_times[member_id] = now
            else:
                # VCにいない場合は管理対象から外す
                self.vc_start_times.pop(member_id, None)

    @update_vc_status.before_loop
    async def before_update_vc_status(self):
        await self.bot.wait_until_ready()
        # 起動時点で既にVCにいるメンバーを計測対象に登録する。
        # これがないと、再起動時にVCへ入りっぱなしの人は入り直すまで時間が加算されない。
        now = datetime.now()
        for guild in self.bot.guilds:
            for channel in list(guild.voice_channels) + list(guild.stage_channels):
                if channel.id in self.excluded_channel_ids:
                    continue
                for member in channel.members:
                    if not member.bot:
                        self.vc_start_times.setdefault(member.id, now)

    # --- テキストレベル：メッセージ投稿で加算（スパム防止に60秒クールダウン） ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        now = datetime.now()
        last = self.text_cooldown.get(message.author.id)
        if last is not None and (now - last).total_seconds() < 60:
            return
        self.text_cooldown[message.author.id] = now
        await self.process_text_data(message.author)

    async def process_text_data(self, member):
        conn = self.get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("SELECT text_count, text_rank FROM users WHERE user_id = %s", (member.id,))
            row = cur.fetchone()
            old_count = row['text_count'] if row else 0
            old_rank = row['text_rank'] if row else 0

            new_count = old_count + 1
            new_rank = old_rank
            while new_count >= self.get_required_messages(new_rank + 1):
                new_rank += 1

            cur.execute("""
                INSERT INTO users (user_id, text_count, text_rank, user_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    text_count = EXCLUDED.text_count,
                    text_rank = EXCLUDED.text_rank,
                    user_name = EXCLUDED.user_name
            """, (member.id, new_count, new_rank, member.display_name))
            conn.commit()
        except Exception as e:
            print(f"Error in VCRank(text): {e}")
            conn.rollback()
        finally:
            cur.close()
            conn.close()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        now = datetime.now()

        # 1. 退室・移動時の最終精算
        if before.channel is not None and before.channel != after.channel:
            join_time = self.vc_start_times.pop(member.id, None)
            if join_time:
                minutes = int((now - join_time).total_seconds() / 60)
                if minutes > 0:
                    await self._credit(member, before.channel, minutes)

        # 2. 入室・移動後の計測開始
        if after.channel is not None and before.channel != after.channel:
            if after.channel.id not in self.excluded_channel_ids:
                self.vc_start_times[member.id] = now

    def _rate_for_channel(self, channel) -> float:
        """チャンネルのカテゴリに応じた加算率。軽減カテゴリなら1/3。"""
        category_id = getattr(channel, "category_id", None)
        if category_id is not None and category_id in self.reduced_category_ids:
            return self.reduced_rate
        return 1.0

    async def _credit(self, member, channel, real_minutes: int):
        """経過分に加算率を掛けて計上する。1未満の端数は次回に持ち越す。"""
        earned = real_minutes * self._rate_for_channel(channel)
        total = self.pending_minutes.get(member.id, 0.0) + earned
        whole = int(total)
        self.pending_minutes[member.id] = total - whole
        if whole >= 1:
            await self.process_vc_data(member, whole)

    async def process_vc_data(self, member, minutes):
        """DB更新、報酬付与、ランク判定をまとめて処理"""
        conn = self.get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            cur.execute("SELECT vc_minutes_total, rank FROM users WHERE user_id = %s", (member.id,))
            row = cur.fetchone()

            old_total = row['vc_minutes_total'] if row else 0
            old_rank = row['rank'] if row else 0

            new_total = old_total + minutes

            # 累計時間からランクを都度計算し直す（カーブ変更時のズレも自動補正）
            new_rank = 0
            while new_total >= self.get_required_minutes(new_rank + 1):
                new_rank += 1

            # レベルが上がった分だけ MPチケットを配布（1レベルにつき1枚）
            tickets_gained = max(0, new_rank - old_rank)

            # DB更新
            cur.execute("""
                INSERT INTO users (user_id, vc_minutes_total, rank, mp_tickets, user_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    vc_minutes_total = EXCLUDED.vc_minutes_total,
                    rank = EXCLUDED.rank,
                    mp_tickets = users.mp_tickets + EXCLUDED.mp_tickets,
                    user_name = EXCLUDED.user_name
            """, (member.id, new_total, new_rank, tickets_gained, member.display_name))

            conn.commit()

        except Exception as e:
            print(f"Error in VCRank: {e}")
            conn.rollback()
        finally:
            cur.close()
            conn.close()

    @app_commands.command(name="rank", description="現在のVCランクと滞在時間を確認します")
    async def vc_status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = interaction.user.id

        conn = self.get_db()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT vc_minutes_total, rank, text_count, text_rank FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            saved_total = row['vc_minutes_total'] if row else 0
            current_rank = row['rank'] if row else 0
            text_count = row['text_count'] if row else 0
            text_level = row['text_rank'] if row else 0
            # リーダーボード順位（自分より累計時間が多い人数 + 1）
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE vc_minutes_total > %s", (saved_total,))
            rank_pos = (cur.fetchone()['c'] or 0) + 1
        conn.close()

        # セッション時間の計算（次の自動更新までの端数分）
        session_min = 0
        if user_id in self.vc_start_times:
            session_min = int((datetime.now() - self.vc_start_times[user_id]).total_seconds() / 60)
        display_total = saved_total + session_min

        # VC必要時間・進捗
        next_req = self.get_required_minutes(current_rank + 1)
        prev_req = self.get_required_minutes(current_rank)
        current_progress_min = max(0, display_total - prev_req)
        needed_min_in_this_rank = max(0, next_req - prev_req)

        # テキスト必要メッセージ・進捗
        text_next = self.get_required_messages(text_level + 1)
        text_prev = self.get_required_messages(text_level)
        text_cur = max(0, text_count - text_prev)
        text_need = max(0, text_next - text_prev)

        # ユーザーの色（未設定なら Discord ブルー）
        color = interaction.user.color
        accent = (color.r, color.g, color.b) if color.value else (88, 101, 242)

        try:
            avatar_bytes = await interaction.user.display_avatar.with_size(256).read()
            png = await asyncio.to_thread(
                render_rank_card,
                avatar_bytes,
                interaction.user.display_name,
                rank_pos,
                current_rank,
                current_progress_min,
                needed_min_in_this_rank,
                text_level,
                text_cur,
                text_need,
                accent,
            )
            file = discord.File(io.BytesIO(png), filename="rank.png")
            await interaction.followup.send(file=file)
        except Exception as e:
            print(f"[ERROR] ランクカードの生成に失敗しました: {e}")
            await interaction.followup.send(
                f"**VCランク** — Rank {current_rank}（#{rank_pos}）/ 累計 {display_total}分 / "
                f"次のランクまであと {max(0, next_req - display_total)}分"
            )

async def setup(bot):
    await bot.add_cog(VCRank(bot))
