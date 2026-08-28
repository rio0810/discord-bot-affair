import logging
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import config
from core.admin_base import AdminCogBase

from .constants import (
    COLOR_ROLE_COST,
    EMOJI_COST,
    EMOJI_MAX_BYTES,
    GRADIENT_COLORS,
    HOLOGRAPHIC_COLORS,
    IMAGE_EXTENSIONS,
    MOOD_PHOTO_COST,
    MOOD_PHOTO_HOURS,
    TEXT_CHANNEL_COST,
    TRIAL_RESET_COST,
)
from .db import MPShopDBMixin
from .ui import ColorRoleModal, EmojiModal, EmojiPreviewView, MPShopView, TextChannelModal

logger = logging.getLogger(__name__)


class MPShop(commands.Cog, MPShopDBMixin):
    """MPチケットの確認と、商品との交換パネル。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.admin_role_id = config.ADMIN_ROLE_ID
        self.text_category_id = config.MP_TEXT_CATEGORY_ID
        # テキストチャットの閲覧ロール選択肢は男性・女性ロールのみ
        self.male_role_id = config.MALE_ROLE_ID
        self.female_role_id = config.FEMALE_ROLE_ID
        # 雰囲気写真の閲覧ロールとチャンネル（未設定=空文字でも0扱い）
        self.mood_role_id = config.MOOD_PHOTO_ROLE_ID
        self.mood_channel_id = config.MOOD_PHOTO_CHANNEL_ID
        # チケット配布/没収のログ先（未設定ならログを出さない）
        self.log_channel_id = config.MP_LOG_CHANNEL_ID

    async def cog_load(self):
        self.bot.add_view(MPShopView())
        self._ensure_tables()
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
        self._clear_mood_deadline(message.author.id)

    @staticmethod
    def _is_image(attachment: discord.Attachment) -> bool:
        if attachment.content_type and attachment.content_type.startswith("image"):
            return True
        return attachment.filename.lower().endswith(IMAGE_EXTENSIONS)

    @tasks.loop(minutes=5.0)
    async def mood_photo_checker(self):
        if not self.mood_role_id:
            return
        for user_id in self._expired_mood_user_ids():
            for guild in self.bot.guilds:
                role = guild.get_role(self.mood_role_id)
                member = guild.get_member(user_id)
                if role is not None and member is not None and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="雰囲気写真：24時間以内に画像投稿がなかったため没収")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            self._clear_mood_deadline(user_id)

    @mood_photo_checker.before_loop
    async def before_mood_photo_checker(self):
        await self.bot.wait_until_ready()

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
                f"📝 個人専用テキストチャット作成 … **{TEXT_CHANNEL_COST}枚**（作成時に名前と閲覧ロール・閲覧ユーザーを指定）\n"
                f"🌈 カラーロール作成 … **{COLOR_ROLE_COST}枚**（グラデーション/ホログラフィック・男=青/女=赤ベース）\n"
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
            logger.error(f"MPログの送信に失敗しました: {e}")

    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="mp_list", description="【管理者専用】メンバーのMPチケット所持数を表示します")
    async def mp_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = self._list_ticket_holders()
        if rows is None:
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
        elif choice == "color_role":
            n = self.get_tickets(interaction.user.id)
            if n < COLOR_ROLE_COST:
                await interaction.response.send_message(
                    f"❌ チケットが足りません（{COLOR_ROLE_COST}枚必要・所持 {n}枚）。", ephemeral=True
                )
                return
            await interaction.response.send_modal(ColorRoleModal(self))
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
            logger.error(f"絵文字の追加に失敗しました: {e}")
            self._refund(interaction.user.id, EMOJI_COST)
            await interaction.followup.send(
                "❌ 絵文字の追加に失敗しました（画像形式・サイズ、絵文字枠の空き、Botの権限をご確認ください）。"
                "チケットは消費されていません。",
                ephemeral=True,
            )
            return
        # テキスト中の見え方（小）と単体の見え方（大）を確認できるようにする。
        # 絵文字だけのメッセージは Discord 側で大きく表示されるため、2通に分けて送る。
        await interaction.followup.send(
            f"✅ 絵文字 {emoji} を追加しました！（-{EMOJI_COST}枚）\n"
            f"**テキスト中：** おはよう {emoji} また明日 {emoji}\n"
            "**単体（大きい表示）：** ↓",
            ephemeral=True,
        )
        view = EmojiPreviewView(self, emoji, interaction.user.id)
        view.message = await interaction.followup.send(str(emoji), view=view, ephemeral=True, wait=True)

    async def cancel_emoji(self, interaction: discord.Interaction, emoji: discord.Emoji):
        """プレビューを見て取り消した場合：絵文字を削除してチケットを返金する。"""
        await interaction.response.defer()
        try:
            await emoji.delete(reason=f"MPチケット交換：{interaction.user} が絵文字の追加を取り消し")
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"絵文字の削除に失敗しました: {e}")
            await interaction.followup.send(
                "❌ 絵文字の削除に失敗しました。管理者にご連絡ください。", ephemeral=True
            )
            return
        self._refund(interaction.user.id, EMOJI_COST)
        await interaction.edit_original_response(
            content=f"🗑️ 絵文字を削除し、チケット{EMOJI_COST}枚を返金しました。", view=None
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
            logger.error(f"雰囲気写真ロールの付与に失敗しました: {e}")
            self._refund(member.id, MOOD_PHOTO_COST)
            await interaction.response.send_message(
                "❌ ロールの付与に失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return

        # 24時間以内の画像投稿ノルマを登録
        deadline = datetime.now() + timedelta(hours=MOOD_PHOTO_HOURS)
        self._set_mood_deadline(member.id, deadline)

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
        if not self._reset_trial_invites(interaction.user.id):
            self._refund(interaction.user.id, TRIAL_RESET_COST)
            await interaction.response.send_message(
                "❌ リセットに失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ お試し個通の誘い履歴をリセットしました。（-{TRIAL_RESET_COST}枚）", ephemeral=True
        )

    async def redeem_text_channel(
        self, interaction: discord.Interaction, name: str, roles: list, members: list | None = None
    ):
        viewers = [m for m in (members or []) if m.id != interaction.user.id]
        if not roles and not viewers:
            await interaction.response.send_message(
                "❌ 閲覧できるロールかユーザーを1つ以上指定してください。", ephemeral=True
            )
            return
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
        for target in viewers:
            overwrites[target] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        try:
            channel = await guild.create_text_channel(
                name=name, overwrites=overwrites, category=category,
                reason=f"MPチケット交換：{member} の個人テキストチャット",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"個人テキストチャットの作成に失敗しました: {e}")
            self._refund(member.id, TEXT_CHANNEL_COST)
            await interaction.response.send_message(
                "❌ チャンネル作成に失敗しました。チケットは消費されていません。", ephemeral=True
            )
            return

        self._save_text_channel(member.id, channel.id)

        role_txt = "、".join(r.name for r in roles) if roles else "なし"
        viewer_txt = "、".join(m.display_name for m in viewers) if viewers else "なし"
        await interaction.response.send_message(
            f"✅ {channel.mention} を作成しました！（-{TEXT_CHANNEL_COST}枚）\n閲覧ロール：{role_txt}\n閲覧ユーザー：{viewer_txt}",
            ephemeral=True,
        )

    async def redeem_color_role(self, interaction: discord.Interaction, name: str, style: str):
        guild = interaction.guild
        member = interaction.user

        # グラデーションのベース色は性別ロールで決定（ホログラフィックは固定配色）
        if self.female_role_id and guild.get_role(self.female_role_id) in member.roles:
            gender = "f"
        elif self.male_role_id and guild.get_role(self.male_role_id) in member.roles:
            gender = "m"
        else:
            await interaction.response.send_message(
                "❌ この商品は男性/女性ロールが必要です。", ephemeral=True
            )
            return

        if not self._spend(member.id, COLOR_ROLE_COST):
            n = self.get_tickets(member.id)
            await interaction.response.send_message(
                f"❌ チケットが足りません（{COLOR_ROLE_COST}枚必要・所持 {n}枚）。", ephemeral=True
            )
            return

        # 配色を決定（ホログラフィックは3色固定・グラデーションは性別ベースの2色）
        if style == "holographic":
            p, s, t = HOLOGRAPHIC_COLORS
            colours = dict(
                colour=discord.Colour(p),
                secondary_colour=discord.Colour(s),
                tertiary_colour=discord.Colour(t),
            )
            style_label = "ホログラフィック"
        else:
            p, s = GRADIENT_COLORS[gender]
            colours = dict(colour=discord.Colour(p), secondary_colour=discord.Colour(s))
            style_label = "グラデーション"

        try:
            # 新規ロールは既定で最下位（@everyone の直上）に作成される
            role = await guild.create_role(
                name=name, reason=f"MPチケット交換：{member} のカラーロール作成", **colours
            )
            await member.add_roles(role, reason="MPチケット交換で作成したカラーロールを付与")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"カラーロールの作成/付与に失敗しました: {e}")
            self._refund(member.id, COLOR_ROLE_COST)
            await interaction.response.send_message(
                "❌ カラーロールの作成に失敗しました。\n"
                "グラデーション/ホログラフィックの色は **サーバーブースト（強化ロールカラー）が有効なサーバー** でのみ使えます。"
                "Botの権限・ロール順もあわせてご確認ください。チケットは消費されていません。",
                ephemeral=True,
            )
            return

        # 表示色が他のロールに上書きされないよう、Botの最上位ロールの直下へ移動する
        # （Botは自分の最上位ロールより下しか動かせないため、そこが置ける最上位）
        top = guild.me.top_role
        if top.position > 1:
            try:
                await role.edit(position=top.position - 1, reason="カラーロールを表示色が出る位置へ")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"カラーロールの位置調整に失敗しました: {e}")

        # 作り直しの場合は、以前のカラーロールを削除する（1人1つに保つ）
        old_id = self._get_color_role_id(member.id)
        if old_id and old_id != role.id:
            old_role = guild.get_role(old_id)
            if old_role is not None:
                try:
                    await old_role.delete(reason="カラーロール作り直しのため旧ロールを削除")
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"旧カラーロールの削除に失敗しました: {e}")
        self._save_color_role(member.id, role.id)

        await interaction.response.send_message(
            f"✅ カラーロール {role.mention} を作成して付与しました！（{style_label}・-{COLOR_ROLE_COST}枚）",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MPShop(bot))
