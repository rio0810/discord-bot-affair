from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .constants import (
    BLOCK_GROUP_SIZE,
    PAGE_SIZE,
    TRIAL_DURATION_MINUTES,
    TRIAL_WARNING_REMAINING,
)

if TYPE_CHECKING:
    from .cog import CallMatchingCog


# ---------------------------------------------------------------------- #
# 相手選択（ページング付き Select）
# ---------------------------------------------------------------------- #
class TargetSelect(discord.ui.Select):
    def __init__(self, view: "TargetSelectView"):
        page = view.targets[view.page * PAGE_SIZE : (view.page + 1) * PAGE_SIZE]
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id), description=f"@{m.name}")
            for m in page
        ]
        super().__init__(placeholder="通話に誘う相手を選んでください...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cog: "CallMatchingCog" = self.view.cog
        target = interaction.guild.get_member(int(self.values[0]))
        if target is None:
            await interaction.response.send_message("❌ そのメンバーが見つかりません。", ephemeral=True)
            return

        # 相手の部屋数上限チェック
        limit = cog.max_rooms_for(target)
        if cog.count_rooms(interaction.guild, target.id) >= limit:
            await interaction.response.send_message(
                f"❌ {target.display_name} さんは現在、通話部屋の上限（{limit}件）に達しています。",
                ephemeral=True,
            )
            return

        # ブロック関係の再チェック（一覧表示後にブロックされた場合など）
        if cog.is_blocked_between(interaction.user.id, target.id):
            await interaction.response.send_message(
                "❌ この相手にはお誘いを送れません。", ephemeral=True
            )
            return

        # お試し個通は同じ相手に1回まで（一覧表示後に履歴が増えた場合の再チェック）
        if self.view.trial and target.id in cog.get_trial_invited_ids(interaction.user.id):
            await interaction.response.send_message(
                f"❌ {target.display_name} さんには既にお試し個通のお誘いを送ったことがあるため、再度誘えません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(RecruitModal(cog, target, self.view.trial))


class TargetSelectView(discord.ui.View):
    """誘える相手の一覧をページング付きで表示するビュー（ephemeral用）。"""

    def __init__(self, cog: "CallMatchingCog", targets: list[discord.Member], trial: bool = False):
        super().__init__(timeout=180)
        self.cog = cog
        self.targets = targets
        self.trial = trial
        self.page = 0
        self._rebuild()

    @property
    def max_page(self) -> int:
        return (len(self.targets) - 1) // PAGE_SIZE

    def _rebuild(self):
        self.clear_items()
        self.add_item(TargetSelect(self))
        if self.max_page > 0:
            prev_btn = discord.ui.Button(
                label="◀ 前へ", style=discord.ButtonStyle.gray, disabled=(self.page == 0)
            )
            next_btn = discord.ui.Button(
                label="次へ ▶", style=discord.ButtonStyle.gray, disabled=(self.page >= self.max_page)
            )
            page_label = discord.ui.Button(
                label=f"{self.page + 1} / {self.max_page + 1}", style=discord.ButtonStyle.gray, disabled=True
            )

            async def go_prev(it: discord.Interaction):
                self.page -= 1
                self._rebuild()
                await it.response.edit_message(view=self)

            async def go_next(it: discord.Interaction):
                self.page += 1
                self._rebuild()
                await it.response.edit_message(view=self)

            prev_btn.callback = go_prev
            next_btn.callback = go_next
            self.add_item(prev_btn)
            self.add_item(page_label)
            self.add_item(next_btn)


# ---------------------------------------------------------------------- #
# ブロック編集 Modal（チェックボックスでブロック/解除をまとめて編集）
# ---------------------------------------------------------------------- #
class BlockEditModal(discord.ui.Modal, title="ブロック編集"):
    def __init__(
        self,
        cog: "CallMatchingCog",
        user_id: int,
        candidates: list[tuple[int, str, str | None]],
        blocked: set[int],
    ):
        """candidates: (user_id, 表示名, 説明) のリスト。blocked: 現在ブロック中のID。"""
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.shown_ids = {uid for uid, _, _ in candidates}
        self.blocked_shown = blocked & self.shown_ids
        self.groups: list[discord.ui.CheckboxGroup] = []

        chunks = [
            candidates[i : i + BLOCK_GROUP_SIZE]
            for i in range(0, len(candidates), BLOCK_GROUP_SIZE)
        ]
        for idx, chunk in enumerate(chunks, 1):
            group = discord.ui.CheckboxGroup(
                options=[
                    discord.CheckboxGroupOption(
                        label=name, value=str(uid), description=desc, default=(uid in blocked)
                    )
                    for uid, name, desc in chunk
                ],
                min_values=0,
                max_values=len(chunk),
                required=False,
            )
            self.groups.append(group)
            text = "ブロックする相手" + (f"（{idx}/{len(chunks)}）" if len(chunks) > 1 else "")
            self.add_item(discord.ui.Label(text=text, component=group))

    async def on_submit(self, interaction: discord.Interaction):
        checked = {int(v) for g in self.groups for v in (g.values or [])}

        added = checked - self.blocked_shown
        removed = self.blocked_shown - checked
        for uid in added:
            self.cog.add_block(self.user_id, uid)
        for uid in removed:
            self.cog.remove_block(self.user_id, uid)

        if not added and not removed:
            await interaction.response.send_message("変更はありませんでした。", ephemeral=True)
            return

        parts = []
        if added:
            parts.append(f"🚫 **{len(added)}人** をブロックしました")
        if removed:
            parts.append(f"✅ **{len(removed)}人** のブロックを解除しました")
        await interaction.response.send_message(
            "、".join(parts) + "。\nブロック中の相手とは、お互いにお誘い相手の一覧へ表示されません。",
            ephemeral=True,
        )


# ---------------------------------------------------------------------- #
# メッセージ入力 Modal
# ---------------------------------------------------------------------- #
class RecruitModal(discord.ui.Modal, title="通話のお誘いメッセージ"):
    message = discord.ui.TextInput(
        label="相手に送るメッセージ",
        style=discord.TextStyle.paragraph,
        placeholder="はじめまして！よかったらお話しませんか？",
        max_length=500,
    )

    def __init__(self, cog: "CallMatchingCog", target: discord.Member, trial: bool = False):
        super().__init__()
        self.cog = cog
        self.target = target
        self.trial = trial

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.send_request(interaction, self.target, str(self.message), trial=self.trial)


# ---------------------------------------------------------------------- #
# DM の「受ける / 断る」ボタン（再起動後も動く DynamicItem）
# ---------------------------------------------------------------------- #
class CallRequestButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"call_req:(?P<action>accept|decline):(?P<recruiter_id>[0-9]+):(?P<guild_id>[0-9]+)(?P<trial>:trial)?",
):
    def __init__(self, action: str, recruiter_id: int, guild_id: int, trial: bool = False):
        self.action = action
        self.recruiter_id = recruiter_id
        self.guild_id = guild_id
        self.trial = trial
        if action == "accept":
            label, style, emoji = "受ける", discord.ButtonStyle.green, "✅"
        else:
            label, style, emoji = "断る", discord.ButtonStyle.red, "❌"
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                emoji=emoji,
                custom_id=f"call_req:{action}:{recruiter_id}:{guild_id}" + (":trial" if trial else ""),
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(
            match["action"],
            int(match["recruiter_id"]),
            int(match["guild_id"]),
            trial=match["trial"] is not None,
        )

    async def callback(self, interaction: discord.Interaction):
        cog: "CallMatchingCog" = interaction.client.get_cog("CallMatchingCog")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        if self.action == "accept":
            await cog.handle_accept(interaction, self.recruiter_id, self.guild_id, trial=self.trial)
        else:
            await cog.handle_decline(interaction, self.recruiter_id, self.guild_id, trial=self.trial)


# ---------------------------------------------------------------------- #
# 募集パネル / 部屋の終了ボタン（永続ビュー）
# ---------------------------------------------------------------------- #
class CallPanelActions(discord.ui.ActionRow):
    """募集パネルのボタン行（1行目：申請）。custom_id は旧パネルと共通なので既設パネルも動く。"""

    def __init__(self, cog: "CallMatchingCog"):
        super().__init__()
        self.cog = cog

    @discord.ui.button(label="個通部屋申請", style=discord.ButtonStyle.green, emoji="📞", custom_id="persistent:call_recruit")
    async def recruit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_recruit(interaction)

    @discord.ui.button(label="お試し個通申請", style=discord.ButtonStyle.blurple, emoji="⏳", custom_id="persistent:call_recruit_trial")
    async def recruit_trial(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_recruit(interaction, trial=True)


class CallPanelSettingsActions(discord.ui.ActionRow):
    """募集パネルのボタン行（2行目：ブロック編集・人数制限）。"""

    def __init__(self, cog: "CallMatchingCog"):
        super().__init__()
        self.cog = cog

    @discord.ui.button(label="ブロック編集", style=discord.ButtonStyle.gray, emoji="🚫", custom_id="persistent:call_block_edit")
    async def block_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_block_edit(interaction)

    @discord.ui.button(label="人数制限（1人⇔解除）", style=discord.ButtonStyle.gray, emoji="🔒", custom_id="persistent:call_room_limit")
    async def room_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_room_limit_toggle(interaction)


class CallPanelView(discord.ui.LayoutView):
    """Components V2 の募集パネル（タイトル下に Separator の区切り線）。"""

    def __init__(self, cog: "CallMatchingCog"):
        super().__init__(timeout=None)
        large = discord.SeparatorSpacing.large

        def section(text: str):
            container.add_item(discord.ui.Separator(spacing=large))
            container.add_item(discord.ui.TextDisplay(text))

        container = discord.ui.Container(accent_colour=discord.Colour.pink())
        container.add_item(discord.ui.TextDisplay("## 📞 個通申請パネル"))
        container.add_item(discord.ui.Separator(spacing=large))
        container.add_item(
            discord.ui.TextDisplay(
                "⚠️ **新人ロールが付いている間は個通をご利用いただけません。**\n\n"
                "下のボタンから、通話したい相手を選んで個通のお誘いを送れます。"
                "詳細の利用方法は動画をご確認ください。"
            )
        )
        section(
            "### 🔰 基本の流れ\n"
            "- ボタンを押してお誘いのメッセージを入力すると、**Botが代理で相手にお誘いを送信** し、相手に **受ける / 断る** を選んでもらいます。\n"
            "- 承認されると、**2人だけの専用VC・テキストチャンネル** が作成されます。"
        )
        section(
            "### ⏱️ お試し個通について\n"
            f"- VCに入ってから **{TRIAL_DURATION_MINUTES}分で自動終了** します。\n"
            f"- 残り{TRIAL_WARNING_REMAINING}分になると **Botが一時的にVCへ入室し、サウンドボードで通知** します。\n"
            "- お誘いは **同じ相手につき1回まで** です。"
        )
        section(
            "### 🚫 ブロック・制限機能\n"
            "- **ブロック編集** でチェックを入れた相手とは、お互いにお誘い相手の一覧に"
            "表示されなくなります（チェックを外せばいつでも解除可）。\n"
            "- お誘いを **断る** と、その相手は自動でブロックされます（ブロック編集から解除可能）。\n"
            "- **人数制限** で、自分が同時に持てる部屋を **1件** に制限できます"
            "（個通部屋を1件以上持っているときのみ設定可・もう一度押すと解除）。\n"
            f"- 同時に持てる通話部屋は **男性{cog.max_rooms_per_male}件 / "
            f"女性{cog.max_rooms_per_female}件** までです。"
        )
        section(
            "### 👀 お誘い相手の一覧に表示されない条件\n"
            "次のいずれかに当てはまる相手は、お誘い先のリストに表示されません。\n"
            "- 雑談のみのロールが付いている\n"
            "- お互いにブロックしている\n"
            "- すでに個通部屋を **2件以上**（お試し個通の部屋も含む）持っている\n"
            "- 個通部屋を **1件のみ** に制限している\n"
            "- 自分と **同性**\n"
            "- 一度こちらのお誘いを断っている（断ると自動的に相手をブロックします。**ブロック編集** から解除できます）\n\n"
            "リストに出てこない場合は、お試し個通の上限に達している可能性があります。"
            "少し時間をおいてから、もう一度申請してみてください。\n"
            "※ ブロックされていても、個通のお誘いリストに出てこないだけです。"
            "通常のVCやチャットでは今までどおり見えます。"
        )
        # 文章とボタンの間の余白（線は表示しない）
        container.add_item(discord.ui.Separator(visible=False, spacing=large))
        container.add_item(CallPanelActions(cog))
        container.add_item(CallPanelSettingsActions(cog))
        self.add_item(container)


class CallRoomCloseModal(discord.ui.Modal, title="個通部屋の削除"):
    """削除前の確認モーダル（はい/いいえ を RadioGroup で選択）。"""

    def __init__(self, cog: "CallMatchingCog"):
        super().__init__()
        self.cog = cog
        self.confirm = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(label="はい（削除する）", value="yes", emoji="✅"),
                discord.RadioGroupOption(label="いいえ（削除しない）", value="no", emoji="❌"),
            ],
            required=True,
        )
        self.add_item(discord.ui.Label(text="この個通部屋を削除しますか？", component=self.confirm))

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value == "yes":
            await self.cog.handle_close(interaction)
        else:
            await interaction.response.send_message("削除をキャンセルしました。", ephemeral=True)


class CallRoomCloseView(discord.ui.View):
    def __init__(self, cog: "CallMatchingCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="個通部屋を削除する", style=discord.ButtonStyle.red, emoji="🚪", custom_id="persistent:call_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        # まず確認モーダルを出し、「はい」なら handle_close で削除する
        await interaction.response.send_modal(CallRoomCloseModal(self.cog))
