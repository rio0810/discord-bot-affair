"""相談VC：雑談ロール・恋愛ロールの人が、相手を1人選んで
2人だけの相談用VCを作れる機能。

パネルのボタン → 相談したい相手を選択 → 非公開VCを作成し、相手にDMで案内する。
VCが空になった時点（および未使用のまま一定時間経過した時点）で自動削除される。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import config
from core.admin_base import AdminCogBase
from core.db_base import DatabaseBase

logger = logging.getLogger(__name__)

# 1ページあたりの Select 表示人数（Discord の上限は25）
PAGE_SIZE = 25
# 誰も入らないまま放置された相談VCを片付けるまでの猶予（分）
UNUSED_GRACE_MINUTES = 15


# ---------------------------------------------------------------------- #
# 相手選択（ページング付き Select）
# ---------------------------------------------------------------------- #
class ConsultTargetSelect(discord.ui.Select):
    def __init__(self, view: "ConsultTargetSelectView"):
        page = view.targets[view.page * PAGE_SIZE : (view.page + 1) * PAGE_SIZE]
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id), description=f"@{m.name}")
            for m in page
        ]
        super().__init__(placeholder="相談したい相手を選んでください...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cog: "ConsultVC" = self.view.cog
        target = interaction.guild.get_member(int(self.values[0]))
        if target is None:
            await interaction.response.send_message("❌ そのメンバーが見つかりません。", ephemeral=True)
            return
        await cog.create_room(interaction, target)


class ConsultTargetSelectView(discord.ui.View):
    """相談相手の一覧をページング付きで表示するビュー（ephemeral用）。"""

    def __init__(self, cog: "ConsultVC", targets: list[discord.Member]):
        super().__init__(timeout=180)
        self.cog = cog
        self.targets = targets
        self.page = 0
        self._rebuild()

    @property
    def max_page(self) -> int:
        return (len(self.targets) - 1) // PAGE_SIZE

    def _rebuild(self):
        self.clear_items()
        self.add_item(ConsultTargetSelect(self))
        if self.max_page > 0:
            prev_btn = discord.ui.Button(
                label="◀ 前へ", style=discord.ButtonStyle.gray, disabled=(self.page == 0)
            )
            next_btn = discord.ui.Button(
                label="次へ ▶", style=discord.ButtonStyle.gray, disabled=(self.page >= self.max_page)
            )
            page_label = discord.ui.Button(
                label=f"{self.page + 1} / {self.max_page + 1}",
                style=discord.ButtonStyle.gray,
                disabled=True,
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
# 相談VC内の操作ビュー（永続）
# ---------------------------------------------------------------------- #
class ConsultRoomView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="相談VCを閉じる",
        style=discord.ButtonStyle.red,
        emoji="🚪",
        custom_id="persistent:consult_close",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "ConsultVC" = interaction.client.get_cog("ConsultVC")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        await cog.handle_close(interaction)


# ---------------------------------------------------------------------- #
# 設置パネル（永続）
# ---------------------------------------------------------------------- #
class ConsultPanelActions(discord.ui.ActionRow):
    @discord.ui.button(
        label="相談VCを作る",
        style=discord.ButtonStyle.green,
        emoji="🗣️",
        custom_id="persistent:consult_create",
    )
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "ConsultVC" = interaction.client.get_cog("ConsultVC")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        await cog.handle_create(interaction)


class ConsultPanelView(discord.ui.LayoutView):
    """Components V2 の相談VC作成パネル。"""

    def __init__(self):
        super().__init__(timeout=None)
        large = discord.SeparatorSpacing.large

        def section(text: str):
            container.add_item(discord.ui.Separator(spacing=large))
            container.add_item(discord.ui.TextDisplay(text))

        container = discord.ui.Container(accent_colour=discord.Colour.teal())
        container.add_item(discord.ui.TextDisplay("## 🗣️ 相談VCパネル"))
        container.add_item(discord.ui.Separator(spacing=large))
        container.add_item(
            discord.ui.TextDisplay(
                "**雑談ロール・恋愛ロール** の方が、相手を1人選んで"
                "**2人だけの相談用VC** を作れます。\n"
                "雑談ロール／恋愛ロールのどちらの方とも相談できます。"
            )
        )
        section(
            "### 🔰 使い方\n"
            "- ボタンを押して、相談したい相手を一覧から選んでください。\n"
            "- **2人だけが入れる非公開のVC** が作られ、相手にはBotからDMでご案内します。\n"
            "- 会話は作られたVCのチャットでもできます。"
        )
        section(
            "### 🧹 自動削除について\n"
            "- **全員がVCから抜けると自動的に削除** されます。\n"
            f"- 誰も入らないまま **{UNUSED_GRACE_MINUTES}分** が経過した場合も削除されます。\n"
            "- VCのチャットにある **相談VCを閉じる** ボタンでいつでも終了できます。\n"
            "- 同時に持てる相談VCは **1人1件** までです。"
        )
        container.add_item(discord.ui.Separator(visible=False, spacing=large))
        container.add_item(ConsultPanelActions())
        self.add_item(container)


# ---------------------------------------------------------------------- #
# Cog
# ---------------------------------------------------------------------- #
class ConsultVC(commands.Cog, DatabaseBase):
    """雑談／恋愛ロールの人同士が使える、2人だけの相談用VC。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.zero_romance_role_id = config.ZERO_ROMANCE_ROLE_ID
        self.romance_role_id = config.ROMANCE_ROLE_ID
        self.admin_role_id = config.ADMIN_ROLE_ID
        self.category_id = config.CONSULT_VC_CATEGORY_ID

    async def cog_load(self):
        self._ensure_table()
        self.bot.add_view(ConsultPanelView())
        self.bot.add_view(ConsultRoomView())
        self.sweep.start()

    def cog_unload(self):
        self.sweep.cancel()

    # ------------------------------------------------------------------ #
    # パネル設置コマンド
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="set_consult_panel", description="【管理者専用】相談VCパネルを設置します")
    async def set_consult_panel(self, interaction: discord.Interaction):
        # Components V2（LayoutView）は embed / content と併用不可のため view のみ送信
        await interaction.channel.send(view=ConsultPanelView())
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)

    # ------------------------------------------------------------------ #
    # DB
    # ------------------------------------------------------------------ #
    def _ensure_table(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS consult_vcs (
                            channel_id BIGINT PRIMARY KEY,
                            owner_id BIGINT NOT NULL,
                            partner_id BIGINT NOT NULL,
                            guild_id BIGINT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    conn.commit()
        except Exception as e:
            logger.error(f"consult_vcs テーブルの作成に失敗しました: {e}")

    def _register(self, channel_id: int, owner_id: int, partner_id: int, guild_id: int):
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO consult_vcs (channel_id, owner_id, partner_id, guild_id) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (channel_id) DO NOTHING",
                    (channel_id, owner_id, partner_id, guild_id),
                )
                conn.commit()

    def _unregister(self, channel_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM consult_vcs WHERE channel_id = %s", (channel_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"相談VCの登録解除に失敗しました: {e}")

    def get_room(self, channel_id: int) -> tuple[int, int] | None:
        """(owner_id, partner_id) を返す。相談VCでなければ None。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT owner_id, partner_id FROM consult_vcs WHERE channel_id = %s",
                        (channel_id,),
                    )
                    row = cur.fetchone()
                    return (row[0], row[1]) if row else None
        except Exception as e:
            logger.error(f"相談VCの情報取得に失敗しました: {e}")
            return None

    def count_rooms(self, user_id: int) -> int:
        """その人が作成者になっている相談VCの数。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM consult_vcs WHERE owner_id = %s", (user_id,))
                    return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"相談VC数の取得に失敗しました: {e}")
            return 0

    def _all_rooms(self) -> list[tuple[int, int, int, bool]]:
        """(channel_id, owner_id, partner_id, 猶予時間を過ぎているか) の一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT channel_id, owner_id, partner_id, "
                        "created_at < now() - (%s || ' minutes')::interval FROM consult_vcs",
                        (UNUSED_GRACE_MINUTES,),
                    )
                    return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"相談VC一覧の取得に失敗しました: {e}")
            return []

    # ------------------------------------------------------------------ #
    # 作成
    # ------------------------------------------------------------------ #
    def _can_use(self, member: discord.Member) -> bool:
        """雑談ロールまたは恋愛ロールを持っているか。"""
        role_ids = {r.id for r in member.roles}
        return bool(
            (self.zero_romance_role_id and self.zero_romance_role_id in role_ids)
            or (self.romance_role_id and self.romance_role_id in role_ids)
        )

    async def handle_create(self, interaction: discord.Interaction):
        user = interaction.user

        if not (self.zero_romance_role_id or self.romance_role_id):
            await interaction.response.send_message(
                "❌ ロール設定が不完全です。管理者にお問い合わせください。", ephemeral=True
            )
            return
        if not self._can_use(user):
            await interaction.response.send_message(
                "❌ このボタンは雑談ロール／恋愛ロールをお持ちの方のみ使用できます。", ephemeral=True
            )
            return
        if self.count_rooms(user.id) >= 1:
            await interaction.response.send_message(
                "❌ すでに相談VCを作成しています。先に今の相談VCを閉じてください。", ephemeral=True
            )
            return

        targets = [
            m for m in interaction.guild.members
            if not m.bot and m.id != user.id and self._can_use(m)
        ]
        if not targets:
            await interaction.response.send_message("❌ 現在、相談できる相手がいません。", ephemeral=True)
            return

        targets.sort(key=lambda m: m.display_name)
        await interaction.response.send_message(
            "相談したい相手を選んでください：",
            view=ConsultTargetSelectView(self, targets),
            ephemeral=True,
        )

    async def create_room(self, interaction: discord.Interaction, target: discord.Member):
        guild = interaction.guild
        owner = interaction.user

        # 一覧表示後に状況が変わっている可能性があるため再チェック
        if not self._can_use(target):
            await interaction.response.send_message(
                f"❌ {target.display_name} さんは現在この機能の対象外です。", ephemeral=True
            )
            return
        if self.count_rooms(owner.id) >= 1:
            await interaction.response.send_message(
                "❌ すでに相談VCを作成しています。先に今の相談VCを閉じてください。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        category = guild.get_channel(self.category_id) if self.category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = interaction.channel.category if interaction.channel else None

        admin_role = guild.get_role(self.admin_role_id) if self.admin_role_id else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            owner: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, send_messages=True, stream=True
            ),
            target: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True, send_messages=True, stream=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, send_messages=True, manage_channels=True
            ),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(
                view_channel=True, connect=True, send_messages=True
            )

        name = f"🗣️｜{owner.display_name}×{target.display_name}"[:100]
        try:
            vc = await guild.create_voice_channel(
                name=name, category=category, overwrites=overwrites, reason="相談VCの作成"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"相談VCの作成に失敗しました ({owner.id} × {target.id}): {e}")
            await interaction.followup.send("❌ 相談VCの作成に失敗しました。", ephemeral=True)
            return

        self._register(vc.id, owner.id, target.id, guild.id)

        embed = discord.Embed(
            title="🗣️ 相談VCを作成しました",
            description=(
                f"{owner.mention} さんと {target.mention} さんの相談用VCです。\n"
                "このVCとチャットは、お二人（と管理者）だけが見られます。\n\n"
                "全員がVCから抜けると自動的に削除されます。\n"
                f"誰も入らないまま{UNUSED_GRACE_MINUTES}分経過した場合も削除されます。"
            ),
            color=discord.Color.teal(),
        )
        try:
            await vc.send(
                content=f"{owner.mention} {target.mention}",
                embed=embed,
                view=ConsultRoomView(),
                allowed_mentions=discord.AllowedMentions(users=[owner, target]),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"相談VCへの案内送信に失敗しました: {e}")

        dm_sent = await self._notify_target(guild, owner, target, vc)
        note = "" if dm_sent else "\n⚠️ 相手にDMを送れませんでした。VCのチャットから声をかけてください。"
        await interaction.followup.send(
            f"✅ {vc.mention} を作成しました。{target.display_name} さんとお話しください。{note}",
            ephemeral=True,
        )

    async def _notify_target(
        self,
        guild: discord.Guild,
        owner: discord.Member,
        target: discord.Member,
        vc: discord.VoiceChannel,
    ) -> bool:
        embed = discord.Embed(
            title="🗣️ 相談VCに招待されました",
            description=(
                f"**{owner.display_name}**（@{owner.name}）さんが、あなたとの相談用VCを作成しました。\n"
                f"よければ {vc.mention} に参加してお話ししてください。"
            ),
            color=discord.Color.teal(),
        )
        embed.add_field(name="サーバー", value=guild.name, inline=True)
        embed.set_thumbnail(url=owner.display_avatar.url)
        embed.set_footer(text="参加が難しい場合は、そのままにしていただいて構いません（自動で削除されます）")
        try:
            await target.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    # ------------------------------------------------------------------ #
    # 終了・自動削除
    # ------------------------------------------------------------------ #
    async def handle_close(self, interaction: discord.Interaction):
        channel = interaction.channel
        room = self.get_room(channel.id) if channel else None
        if room is None:
            await interaction.response.send_message("❌ この操作はここでは使えません。", ephemeral=True)
            return

        admin_role = interaction.guild.get_role(self.admin_role_id) if self.admin_role_id else None
        is_participant = interaction.user.id in room
        is_admin = admin_role is not None and admin_role in interaction.user.roles
        if not (is_participant or is_admin):
            await interaction.response.send_message(
                "❌ この相談VCの参加者（または管理者）のみ終了できます。", ephemeral=True
            )
            return

        await interaction.response.send_message("相談VCを閉じます...", ephemeral=True)
        await self._delete_room(channel, f"{interaction.user} が相談VCを終了")

    async def _delete_room(self, channel: discord.abc.GuildChannel, reason: str):
        try:
            await channel.delete(reason=reason)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        self._unregister(channel.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        # VCから抜けた／移動した → 元が相談VCで空なら削除
        if before.channel is None or before.channel == after.channel:
            return
        channel = before.channel
        if self.get_room(channel.id) is None:
            return
        if any(not m.bot for m in channel.members):
            return
        await self._delete_room(channel, "相談VCが空になったため削除")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """参加者がサーバーを抜けたら、その相談VCを片付ける。"""
        if member.bot:
            return
        for channel_id, owner_id, partner_id, _ in self._all_rooms():
            if member.id not in (owner_id, partner_id):
                continue
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                self._unregister(channel_id)
                continue
            await self._delete_room(channel, f"{member} がサーバーを退出したため相談VCを削除")

    # ------------------------------------------------------------------ #
    # 保険：空／未使用の相談VCを定期的に掃除
    # ------------------------------------------------------------------ #
    @tasks.loop(minutes=5.0)
    async def sweep(self):
        for channel_id, _owner_id, _partner_id, expired in self._all_rooms():
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                self._unregister(channel_id)  # 既に消えている
                continue
            if not isinstance(channel, discord.VoiceChannel):
                continue
            if any(not m.bot for m in channel.members):
                continue
            # 誰も入っていない部屋は、作成直後の猶予時間を過ぎていれば削除する
            if expired:
                await self._delete_room(channel, "相談VCが未使用のため削除（定期掃除）")

    @sweep.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ConsultVC(bot))
