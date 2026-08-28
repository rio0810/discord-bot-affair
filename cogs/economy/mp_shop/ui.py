from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .constants import (
    COLOR_ROLE_COST,
    EMOJI_COST,
    MOOD_PHOTO_COST,
    TEXT_CHANNEL_COST,
    TRIAL_RESET_COST,
)

if TYPE_CHECKING:
    from .cog import MPShop


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
                min_values=0,
                max_values=len(role_options),
                required=False,
            )
            self.add_item(discord.ui.Label(text="閲覧できるロール（男性・女性／任意）", component=self.roles))
        # ロールとは別に、個別のユーザーも閲覧者として指定できる（任意）
        self.users = discord.ui.UserSelect(min_values=0, max_values=25, required=False)
        self.add_item(discord.ui.Label(text="閲覧できるユーザー（任意・最大25人）", component=self.users))

    async def on_submit(self, interaction: discord.Interaction):
        role_ids = [int(v) for v in (self.roles.values or [])] if self.roles is not None else []
        roles = [r for r in (interaction.guild.get_role(rid) for rid in role_ids) if r is not None]
        # UserSelect の値はギルド外ユーザーが混ざりうるので Member だけ拾う
        members = [u for u in (self.users.values or []) if isinstance(u, discord.Member)]
        await self.cog.redeem_text_channel(interaction, str(self.ch_name), roles, members)


class ColorRoleModal(discord.ui.Modal, title="カラーロール作成"):
    role_name = discord.ui.TextInput(label="ロール名", max_length=100, placeholder="例：〇〇カラー")

    def __init__(self, cog: "MPShop"):
        super().__init__()
        self.cog = cog
        self.style = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(
                    label="🌈 グラデーション", value="gradient",
                    description="男性は青、女性は赤をベースにした2色グラデ",
                ),
                discord.RadioGroupOption(
                    label="✨ ホログラフィック", value="holographic",
                    description="Discord標準のきらめき配色（男女共通）",
                ),
            ],
            required=True,
        )
        self.add_item(discord.ui.Label(text="色のスタイル", component=self.style))

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.redeem_color_role(interaction, str(self.role_name), self.style.value)


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


class EmojiPreviewView(discord.ui.View):
    """追加直後の絵文字を確認し、気に入らなければ取り消して返金するビュー。"""

    def __init__(self, cog: "MPShop", emoji: discord.Emoji, user_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.emoji_obj = emoji
        self.user_id = user_id
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="取り消す（削除して返金）", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ この操作はできません。", ephemeral=True)
            return
        await self.cog.cancel_emoji(interaction, self.emoji_obj)
        self.stop()


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
                label="カラーロール作成（グラデ/ホロ）", value="color_role",
                description=f"グラデーション/ホログラフィックのロールを作成（{COLOR_ROLE_COST}枚）", emoji="🌈",
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
