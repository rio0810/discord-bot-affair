import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os

from core.admin_base import AdminCogBase
from core.db_base import DatabaseBase

# 通話部屋テキストchの topic に埋め込む識別子:
#   通常     call_room:<recruiter_id>:<target_id>:<vc_id>
#   お試し   call_room:<recruiter_id>:<target_id>:<vc_id>:trial
#            （VCに最初の人が入ると :start=<unix秒> が付き、警告送信後は :warned が付く）
ROOM_TOPIC_PREFIX = "call_room:"
# 1ページあたりの Select 表示人数（Discord の上限は25）
PAGE_SIZE = 25
# お試し通話の制限時間（分）と、終了前の警告タイミング（残り分数）
TRIAL_DURATION_MINUTES = 30
TRIAL_WARNING_REMAINING = 5


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

        # お試し通話は同じ相手に1回まで（一覧表示後に履歴が増えた場合の再チェック）
        if self.view.trial and target.id in cog.get_trial_invited_ids(interaction.user.id):
            await interaction.response.send_message(
                f"❌ {target.display_name} さんには既にお試し通話のお誘いを送ったことがあるため、再度誘えません。",
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
# CheckboxGroup は1グループ最大10個・Modal は最大5コンポーネント → 一度に扱えるのは50人まで
BLOCK_GROUP_SIZE = 10
BLOCK_MAX_CANDIDATES = 50


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

    @discord.ui.button(label="2人部屋申請", style=discord.ButtonStyle.green, emoji="📞", custom_id="persistent:call_recruit")
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
        container = discord.ui.Container(accent_colour=discord.Colour.pink())
        container.add_item(discord.ui.TextDisplay("## 📞 個通申請パネル"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(
            discord.ui.TextDisplay(
                "⚠️ **新人ロールが付いている間は個通をご利用いただけません。**\n\n"
                "下のボタンから、通話したい相手を選んで個通のお誘いを送れます。"
                "詳細の利用方法は動画をご確認ください。\n\n"
                "## 基本の流れ\n\n"
                "- ボタンを押してお誘いのメッセージを入力すると、**Botが代理で相手にお誘いを送信** し、相手に **受ける / 断る** を選んでもらいます。\n"
                "- 承認されると、**2人だけの専用VC・テキストチャンネル** が作成されます。\n\n"
                "## お試し通話について\n\n"
                f"- VCに入ってから **{TRIAL_DURATION_MINUTES}分で自動終了** します"
                f"- 残り{TRIAL_WARNING_REMAINING}分になると **Botが一時的にVCへ入室し、サウンドボードで通知** します。\n"
                "- お誘いは **同じ相手につき1回まで** です。\n\n"
                "## ブロック・制限機能\n\n"
                "- **ブロック編集** でチェックを入れた相手とは、お互いにお誘い相手の一覧に"
                "表示されなくなります（チェックを外せばいつでも解除可）。\n"
                "- お誘いを **断る** と、その相手は自動でブロックされます（ブロック編集から解除可能）。\n"
                "- **人数制限** で、自分が同時に持てる部屋を **1件** に制限できます"
                "（個通部屋を1件以上持っているときのみ設定可・もう一度押すと解除）。\n"
                f"- 同時に持てる通話部屋は **男性{cog.max_rooms_per_male}件 / "
                f"女性{cog.max_rooms_per_female}件** までです。"
            )
        )
        # 文章とボタンの間の余白（線は表示しない）
        container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large))
        container.add_item(CallPanelActions(cog))
        container.add_item(CallPanelSettingsActions(cog))
        self.add_item(container)


class CallRoomCloseView(discord.ui.View):
    def __init__(self, cog: "CallMatchingCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="個通部屋を削除する", style=discord.ButtonStyle.red, emoji="🚪", custom_id="persistent:call_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_close(interaction)


# ---------------------------------------------------------------------- #
# Cog 本体
# ---------------------------------------------------------------------- #
class CallMatchingCog(commands.Cog, DatabaseBase):
    """男性⇄女性がお互いに1対1通話を募集し、相手が DM で承認/拒否するマッチング機能。
    男性がボタンを押すと女性一覧、女性が押すと男性一覧が表示される。"""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.male_role_id = int(os.getenv("MALE_ROLE_ID", "0"))
        self.female_role_id = int(os.getenv("FEMALE_ROLE_ID", "0"))
        self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID", "0"))
        # 新人ロール保持者は個通を利用できない（未設定なら制限なし）
        self.newcomer_role_id = int(os.getenv("NEWCOMER_ROLE_ID") or "0")
        self.category_id = int(os.getenv("CALL_CATEGORY_ID", "0"))
        # 通話マッチングのログ先（未設定ならログは送られない）
        self.log_channel_id = int(os.getenv("CALL_LOG_CHANNEL_ID", "0"))
        self.max_rooms_per_female = int(os.getenv("MAX_ROOMS_PER_FEMALE", "2"))
        self.max_rooms_per_male = int(os.getenv("MAX_ROOMS_PER_MALE", "2"))
        # お試し通話の残り5分でVCに鳴らすサウンドボード（ID または 名前。未設定なら鳴らさない）
        self.trial_warning_sound = os.getenv("TRIAL_WARNING_SOUND", "").strip()
        self.trial_watcher.start()

    def cog_unload(self):
        self.trial_watcher.cancel()

    async def cog_load(self):
        # 再起動後もボタンが反応するよう登録
        self.bot.add_view(CallPanelView(self))
        self.bot.add_view(CallRoomCloseView(self))
        self.bot.add_dynamic_items(CallRequestButton)
        self._ensure_tables()

    # ------------------------------------------------------------------ #
    # テーブル作成（init.sql は新規ボリューム時しか走らないため起動時にも作る）
    # ------------------------------------------------------------------ #
    def _ensure_tables(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS trial_invites (
                            recruiter_id BIGINT NOT NULL,
                            target_id BIGINT NOT NULL,
                            invited_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (recruiter_id, target_id)
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS call_blocks (
                            blocker_id BIGINT NOT NULL,
                            blocked_id BIGINT NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (blocker_id, blocked_id)
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS call_room_limits (
                            user_id BIGINT PRIMARY KEY,
                            max_rooms INT NOT NULL
                        )
                    """)
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] テーブルの作成に失敗しました: {e}")

    def get_trial_invited_ids(self, recruiter_id: int) -> set[int]:
        """recruiter が過去にお試し通話へ誘った相手の ID 一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT target_id FROM trial_invites WHERE recruiter_id = %s",
                        (recruiter_id,),
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception as e:
            print(f"[ERROR] お試し通話履歴の取得に失敗しました: {e}")
            return set()

    def record_trial_invite(self, recruiter_id: int, target_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO trial_invites (recruiter_id, target_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (recruiter_id, target_id),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] お試し通話履歴の記録に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # ブロック（お互いにお誘い相手一覧へ表示されなくなる）
    # ------------------------------------------------------------------ #
    def get_blocked_ids(self, blocker_id: int) -> set[int]:
        """自分がブロックしている相手の ID 一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT blocked_id FROM call_blocks WHERE blocker_id = %s", (blocker_id,)
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception as e:
            print(f"[ERROR] ブロック一覧の取得に失敗しました: {e}")
            return set()

    def get_blockers_of(self, user_id: int) -> set[int]:
        """自分をブロックしている相手の ID 一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT blocker_id FROM call_blocks WHERE blocked_id = %s", (user_id,)
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception as e:
            print(f"[ERROR] 被ブロック一覧の取得に失敗しました: {e}")
            return set()

    def is_blocked_between(self, user_a: int, user_b: int) -> bool:
        """どちらかがどちらかをブロックしているか。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM call_blocks WHERE (blocker_id = %s AND blocked_id = %s) "
                        "OR (blocker_id = %s AND blocked_id = %s) LIMIT 1",
                        (user_a, user_b, user_b, user_a),
                    )
                    return cur.fetchone() is not None
        except Exception as e:
            print(f"[ERROR] ブロック状態の確認に失敗しました: {e}")
            return False

    def add_block(self, blocker_id: int, blocked_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO call_blocks (blocker_id, blocked_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (blocker_id, blocked_id),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] ブロックの追加に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 個人ごとの部屋数上限（パネルのボタンで 1 件に制限 ⇔ 解除）
    # ------------------------------------------------------------------ #
    def get_room_limit(self, user_id: int) -> int | None:
        """個人設定の上限。未設定なら None（ロール別のデフォルトを使う）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT max_rooms FROM call_room_limits WHERE user_id = %s", (user_id,)
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            print(f"[ERROR] 部屋数上限の取得に失敗しました: {e}")
            return None

    def get_all_room_limits(self) -> dict[int, int]:
        """個人設定の上限を一括取得（一覧フィルタ用）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id, max_rooms FROM call_room_limits")
                    return {row[0]: row[1] for row in cur.fetchall()}
        except Exception as e:
            print(f"[ERROR] 部屋数上限の一括取得に失敗しました: {e}")
            return {}

    def set_room_limit(self, user_id: int, max_rooms: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO call_room_limits (user_id, max_rooms) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET max_rooms = EXCLUDED.max_rooms",
                        (user_id, max_rooms),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 部屋数上限の設定に失敗しました: {e}")

    def clear_room_limit(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM call_room_limits WHERE user_id = %s", (user_id,))
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] 部屋数上限の解除に失敗しました: {e}")

    def get_all_blocks(self) -> list[tuple[int, int]]:
        """全ブロック関係 (blocker_id, blocked_id) の一覧（登録順）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT blocker_id, blocked_id FROM call_blocks ORDER BY created_at"
                    )
                    return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as e:
            print(f"[ERROR] ブロック全件の取得に失敗しました: {e}")
            return []

    def remove_block(self, blocker_id: int, blocked_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM call_blocks WHERE blocker_id = %s AND blocked_id = %s",
                        (blocker_id, blocked_id),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ERROR] ブロックの解除に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # パネル設置コマンド
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="set_call_panel", description="【管理者専用】個通募集パネルを設置します")
    async def set_call_panel(self, interaction: discord.Interaction):
        # Components V2（LayoutView）は embed / content と併用不可のため view のみ送信
        await interaction.channel.send(view=CallPanelView(self))
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)

    # ------------------------------------------------------------------ #
    # 新人ロール保持者の一覧（管理者専用）
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="newcomer_list", description="【管理者専用】新人ロールを持つメンバーを一覧表示します")
    async def newcomer_list(self, interaction: discord.Interaction):
        if not self.newcomer_role_id:
            await interaction.response.send_message(
                "新人ロール（NEWCOMER_ROLE_ID）が設定されていません。", ephemeral=True
            )
            return
        role = interaction.guild.get_role(self.newcomer_role_id)
        if role is None:
            await interaction.response.send_message("新人ロールが見つかりません。", ephemeral=True)
            return

        members = sorted(role.members, key=lambda m: m.joined_at or discord.utils.utcnow())
        if not members:
            await interaction.response.send_message(f"「{role.name}」を持つメンバーはいません。", ephemeral=True)
            return

        lines = []
        length = 0
        omitted = 0
        for i, m in enumerate(members, 1):
            line = f"`{i:02d}.` {m.mention}"
            if length + len(line) + 1 > 3900:
                omitted = len(members) - len(lines)
                break
            lines.append(line)
            length += len(line) + 1
        if omitted:
            lines.append(f"…他 **{omitted}名**")

        embed = discord.Embed(
            title=f"🔰 {role.name} 一覧",
            description="\n".join(lines),
            color=role.color if role.color.value else discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"合計 {len(members)}名")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # ブロック状況の確認（管理者専用）
    # ------------------------------------------------------------------ #
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_any_role(AdminCogBase.ADMIN_ROLE_ID)
    @app_commands.command(name="admin_block_list", description="【管理者専用】個通のブロック状況を確認します")
    @app_commands.describe(member="指定すると、そのメンバーに関係するブロックだけ表示します")
    async def admin_block_list(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        await interaction.response.defer(ephemeral=True)

        blocks = self.get_all_blocks()
        if member is not None:
            blocks = [b for b in blocks if member.id in b]

        if not blocks:
            suffix = f"（{member.display_name} さん関係）" if member else ""
            await interaction.followup.send(f"現在、登録されているブロックはありません{suffix}。")
            return

        guild = interaction.guild

        def display(uid: int) -> str:
            m = guild.get_member(uid)
            return f"{m.display_name}（@{m.name}）" if m else f"退出済み（{uid}）"

        lines = [f"`{i:02d}.` 🚫 **{display(b)}** → {display(t)}" for i, (b, t) in enumerate(blocks, 1)]

        # embed の description 上限（4096字）に収まるよう調整
        description_lines = []
        length = 0
        omitted = 0
        for line in lines:
            if length + len(line) + 1 > 3900:
                omitted = len(lines) - len(description_lines)
                break
            description_lines.append(line)
            length += len(line) + 1
        if omitted:
            description_lines.append(f"…他 **{omitted}件**")

        title = "📜 個通ブロック一覧"
        if member is not None:
            title += f"：{member.display_name} さん関係"
        embed = discord.Embed(
            title=title,
            description="\n".join(description_lines),
            color=discord.Color.dark_gray(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"合計 {len(blocks)} 件（🚫 ブロックした人 → された人）")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------ #
    # 募集開始（男性⇄女性）
    # ------------------------------------------------------------------ #
    def _is_newcomer(self, member: discord.Member) -> bool:
        return bool(self.newcomer_role_id) and any(r.id == self.newcomer_role_id for r in member.roles)

    async def handle_recruit(self, interaction: discord.Interaction, trial: bool = False):
        guild = interaction.guild
        user = interaction.user

        # 新人ロールの間は個通を利用できない
        if self._is_newcomer(user):
            await interaction.response.send_message(
                "❌ 新人ロールが付いている間は個通をご利用いただけません。", ephemeral=True
            )
            return

        male_role = guild.get_role(self.male_role_id)
        female_role = guild.get_role(self.female_role_id)
        if male_role is None or female_role is None:
            await interaction.response.send_message("❌ ロール設定が不完全です。管理者にお問い合わせください。", ephemeral=True)
            return

        # 押した人のロールから相手側のロールを決定
        if male_role in user.roles:
            target_role = female_role
        elif female_role in user.roles:
            target_role = male_role
        else:
            await interaction.response.send_message(
                "❌ このボタンは男性/女性ロール保持者のみ使用できます。", ephemeral=True
            )
            return

        limit = self.max_rooms_for(user)
        if self.count_rooms(guild, user.id) >= limit:
            await interaction.response.send_message(
                f"❌ あなたは既に通話部屋の上限（{limit}件）に達しています。"
                "既存の部屋を終了してから募集してください。",
                ephemeral=True,
            )
            return

        # ブロック関係にある相手（自分がブロック／自分をブロック）は双方向で除外
        block_ids = self.get_blocked_ids(user.id) | self.get_blockers_of(user.id)
        # 部屋数が上限に達している人は一覧に表示しない（個人設定の上限を優先）
        room_counts = self.count_all_rooms(guild)
        personal_limits = self.get_all_room_limits()
        targets = [
            m for m in guild.members
            if target_role in m.roles and not m.bot
            and m.id not in block_ids
            and not self._is_newcomer(m)
            and room_counts.get(m.id, 0) < personal_limits.get(m.id, self.default_max_rooms(m))
        ]
        if trial:
            # 一度お試し通話に誘った相手は一覧から除外
            invited = self.get_trial_invited_ids(user.id)
            targets = [m for m in targets if m.id not in invited]
        if not targets:
            await interaction.response.send_message("❌ 現在、誘える相手がいません。", ephemeral=True)
            return

        view = TargetSelectView(self, targets, trial=trial)
        label = f"お試し通話（{TRIAL_DURATION_MINUTES}分）" if trial else "通話"
        await interaction.response.send_message(f"{label}に誘う相手を選んでください：", view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # お誘い送信（Modal 送信後）
    # ------------------------------------------------------------------ #
    async def send_request(
        self, interaction: discord.Interaction, target: discord.Member, message: str, trial: bool = False
    ):
        guild = interaction.guild
        recruiter = interaction.user

        title = f"⏳ お試し通話（{TRIAL_DURATION_MINUTES}分）のお誘い" if trial else "📞 1対1通話のお誘い"
        embed = discord.Embed(
            title=title,
            description=message,
            color=discord.Color.pink(),
        )
        embed.add_field(name="募集者", value=f"{recruiter.display_name}（@{recruiter.name}）", inline=True)
        embed.add_field(name="サーバー", value=guild.name, inline=True)
        if trial:
            embed.add_field(
                name="制限時間",
                value=f"VC に入ってから **{TRIAL_DURATION_MINUTES}分** で自動終了します",
                inline=False,
            )
        embed.set_thumbnail(url=recruiter.display_avatar.url)
        embed.set_footer(text="下のボタンで「受ける / 断る」を選んでください")

        view = discord.ui.View(timeout=None)
        view.add_item(CallRequestButton("accept", recruiter.id, guild.id, trial=trial))
        view.add_item(CallRequestButton("decline", recruiter.id, guild.id, trial=trial))

        try:
            await target.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ {target.display_name} さんに DM を送れませんでした（DMが閉じられている可能性があります）。",
                ephemeral=True,
            )
            return

        if trial:
            # DM 送信に成功した時点で履歴に記録（同じ相手は2度誘えない）
            self.record_trial_invite(recruiter.id, target.id)

        await interaction.response.send_message(
            f"✅ {target.display_name} さんにお誘いを送りました。返事をお待ちください。", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # 承認（相手の DM ボタン）
    # ------------------------------------------------------------------ #
    async def handle_accept(
        self, interaction: discord.Interaction, recruiter_id: int, guild_id: int, trial: bool = False
    ):
        await interaction.response.defer()

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            await interaction.followup.send("❌ サーバーが見つかりませんでした。")
            return

        recruiter = guild.get_member(recruiter_id)
        target = guild.get_member(interaction.user.id)
        if recruiter is None or target is None:
            await interaction.followup.send("❌ 相手（またはあなた）がサーバーにいないため、成立できませんでした。")
            await self._disable_dm_buttons(interaction)
            return

        # 新人ロールの間は個通を利用できない
        if self._is_newcomer(target) or self._is_newcomer(recruiter):
            await interaction.followup.send("❌ 新人ロールが付いている間は個通をご利用いただけません。")
            await self._disable_dm_buttons(interaction)
            return

        # お誘い送信後にブロック関係になっていたら成立させない
        if self.is_blocked_between(recruiter.id, target.id):
            await interaction.followup.send("❌ このお誘いは現在受けられません。")
            await self._disable_dm_buttons(interaction)
            return

        # 上限の再チェック（承認までの間に埋まっている可能性がある）
        target_limit = self.max_rooms_for(target)
        if self.count_rooms(guild, target.id) >= target_limit:
            await interaction.followup.send(
                f"❌ あなたは既に通話部屋の上限（{target_limit}件）に達しています。"
            )
            return
        if self.count_rooms(guild, recruiter.id) >= self.max_rooms_for(recruiter):
            await interaction.followup.send(
                f"❌ {recruiter.display_name} さんが通話部屋の上限に達しているため、成立できませんでした。"
            )
            await self._disable_dm_buttons(interaction)
            return

        room = await self._create_call_room(guild, recruiter, target, trial=trial)
        if room is None:
            await interaction.followup.send("❌ 部屋の作成に失敗しました。管理者にお問い合わせください。")
            return
        text_channel, voice_channel = room

        await self._disable_dm_buttons(interaction)
        await interaction.followup.send(f"✅ 通話が成立しました！ {text_channel.mention} へどうぞ。")

        # 募集者へ DM 通知
        try:
            await recruiter.send(f"✅ {target.display_name} さんが通話を承認しました！ {text_channel.mention} へどうぞ。")
        except discord.Forbidden:
            pass

        await self._send_log(
            "✅ お試し通話成立" if trial else "✅ 通話成立",
            recruiter, target, discord.Color.green(),
            extra=f"部屋: {text_channel.mention} / {voice_channel.mention}",
            request_message=self._extract_request_message(interaction),
        )

    # ------------------------------------------------------------------ #
    # 拒否（相手の DM ボタン）
    # ------------------------------------------------------------------ #
    async def handle_decline(
        self, interaction: discord.Interaction, recruiter_id: int, guild_id: int, trial: bool = False
    ):
        await interaction.response.defer()

        guild = self.bot.get_guild(guild_id)
        recruiter = guild.get_member(recruiter_id) if guild else None

        # 断った側が募集者を自動ブロック（以後お互いにお誘い一覧へ表示されない）
        self.add_block(interaction.user.id, recruiter_id)

        await self._disable_dm_buttons(interaction)
        await interaction.followup.send(
            "お誘いをお断りしました。\n"
            "この相手は自動でブロックされ、今後お互いにお誘い相手の一覧へ表示されません"
            "（パネルの 🚫 ブロック編集からいつでも解除できます）。"
        )

        if recruiter is not None:
            try:
                await recruiter.send(f"❌ {interaction.user.display_name} さんは今回のお誘いを見送りました。")
            except discord.Forbidden:
                pass

        if guild is not None:
            target = guild.get_member(interaction.user.id)
            await self._send_log(
                "❌ お試し通話不成立（お断り）" if trial else "❌ 通話不成立（お断り）",
                recruiter, target or interaction.user, discord.Color.red(),
                request_message=self._extract_request_message(interaction),
            )

    # ------------------------------------------------------------------ #
    # 人数制限トグル（パネルのボタン：上限1件 ⇔ 解除）
    # ------------------------------------------------------------------ #
    async def handle_room_limit_toggle(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        male_role = guild.get_role(self.male_role_id)
        female_role = guild.get_role(self.female_role_id)
        if not ((male_role and male_role in user.roles) or (female_role and female_role in user.roles)):
            await interaction.response.send_message(
                "❌ このボタンは男性/女性ロール保持者のみ使用できます。", ephemeral=True
            )
            return

        current = self.get_room_limit(user.id)
        if current == 1:
            # 解除は部屋数に関係なく常に可能（部屋が閉じた後に解除できなくなるのを防ぐ）
            self.clear_room_limit(user.id)
            default = self.default_max_rooms(user)
            await interaction.response.send_message(
                f"🔓 人数制限を解除しました。同時に持てる個通部屋は通常の **{default}件** に戻ります。",
                ephemeral=True,
            )
        else:
            # 制限の設定は、個通部屋を1件以上持っている人のみ
            if self.count_rooms(guild, user.id) < 1:
                await interaction.response.send_message(
                    "❌ このボタンは、個通部屋を **1件以上持っているとき** のみ使用できます。",
                    ephemeral=True,
                )
                return
            self.set_room_limit(user.id, 1)
            await interaction.response.send_message(
                "🔒 同時に持てる個通部屋を **1件** に制限しました。\n"
                "・制限中は、あなたから新しく申請できず、相手の一覧にも表示されません。\n"
                "・もう一度このボタンを押すと解除できます。",
                ephemeral=True,
            )

    # ------------------------------------------------------------------ #
    # ブロック編集（パネルのボタン → チェックボックス Modal）
    # ------------------------------------------------------------------ #
    async def handle_block_edit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        # 新人ロールの間は個通（ブロック編集含む）を利用できない
        if self._is_newcomer(user):
            await interaction.response.send_message(
                "❌ 新人ロールが付いている間は個通をご利用いただけません。", ephemeral=True
            )
            return

        male_role = guild.get_role(self.male_role_id)
        female_role = guild.get_role(self.female_role_id)
        if male_role is None or female_role is None:
            await interaction.response.send_message("❌ ロール設定が不完全です。管理者にお問い合わせください。", ephemeral=True)
            return

        # 押した人のロールから相手側のロールを決定（マッチング対象＝ブロック対象）
        if male_role in user.roles:
            target_role = female_role
        elif female_role in user.roles:
            target_role = male_role
        else:
            await interaction.response.send_message(
                "❌ このボタンは男性/女性ロール保持者のみ使用できます。", ephemeral=True
            )
            return

        blocked = self.get_blocked_ids(user.id)

        # ブロック中を先頭に、その後は名前順。退出済みのブロック相手も解除できるように含める
        members = sorted(
            (m for m in guild.members if target_role in m.roles and not m.bot),
            key=lambda m: (m.id not in blocked, m.display_name),
        )
        left_blocked = [uid for uid in sorted(blocked) if guild.get_member(uid) is None]
        candidates: list[tuple[int, str, str | None]] = [
            (uid, f"退出済みユーザー（{uid}）", None) for uid in left_blocked
        ] + [(m.id, m.display_name, f"@{m.name}") for m in members]

        if not candidates:
            await interaction.response.send_message("❌ ブロックできる相手がいません。", ephemeral=True)
            return

        # Modal の上限（5グループ×10人）に収める。ブロック中は先頭に並ぶため必ず編集可能
        candidates = candidates[:BLOCK_MAX_CANDIDATES]

        await interaction.response.send_modal(BlockEditModal(self, user.id, candidates, blocked))

    # ------------------------------------------------------------------ #
    # 部屋の終了
    # ------------------------------------------------------------------ #
    async def handle_close(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not (channel.topic or "").startswith(ROOM_TOPIC_PREFIX):
            await interaction.response.send_message("❌ この操作はここでは使えません。", ephemeral=True)
            return

        parts = channel.topic.split(":")  # call_room:<recruiter>:<target>:<vc_id>
        member_ids = set(parts[1:3])
        admin_role = interaction.guild.get_role(self.admin_role_id)
        is_participant = str(interaction.user.id) in member_ids
        is_admin = admin_role is not None and admin_role in interaction.user.roles
        if not (is_participant or is_admin):
            await interaction.response.send_message("❌ この部屋の参加者または管理者のみ終了できます。", ephemeral=True)
            return

        await interaction.response.send_message("部屋を終了します...", ephemeral=True)

        vc = interaction.guild.get_channel(int(parts[3])) if len(parts) > 3 and parts[3].isdigit() else None
        for ch in (vc, channel):
            if ch is not None:
                try:
                    await ch.delete(reason=f"{interaction.user} が通話部屋を終了")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

    # ------------------------------------------------------------------ #
    # ヘルパー
    # ------------------------------------------------------------------ #
    def default_max_rooms(self, member: discord.Member) -> int:
        """ロールに応じたデフォルトの部屋数上限。"""
        if any(r.id == self.female_role_id for r in member.roles):
            return self.max_rooms_per_female
        return self.max_rooms_per_male

    def max_rooms_for(self, member: discord.Member) -> int:
        """メンバーの部屋数上限（個人設定があればそちらを優先）。"""
        personal = self.get_room_limit(member.id)
        return personal if personal is not None else self.default_max_rooms(member)

    def count_rooms(self, guild: discord.Guild, user_id: int) -> int:
        """topic からそのユーザーが参加中の通話部屋数を数える（再起動後も正確）。"""
        count = 0
        for ch in guild.text_channels:
            if ch.topic and ch.topic.startswith(ROOM_TOPIC_PREFIX):
                parts = ch.topic.split(":")
                if str(user_id) in parts[1:3]:
                    count += 1
        return count

    def count_all_rooms(self, guild: discord.Guild) -> dict[int, int]:
        """全メンバーの通話部屋数を一括集計（一覧のフィルタ用にチャンネル走査を1回で済ませる）。"""
        counts: dict[int, int] = {}
        for ch in guild.text_channels:
            if ch.topic and ch.topic.startswith(ROOM_TOPIC_PREFIX):
                for pid in ch.topic.split(":")[1:3]:
                    if pid.isdigit():
                        counts[int(pid)] = counts.get(int(pid), 0) + 1
        return counts

    async def _create_call_room(
        self, guild: discord.Guild, recruiter: discord.Member, target: discord.Member, trial: bool = False
    ) -> tuple[discord.TextChannel, discord.VoiceChannel] | None:
        admin_role = guild.get_role(self.admin_role_id)
        category = guild.get_channel(self.category_id) if self.category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            recruiter: discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True, speak=True),
            target: discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True, speak=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True, manage_channels=True),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, connect=True, send_messages=True)

        vc_emoji = "⏳" if trial else "📞"
        topic = f"{ROOM_TOPIC_PREFIX}{recruiter.id}:{target.id}"
        try:
            voice_channel = await guild.create_voice_channel(
                name=f"{vc_emoji}｜{recruiter.display_name}×{target.display_name}",
                category=category,
                overwrites=overwrites,
            )
            text_channel = await guild.create_text_channel(
                name=f"💬｜{recruiter.display_name}×{target.display_name}",
                category=category,
                overwrites=overwrites,
                topic=f"{topic}:{voice_channel.id}" + (":trial" if trial else ""),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 通話部屋の作成に失敗しました ({recruiter.id} × {target.id}): {e}")
            return None

        description = (
            f"{recruiter.mention} さんと {target.mention} さんの専用部屋です。\n\n"
            f"通話は {voice_channel.mention} でどうぞ。\n"
            "終わったら下のボタンで部屋を終了してください。"
        )
        if trial:
            description += (
                f"\n\n⏳ **この部屋はお試し通話です。VCに入ってから{TRIAL_DURATION_MINUTES}分で自動終了します**"
                f"（残り{TRIAL_WARNING_REMAINING}分で通知します）。"
            )
        embed = discord.Embed(
            title=f"⏳ お試し通話部屋（{TRIAL_DURATION_MINUTES}分）" if trial else "📞 1対1通話部屋",
            description=description,
            color=discord.Color.pink(),
        )
        await text_channel.send(
            content=f"{recruiter.mention} {target.mention}", embed=embed, view=CallRoomCloseView(self)
        )
        return text_channel, voice_channel

    async def _disable_dm_buttons(self, interaction: discord.Interaction):
        """処理済みの DM からボタンを取り除き、二重操作を防ぐ。"""
        try:
            await interaction.message.edit(view=None)
        except (discord.NotFound, discord.HTTPException):
            pass

    @staticmethod
    def _extract_request_message(interaction: discord.Interaction) -> str | None:
        """DM のお誘い embed から本文を取り出す（ログ用）。"""
        if interaction.message and interaction.message.embeds:
            return interaction.message.embeds[0].description
        return None

    async def _send_log(
        self,
        title: str,
        recruiter: discord.Member | None,
        target: discord.abc.User | None,
        color: discord.Color,
        extra: str | None = None,
        request_message: str | None = None,
    ):
        channel = self.bot.get_channel(self.log_channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            print(f"⚠️ [CallMatching] ログチャンネル {self.log_channel_id} が見つかりません。")
            return

        embed = discord.Embed(title=f"📜 通話マッチング: {title}", color=color, timestamp=discord.utils.utcnow())
        embed.add_field(name="募集者", value=recruiter.mention if recruiter else "不明", inline=True)
        embed.add_field(name="相手", value=target.mention if target else "不明", inline=True)
        if request_message:
            embed.add_field(name="メッセージ", value=request_message[:1024], inline=False)
        if extra:
            embed.add_field(name="詳細", value=extra, inline=False)
        await channel.send(embed=embed)

    # ------------------------------------------------------------------ #
    # お試し通話の時間監視（残り5分で警告 → 30分で強制終了）
    # ------------------------------------------------------------------ #
    @tasks.loop(minutes=1.0)
    async def trial_watcher(self):
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            for ch in list(guild.text_channels):
                if not (ch.topic and ch.topic.startswith(ROOM_TOPIC_PREFIX)):
                    continue
                parts = ch.topic.split(":")  # call_room:<r>:<t>:<vc>:trial[:warned]
                if "trial" not in parts[4:]:
                    continue

                # タイマーは VC に最初の人が入った時点（start=<unix秒>）から起算
                start_ts = next((p[len("start="):] for p in parts if p.startswith("start=")), None)
                if start_ts is None or not start_ts.isdigit():
                    continue  # まだ誰も VC に入っていない

                elapsed_min = (now.timestamp() - int(start_ts)) / 60
                recruiter = guild.get_member(int(parts[1])) if parts[1].isdigit() else None
                target = guild.get_member(int(parts[2])) if parts[2].isdigit() else None

                if elapsed_min >= TRIAL_DURATION_MINUTES:
                    # 制限時間到達 → 部屋を強制終了
                    vc = guild.get_channel(int(parts[3])) if len(parts) > 3 and parts[3].isdigit() else None
                    for c in (vc, ch):
                        if c is not None:
                            try:
                                await c.delete(reason=f"お試し通話の制限時間（{TRIAL_DURATION_MINUTES}分）が経過")
                            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                                pass
                    await self._send_log(
                        f"⏰ お試し通話終了（{TRIAL_DURATION_MINUTES}分経過）",
                        recruiter, target, discord.Color.orange(),
                    )
                elif elapsed_min >= TRIAL_DURATION_MINUTES - TRIAL_WARNING_REMAINING and "warned" not in parts:
                    # 残り5分 → 2人にメンションで警告し、VCにサウンドを鳴らし、topic に警告済みフラグを付ける
                    mentions = " ".join(m.mention for m in (recruiter, target) if m is not None)
                    try:
                        await ch.send(
                            f"{mentions} ⏳ このお試し通話は**あと約{TRIAL_WARNING_REMAINING}分**で自動終了します。"
                        )
                        await ch.edit(topic=ch.topic + ":warned")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    vc_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
                    await self._play_warning_sound(guild, vc_id)

    @trial_watcher.before_loop
    async def before_trial_watcher(self):
        await self.bot.wait_until_ready()

    async def _resolve_warning_sound(self, guild: discord.Guild):
        """TRIAL_WARNING_SOUND（ID または 名前）からサーバーのサウンドボード音源を探す。"""
        if not self.trial_warning_sound:
            return None
        try:
            sounds = await guild.fetch_soundboard_sounds()
        except discord.HTTPException as e:
            print(f"⚠️ [CallMatching] サウンドボード一覧の取得に失敗: {e}")
            return None
        if self.trial_warning_sound.isdigit():
            return discord.utils.get(sounds, id=int(self.trial_warning_sound))
        return discord.utils.get(sounds, name=self.trial_warning_sound)

    async def _play_warning_sound(self, guild: discord.Guild, vc_id: int):
        """お試し通話のVCに残り5分のサウンドを鳴らす（Botが一時的にVCへ参加して送信）。"""
        if not self.trial_warning_sound or not vc_id:
            return
        channel = guild.get_channel(vc_id)
        # 誰もいないVCには鳴らさない
        if not isinstance(channel, discord.VoiceChannel) or not any(not m.bot for m in channel.members):
            return
        # 既にどこかのVCに接続中なら競合を避けてスキップ
        if guild.voice_client is not None:
            return

        sound = await self._resolve_warning_sound(guild)
        if sound is None:
            print(f"⚠️ [CallMatching] サウンド '{self.trial_warning_sound}' が見つかりません。")
            return

        voice_client = None
        try:
            # サウンド送信には非ミュート・非スピーカー抑制での接続が必要
            voice_client = await channel.connect(timeout=10.0, self_deaf=False, self_mute=False)
            await channel.send_sound(sound)
            await asyncio.sleep(5)  # 再生が終わる前に切断しないよう少し待つ
        except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as e:
            print(f"⚠️ [CallMatching] サウンド再生に失敗: {e}")
        finally:
            if voice_client is not None:
                try:
                    await voice_client.disconnect(force=True)
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """お試し通話の VC に最初の人が入ったらタイマーを開始する。"""
        if member.bot or after.channel is None or before.channel == after.channel:
            return

        vc = after.channel
        for ch in vc.guild.text_channels:
            if not (ch.topic and ch.topic.startswith(ROOM_TOPIC_PREFIX)):
                continue
            parts = ch.topic.split(":")
            if len(parts) <= 4 or parts[3] != str(vc.id) or "trial" not in parts[4:]:
                continue
            if any(p.startswith("start=") for p in parts):
                break  # 既にタイマー開始済み

            start_ts = int(discord.utils.utcnow().timestamp())
            try:
                await ch.edit(topic=ch.topic + f":start={start_ts}")
                await ch.send(
                    f"⏳ お試し通話を開始しました。**{TRIAL_DURATION_MINUTES}分後**に自動終了します"
                    f"（残り{TRIAL_WARNING_REMAINING}分で通知します）。"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            break

    # ------------------------------------------------------------------ #
    # 退出時：参加していた通話部屋を掃除
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        for ch in list(guild.text_channels):
            if ch.topic and ch.topic.startswith(ROOM_TOPIC_PREFIX):
                parts = ch.topic.split(":")
                if str(member.id) not in parts[1:3]:
                    continue
                vc = guild.get_channel(int(parts[3])) if len(parts) > 3 and parts[3].isdigit() else None
                for target in (vc, ch):
                    if target is not None:
                        try:
                            await target.delete(reason=f"{member} がサーバーを退出したため通話部屋を削除")
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CallMatchingCog(bot))
