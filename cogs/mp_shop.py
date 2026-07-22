import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import re
from datetime import datetime, timedelta

from core.admin_base import AdminCogBase
from core.db_base import DatabaseBase

# 交換に必要なチケット枚数
TRIAL_RESET_COST = 20
TEXT_CHANNEL_COST = 10
ROLE_CREATE_COST = 5
EMOJI_COST = 5
# 絵文字画像の上限（Discord仕様：256KB）
EMOJI_MAX_BYTES = 256 * 1024
MOOD_PHOTO_COST = int(os.getenv("MOOD_PHOTO_COST") or "3")
# 雰囲気写真の閲覧権を得てから画像投稿までの猶予（時間）
MOOD_PHOTO_HOURS = 24
# 画像とみなす拡張子
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic")


class TextChannelModal(discord.ui.Modal, title="個人専用テキストチャット作成"):
    ch_name = discord.ui.TextInput(
        label="チャンネル名", max_length=100, placeholder="例：〇〇専用部屋"
    )

    def __init__(self, cog: "MPShop", role_options: list[tuple[int, str]]):
        super().__init__()
        self.cog = cog
        # 閲覧ロールは男性・女性ロールのみから選択（設定が無ければ選択欄は出さない）
        self.roles = None
        if role_options:
            self.roles = discord.ui.CheckboxGroup(
                options=[discord.CheckboxGroupOption(label=name, value=str(rid)) for rid, name in role_options],
                min_values=1,
                max_values=len(role_options),
                required=True,
            )
            self.add_item(discord.ui.Label(text="閲覧できるロール（男性・女性から1つ以上必須）", component=self.roles))

    async def on_submit(self, interaction: discord.Interaction):
        role_ids = [int(v) for v in (self.roles.values or [])] if self.roles is not None else []
        roles = [r for r in (interaction.guild.get_role(rid) for rid in role_ids) if r is not None]
        await self.cog.redeem_text_channel(interaction, str(self.ch_name), roles)


class RoleCreateModal(discord.ui.Modal, title="ロール作成"):
    role_name = discord.ui.TextInput(label="ロール名", max_length=100, placeholder="例：〇〇ファン")
    color = discord.ui.TextInput(
        label="色（#RRGGBB・任意）", max_length=7, required=False, placeholder="#FF0000",
    )

    def __init__(self, cog: "MPShop"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.redeem_role_create(interaction, str(self.role_name), str(self.color))


class EmojiModal(discord.ui.Modal, title="サーバー絵文字を追加"):
    emoji_name = discord.ui.TextInput(
        label="絵文字の名前（英数字と_）", max_length=32, placeholder="例：my_emoji",
    )

    def __init__(self, cog: "MPShop"):
        super().__init__()
        self.cog = cog
        self.image = discord.ui.FileUpload(required=True, min_values=1, max_values=1)
        self.add_item(discord.ui.Label(text="絵文字にする画像（256KB以下・png/jpg/gif）", component=self.image))

    async def on_submit(self, interaction: discord.Interaction):
        attachment = self.image.values[0] if self.image.values else None
        await self.cog.redeem_emoji(interaction, str(self.emoji_name), attachment)


class MPShopView(discord.ui.View):
    """MPチケットの確認・交換パネル（永続ビュー）。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="チケットを確認", emoji="🎫", style=discord.ButtonStyle.gray, custom_id="persistent:mp_check", row=0)
    async def check(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "MPShop" = interaction.client.get_cog("MPShop")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        n = cog.get_tickets(interaction.user.id)
        await interaction.response.send_message(f"🎫 あなたのMPチケット：**{n}枚**", ephemeral=True)

    @discord.ui.select(
        placeholder="🎁 引き換える商品を選ぶ...",
        custom_id="persistent:mp_shop",
        row=1,
        options=[
            discord.SelectOption(
                label="お試し個通のリセット", value="trial_reset",
                description=f"お試し個通の誘い履歴をリセット（{TRIAL_RESET_COST}枚）", emoji="🔄",
            ),
            discord.SelectOption(
                label="個人専用テキストチャット作成", value="text_channel",
                description=f"閲覧ロールを指定して作成（{TEXT_CHANNEL_COST}枚）", emoji="📝",
            ),
            discord.SelectOption(
                label="ロール作成", value="create_role",
                description=f"名前と色を指定してロールを作成・付与（{ROLE_CREATE_COST}枚）", emoji="🎨",
            ),
            discord.SelectOption(
                label="雰囲気写真の閲覧権", value="mood_photo",
                description=f"24h以内に画像投稿しないと没収（{MOOD_PHOTO_COST}枚）", emoji="📷",
            ),
            discord.SelectOption(
                label="サーバー絵文字を追加", value="add_emoji",
                description=f"画像をアップロードして絵文字を追加（{EMOJI_COST}枚）", emoji="😀",
            ),
        ],
    )
    async def shop(self, interaction: discord.Interaction, select: discord.ui.Select):
        cog: "MPShop" = interaction.client.get_cog("MPShop")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        choice = select.values[0]
        # 同じ商品を連続で選べるよう、パネルのセレクトの選択状態をリセットする
        try:
            await interaction.message.edit(view=MPShopView())
        except (discord.NotFound, discord.HTTPException):
            pass
        await cog.handle_redeem(interaction, choice)


class MPShop(commands.Cog, DatabaseBase):
    """MPチケットの確認と、商品との交換パネル。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID", "0"))
        self.text_category_id = int(os.getenv("MP_TEXT_CATEGORY_ID", "0"))
        # テキストチャットの閲覧ロール選択肢は男性・女性ロールのみ
        self.male_role_id = int(os.getenv("MALE_ROLE_ID", "0"))
        self.female_role_id = int(os.getenv("FEMALE_ROLE_ID", "0"))
        # 雰囲気写真の閲覧ロールとチャンネル（未設定=空文字でも0扱い）
        self.mood_role_id = int(os.getenv("MOOD_PHOTO_ROLE_ID") or "0")
        self.mood_channel_id = int(os.getenv("MOOD_PHOTO_CHANNEL_ID") or "0")
        # チケット配布/没収のログ先（未設定ならログを出さない）
        self.log_channel_id = int(os.getenv("MP_LOG_CHANNEL_ID") or "0")

    async def cog_load(self):
        self.bot.add_view(MPShopView())
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS mp_text_channels (
                            user_id BIGINT PRIMARY KEY,
                            channel_id BIGINT NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS mood_photo_deadlines (
                            user_id BIGINT PRIMARY KEY,
                            deadline TIMESTAMP NOT NULL
                        )
                    """)
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] mp_shop テーブルの作成に失敗しました: {e}")
        self.mood_photo_checker.start()

    def cog_unload(self):
        self.mood_photo_checker.cancel()

    # ------------------------------------------------------------------ #
    # 雰囲気写真：閲覧ロールの付与と 24h 以内の画像投稿チェック
    # ------------------------------------------------------------------ #
    def _mood_channel(self, guild: discord.Guild):
        if self.mood_channel_id:
            ch = guild.get_channel(self.mood_channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        return discord.utils.get(guild.text_channels, name="雰囲気写真")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 雰囲気写真チャンネルに画像を投稿したら猶予をクリア（ノルマ達成）
        if message.author.bot or message.guild is None or not self.mood_role_id:
            return
        mood_ch = self._mood_channel(message.guild)
        if mood_ch is None or message.channel.id != mood_ch.id:
            return
        if not any(self._is_image(a) for a in message.attachments):
            return
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM mood_photo_deadlines WHERE user_id = %s", (message.author.id,))
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 雰囲気写真ノルマのクリアに失敗しました: {e}")

    @staticmethod
    def _is_image(attachment: discord.Attachment) -> bool:
        if attachment.content_type and attachment.content_type.startswith("image"):
            return True
        return attachment.filename.lower().endswith(IMAGE_EXTENSIONS)

    @tasks.loop(minutes=5.0)
    async def mood_photo_checker(self):
        if not self.mood_role_id:
            return
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id FROM mood_photo_deadlines WHERE deadline <= %s", (datetime.now(),)
                    )
                    expired = [r[0] for r in cur.fetchall()]
        except Exception as e:
            print(f"[ERROR] 雰囲気写真の期限チェックに失敗しました: {e}")
            return

        for user_id in expired:
            for guild in self.bot.guilds:
                role = guild.get_role(self.mood_role_id)
                member = guild.get_member(user_id)
                if role is not None and member is not None and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="雰囲気写真：24時間以内に画像投稿がなかったため没収")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            try:
                with self.get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM mood_photo_deadlines WHERE user_id = %s", (user_id,))
                        conn.commit()
            except Exception as e:
                print(f"[ERROR] 期限レコードの削除に失敗しました: {e}")

    @mood_photo_checker.before_loop
    async def before_mood_photo_checker(self):
        await self.bot.wait_until_ready()

    def _existing_text_channel(self, guild: discord.Guild, user_id: int):
        """そのユーザーが作成済みで、今も存在する個人テキストチャットを返す（無ければNone）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT channel_id FROM mp_text_channels WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
        except Exception as e:
            print(f"[ERROR] 個人テキストチャットの確認に失敗しました: {e}")
            return None
        if not row:
            return None
        return guild.get_channel(row[0])  # 削除済みなら None

    def _save_text_channel(self, user_id: int, channel_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO mp_text_channels (user_id, channel_id) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
                        (user_id, channel_id),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 個人テキストチャットの記録に失敗しました: {e}")

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="set_mp_panel", description="【管理者専用】MPチケット交換パネルを設置します")
    async def set_mp_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 MPチケット交換所",
            description=(
                "**チケットを確認** で所持枚数を確認できます。\n"
                "**商品を選ぶ** から交換できます。\n\n"
                f"🔄 お試し個通のリセット … **{TRIAL_RESET_COST}枚**\n"
                f"📝 個人専用テキストチャット作成 … **{TEXT_CHANNEL_COST}枚**（作成時に名前と閲覧ロールを指定）\n"
                f"🎨 ロール作成 … **{ROLE_CREATE_COST}枚**（名前と色を指定して作成・付与）\n"
                f"📷 雰囲気写真の閲覧権 … **{MOOD_PHOTO_COST}枚**（{MOOD_PHOTO_HOURS}時間以内に画像投稿しないと没収）\n"
                f"😀 サーバー絵文字を追加 … **{EMOJI_COST}枚**（画像をアップロードして絵文字化）"
            ),
            color=discord.Color.gold(),
        )
        await interaction.channel.send(embed=embed, view=MPShopView())
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)

    # ------------------------------------------------------------------ #
    # 管理者：チケットの配布 / 没収
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="mp_give", description="【管理者専用】指定ユーザーにMPチケットを配布します")
    @app_commands.describe(member="配布する相手", amount="配布する枚数")
    async def mp_give(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("1枚以上を指定してください。", ephemeral=True)
            return
        new_balance = self._grant(member.id, amount, member.display_name)
        await interaction.response.send_message(
            f"✅ {member.mention} に **{amount}枚** 配布しました。（現在 {new_balance}枚）", ephemeral=True
        )
        await self._send_mp_log(
            "🎫 チケット配布", interaction.user, member, amount, new_balance, discord.Color.green()
        )

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="mp_take", description="【管理者専用】指定ユーザーからMPチケットを没収します")
    @app_commands.describe(member="没収する相手", amount="没収する枚数")
    async def mp_take(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("1枚以上を指定してください。", ephemeral=True)
            return
        before = self.get_tickets(member.id)
        taken = min(amount, before)
        new_balance = self._grant(member.id, -taken, member.display_name) if taken else before
        await interaction.response.send_message(
            f"✅ {member.mention} から **{taken}枚** 没収しました。（現在 {new_balance}枚）", ephemeral=True
        )
        await self._send_mp_log(
            "🎫 チケット没収", interaction.user, member, taken, new_balance, discord.Color.red()
        )

    async def _send_mp_log(self, title, executor, target, amount, new_balance, color):
        if not self.log_channel_id:
            return
        channel = self.bot.get_channel(self.log_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        embed.add_field(name="実行者", value=executor.mention, inline=True)
        embed.add_field(name="対象者", value=target.mention, inline=True)
        embed.add_field(name="枚数", value=f"{amount}枚", inline=True)
        embed.add_field(name="変更後の残高", value=f"{new_balance}枚", inline=False)
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] MPログの送信に失敗しました: {e}")

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="mp_list", description="【管理者専用】メンバーのMPチケット所持数を表示します")
    async def mp_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id, mp_tickets FROM users WHERE mp_tickets > 0 ORDER BY mp_tickets DESC"
                    )
                    rows = cur.fetchall()
        except Exception as e:
            print(f"[ERROR] チケット一覧の取得に失敗しました: {e}")
            await interaction.followup.send("❌ 取得に失敗しました。")
            return

        if not rows:
            await interaction.followup.send("MPチケットを持っているメンバーはいません。")
            return

        guild = interaction.guild
        lines = []
        length = 0
        omitted = 0
        for i, (uid, tickets) in enumerate(rows, 1):
            member = guild.get_member(uid)
            name = member.display_name if member else f"退出済み（{uid}）"
            line = f"`{i:02d}.` {name} … **{tickets}枚**"
            if length + len(line) + 1 > 3900:
                omitted = len(rows) - len(lines)
                break
            lines.append(line)
            length += len(line) + 1
        if omitted:
            lines.append(f"…他 **{omitted}名**")

        total = sum(r[1] for r in rows)
        embed = discord.Embed(
            title="🎫 MPチケット所持一覧",
            description="\n".join(lines),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"所持者 {len(rows)}名 / 合計 {total}枚")
        await interaction.followup.send(embed=embed)

    def _grant(self, user_id: int, amount: int, user_name: str | None = None) -> int:
        """チケットを amount 枚増減し（負なら減少）、変更後の残高を返す。user_name があれば記録。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (user_id, mp_tickets, user_name) VALUES (%s, %s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET "
                        "mp_tickets = GREATEST(0, users.mp_tickets + EXCLUDED.mp_tickets), "
                        "user_name = COALESCE(EXCLUDED.user_name, users.user_name) "
                        "RETURNING mp_tickets",
                        (user_id, amount, user_name),
                    )
                    new_balance = cur.fetchone()[0]
                    conn.commit()
                    return new_balance
        except Exception as e:
            print(f"[ERROR] チケットの増減に失敗しました: {e}")
            return self.get_tickets(user_id)

    # ------------------------------------------------------------------ #
    # チケットのDB操作
    # ------------------------------------------------------------------ #
    def get_tickets(self, user_id: int) -> int:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT mp_tickets FROM users WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            print(f"[ERROR] チケット残高の取得に失敗しました: {e}")
            return 0

    def _spend(self, user_id: int, cost: int) -> bool:
        """残高が足りれば cost 枚を消費して True。足りなければ False（原子的）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET mp_tickets = mp_tickets - %s "
                        "WHERE user_id = %s AND mp_tickets >= %s RETURNING mp_tickets",
                        (cost, user_id, cost),
                    )
                    ok = cur.fetchone() is not None
                    conn.commit()
                    return ok
        except Exception as e:
            print(f"[ERROR] チケットの消費に失敗しました: {e}")
            return False

    def _refund(self, user_id: int, cost: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET mp_tickets = mp_tickets + %s WHERE user_id = %s",
                        (cost, user_id),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] チケットの返還に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 交換処理
    # ------------------------------------------------------------------ #
    async def handle_redeem(self, interaction: discord.Interaction, choice: str):
        if choice == "trial_reset":
            await self._redeem_trial_reset(interaction)
        elif choice == "text_channel":
            existing = self._existing_text_channel(interaction.guild, interaction.user.id)
            if existing is not None:
                await interaction.response.send_message(
                    f"❌ 個人テキストチャットは1つまでです。既に {existing.mention} を作成済みです。",
                    ephemeral=True,
                )
                return
            n = self.get_tickets(interaction.user.id)
            if n < TEXT_CHANNEL_COST:
                await interaction.response.send_message(
                    f"❌ チケットが足りません（{TEXT_CHANNEL_COST}枚必要・所持 {n}枚）。", ephemeral=True
                )
                return
            # 選べる閲覧ロールは男性・女性のみ（存在するものだけ）
            role_options = []
            for rid in (self.male_role_id, self.female_role_id):
                role = interaction.guild.get_role(rid) if rid else None
                if role is not None:
                    role_options.append((role.id, role.name))
            await interaction.response.send_modal(TextChannelModal(self, role_options))
        elif choice == "create_role":
            n = self.get_tickets(interaction.user.id)
            if n < ROLE_CREATE_COST:
                await interaction.response.send_message(
                    f"❌ チケットが足りません（{ROLE_CREATE_COST}枚必要・所持 {n}枚）。", ephemeral=True
                )
                return
            await interaction.response.send_modal(RoleCreateModal(self))
        elif choice == "mood_photo":
            await self._redeem_mood_photo(interaction)
        elif choice == "add_emoji":
            n = self.get_tickets(interaction.user.id)
            if n < EMOJI_COST:
                await interaction.response.send_message(
                    f"❌ チケットが足りません（{EMOJI_COST}枚必要・所持 {n}枚）。", ephemeral=True
                )
                return
            await interaction.response.send_modal(EmojiModal(self))

    async def redeem_emoji(self, interaction: discord.Interaction, name: str, attachment):
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{2,32}", name):
            await interaction.response.send_message(
                "❌ 絵文字名は英数字とアンダースコアの2〜32文字で入力してください。", ephemeral=True
            )
            return
        if attachment is None:
            await interaction.response.send_message("❌ 画像が添付されていません。", ephemeral=True)
            return
        if attachment.size > EMOJI_MAX_BYTES:
            await interaction.response.send_message(
                "❌ 画像は256KB以下にしてください。", ephemeral=True
            )
            return

        # 画像取得・絵文字作成は時間がかかるので defer
        await interaction.response.defer(ephemeral=True)
        if not self._spend(interaction.user.id, EMOJI_COST):
            n = self.get_tickets(interaction.user.id)
            await interaction.followup.send(
                f"❌ チケットが足りません（{EMOJI_COST}枚必要・所持 {n}枚）。", ephemeral=True
            )
            return
        try:
            data = await attachment.read()
            emoji = await interaction.guild.create_custom_emoji(
                name=name, image=data, reason=f"MPチケット交換：{interaction.user} の絵文字追加"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 絵文字の追加に失敗しました: {e}")
            self._refund(interaction.user.id, EMOJI_COST)
            await interaction.followup.send(
                "❌ 絵文字の追加に失敗しました（画像形式・サイズ、絵文字枠の空き、Botの権限をご確認ください）。"
                "チケットは消費されていません。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ 絵文字 {emoji} を追加しました！（-{EMOJI_COST}枚）", ephemeral=True
        )

    async def _redeem_mood_photo(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(self.mood_role_id) if self.mood_role_id else None
        if role is None:
            await interaction.response.send_message(
                "❌ 雰囲気写真の閲覧ロールが設定されていません。管理者にお問い合わせください。", ephemeral=True
            )
            return
        if role in member.roles:
            await interaction.response.send_message(
                "既に雰囲気写真の閲覧権を持っています。", ephemeral=True
            )
            return
        if not self._spend(member.id, MOOD_PHOTO_COST):
            n = self.get_tickets(member.id)
            await interaction.response.send_message(
                f"❌ チケットが足りません（{MOOD_PHOTO_COST}枚必要・所持 {n}枚）。", ephemeral=True
            )
            return
        try:
            await member.add_roles(role, reason="MPチケット交換：雰囲気写真の閲覧権")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 雰囲気写真ロールの付与に失敗しました: {e}")
            self._refund(member.id, MOOD_PHOTO_COST)
            await interaction.response.send_message(
                "❌ ロールの付与に失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return

        # 24時間以内の画像投稿ノルマを登録
        deadline = datetime.now() + timedelta(hours=MOOD_PHOTO_HOURS)
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO mood_photo_deadlines (user_id, deadline) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET deadline = EXCLUDED.deadline",
                        (member.id, deadline),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 雰囲気写真ノルマの登録に失敗しました: {e}")

        mood_ch = self._mood_channel(guild)
        where = mood_ch.mention if mood_ch else "「雰囲気写真」チャンネル"
        await interaction.response.send_message(
            f"✅ 雰囲気写真の閲覧権を付与しました！（-{MOOD_PHOTO_COST}枚）\n"
            f"⚠️ **{MOOD_PHOTO_HOURS}時間以内に {where} へ画像を投稿しないと閲覧権は没収されます。**",
            ephemeral=True,
        )

    async def _redeem_trial_reset(self, interaction: discord.Interaction):
        if not self._spend(interaction.user.id, TRIAL_RESET_COST):
            n = self.get_tickets(interaction.user.id)
            await interaction.response.send_message(
                f"❌ チケットが足りません（{TRIAL_RESET_COST}枚必要・所持 {n}枚）。", ephemeral=True
            )
            return
        # お試し個通の誘い履歴を削除（call_matching の trial_invites テーブル）
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM trial_invites WHERE recruiter_id = %s", (interaction.user.id,))
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] お試し個通のリセットに失敗しました: {e}")
            self._refund(interaction.user.id, TRIAL_RESET_COST)
            await interaction.response.send_message(
                "❌ リセットに失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ お試し個通の誘い履歴をリセットしました。（-{TRIAL_RESET_COST}枚）", ephemeral=True
        )

    async def redeem_text_channel(self, interaction: discord.Interaction, name: str, roles: list):
        # 作成直前の再チェック（1人1つまで）
        existing = self._existing_text_channel(interaction.guild, interaction.user.id)
        if existing is not None:
            await interaction.response.send_message(
                f"❌ 個人テキストチャットは1つまでです。既に {existing.mention} を作成済みです。",
                ephemeral=True,
            )
            return
        if not self._spend(interaction.user.id, TEXT_CHANNEL_COST):
            n = self.get_tickets(interaction.user.id)
            await interaction.response.send_message(
                f"❌ チケットが足りません（{TEXT_CHANNEL_COST}枚必要・所持 {n}枚）。", ephemeral=True
            )
            return

        guild = interaction.guild
        member = interaction.user
        category = guild.get_channel(self.text_category_id) if self.text_category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        admin_role = guild.get_role(self.admin_role_id) if self.admin_role_id else None
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        try:
            channel = await guild.create_text_channel(
                name=name, overwrites=overwrites, category=category,
                reason=f"MPチケット交換：{member} の個人テキストチャット",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 個人テキストチャットの作成に失敗しました: {e}")
            self._refund(member.id, TEXT_CHANNEL_COST)
            await interaction.response.send_message(
                "❌ チャンネル作成に失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return

        self._save_text_channel(member.id, channel.id)

        role_txt = "、".join(r.name for r in roles) if roles else "なし"
        await interaction.response.send_message(
            f"✅ {channel.mention} を作成しました！（-{TEXT_CHANNEL_COST}枚）\n閲覧ロール：{role_txt}",
            ephemeral=True,
        )

    async def redeem_role_create(self, interaction: discord.Interaction, name: str, color_str: str):
        # 色の検証（任意。未入力なら色なし）
        color_str = color_str.strip().lstrip("#")
        if color_str:
            try:
                cval = int(color_str, 16)
                if len(color_str) != 6 or not (0 <= cval <= 0xFFFFFF):
                    raise ValueError
                colour = discord.Colour(cval)
            except ValueError:
                await interaction.response.send_message(
                    "❌ 色は #RRGGBB 形式（例：#FF0000）で入力してください。", ephemeral=True
                )
                return
        else:
            colour = discord.Colour.default()

        if not self._spend(interaction.user.id, ROLE_CREATE_COST):
            n = self.get_tickets(interaction.user.id)
            await interaction.response.send_message(
                f"❌ チケットが足りません（{ROLE_CREATE_COST}枚必要・所持 {n}枚）。", ephemeral=True
            )
            return

        guild = interaction.guild
        member = interaction.user
        try:
            # 新規ロールは既定で最下位（@everyone の直上）に作成される
            role = await guild.create_role(
                name=name, colour=colour, reason=f"MPチケット交換：{member} のロール作成"
            )
            await member.add_roles(role, reason="MPチケット交換で作成したロールを付与")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] ロールの作成/付与に失敗しました: {e}")
            self._refund(member.id, ROLE_CREATE_COST)
            await interaction.response.send_message(
                "❌ ロールの作成に失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ ロール {role.mention} を作成して付与しました！（-{ROLE_CREATE_COST}枚）", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MPShop(bot))
