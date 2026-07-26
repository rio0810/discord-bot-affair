import discord

# 転送対象とみなす音声ファイルの拡張子
AUDIO_EXTENSIONS = (".mp3", ".ogg", ".wav", ".m4a", ".flac", ".webm", ".oga")


def is_audio(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.startswith("audio"):
        return True
    return attachment.filename.lower().endswith(AUDIO_EXTENSIONS)


# 採点項目：(DBキー, 表示ラベル)。各 0〜2 点。
SCORE_CATEGORIES: list[tuple[str, str]] = [
    ("profile", "プロフとの整合性（0〜2）"),
    ("voice", "聞き取りやすさ・イケボ（0〜2）"),
    ("talk", "トーク力（0〜2）"),
    ("character", "人柄（0〜2）"),
]
# 女性（音声なし）の採点項目：voice / talk を除く
FEMALE_KEYS = {"profile", "character"}


def categories_for(kind: str) -> list[tuple[str, str]]:
    """審査の種別に応じた採点項目。'f'（女性）は音声系を除く。"""
    if kind == "f":
        return [(k, label) for k, label in SCORE_CATEGORIES if k in FEMALE_KEYS]
    return SCORE_CATEGORIES

# 合計平均が「1項目あたり満点2点」換算でこの割合以上なら合格（5/8 = 62.5%）
PASS_THRESHOLD = 5.0


class ScoreModal(discord.ui.Modal, title="プロフィールの採点"):
    def __init__(self, submitter_id: int, message_id: int, kind: str = "m"):
        super().__init__()
        self.submitter_id = submitter_id
        self.message_id = message_id
        self.kind = kind
        self.groups: dict[str, discord.ui.RadioGroup] = {}
        for key, label in categories_for(kind):
            rg = discord.ui.RadioGroup(
                options=[discord.RadioGroupOption(label=str(n), value=str(n)) for n in (0, 1, 2)],
                required=True,
            )
            self.groups[key] = rg
            self.add_item(discord.ui.Label(text=label, component=rg))

        # 0点をつけた場合の理由欄（0点があるのに空なら送信時に弾く）
        self.reason = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
            placeholder="0点をつけた場合は理由を記入してください",
        )
        self.add_item(discord.ui.Label(text="0点をつけた理由（0点がある場合は必須）", component=self.reason))

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RecordingScore")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        scores = {key: int(rg.value) for key, rg in self.groups.items()}
        reason = str(self.reason).strip()
        has_zero = any(v == 0 for v in scores.values())
        if has_zero and not reason:
            await interaction.response.send_message(
                "❌ 0点をつけた項目があります。理由を記入してもう一度採点してください。", ephemeral=True
            )
            return
        if not has_zero and reason:
            await interaction.response.send_message(
                "❌ 0点をつけていない場合は理由欄を空にしてもう一度採点してください。", ephemeral=True
            )
            return
        await cog.submit_score(interaction, self.message_id, self.submitter_id, scores, reason, self.kind)


class ScoreButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"rec_score:(?P<submitter>[0-9]+)(?::(?P<kind>[mf]))?",
):
    def __init__(self, submitter_id: int, kind: str = "m"):
        self.submitter_id = submitter_id
        self.kind = kind
        super().__init__(
            discord.ui.Button(
                label="採点する",
                style=discord.ButtonStyle.green,
                emoji="📝",
                custom_id=f"rec_score:{submitter_id}:{kind}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["submitter"]), match["kind"] or "m")

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RecordingScore")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        # 登録された審査メンバーのみ採点可
        if not cog.is_reviewer(interaction.user.id):
            await interaction.response.send_message(
                "❌ 採点は登録された審査メンバーのみ可能です。", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            ScoreModal(self.submitter_id, interaction.message.id, self.kind)
        )


class ScoreStatusButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"rec_status:(?P<submitter>[0-9]+)",
):
    """この審査の採点状況（審査メンバーの採点済み・未採点）を表示するボタン。"""

    def __init__(self, submitter_id: int):
        self.submitter_id = submitter_id
        super().__init__(
            discord.ui.Button(
                label="採点状況",
                style=discord.ButtonStyle.grey,
                emoji="👥",
                custom_id=f"rec_status:{submitter_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["submitter"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RecordingScore")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        reviewers = cog._list_reviewers()
        if not reviewers:
            await interaction.response.send_message(
                "審査メンバーが登録されていません。", ephemeral=True
            )
            return
        scored = cog.scored_reviewer_ids(interaction.message.id)
        done = [uid for uid in reviewers if uid in scored]
        pending = [uid for uid in reviewers if uid not in scored]

        done_txt = "\n".join(f"- <@{uid}>" for uid in done) or "- （まだいません）"
        pending_txt = "\n".join(f"- <@{uid}>" for uid in pending) or "- （全員採点済み）"
        await interaction.response.send_message(
            f"**採点状況（{len(done)}/{len(reviewers)}人）**\n"
            f"__✅ 採点済み__\n{done_txt}\n\n"
            f"__⏳ 未採点__\n{pending_txt}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def make_score_view(submitter_id: int, kind: str = "m") -> discord.ui.View:
    """審査メッセージに付ける採点ボタン入りのView。"""
    view = discord.ui.View(timeout=None)
    view.add_item(ScoreButton(submitter_id, kind))
    view.add_item(ScoreStatusButton(submitter_id))
    return view


# ---------------------------------------------------------------------- #
# 合否判定（審査結果パネルのボタン → RadioGroup モーダル）
# ---------------------------------------------------------------------- #
class VerdictModal(discord.ui.Modal, title="合否判定"):
    def __init__(self, submitter_id: int):
        super().__init__()
        self.submitter_id = submitter_id
        self.verdict = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(label="✅ 合格", value="pass"),
                discord.RadioGroupOption(label="❌ 不合格", value="fail"),
            ],
            required=True,
        )
        self.add_item(discord.ui.Label(text="合否を選択してください", component=self.verdict))

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RecordingScore")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        await cog.apply_verdict(interaction, self.submitter_id, self.verdict.value)


class VerdictButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"verdict:(?P<submitter>[0-9]+)",
):
    def __init__(self, submitter_id: int):
        self.submitter_id = submitter_id
        super().__init__(
            discord.ui.Button(
                label="合否を出す",
                style=discord.ButtonStyle.blurple,
                emoji="⚖️",
                custom_id=f"verdict:{submitter_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["submitter"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RecordingScore")
        if cog is None:
            await interaction.response.send_message("❌ 現在この機能は利用できません。", ephemeral=True)
            return
        await interaction.response.send_modal(VerdictModal(self.submitter_id))


async def forward_recording(
    forward_channel,
    submitter: discord.abc.User,
    attachments,
    embed: discord.Embed | None = None,
    source_channel=None,
    jump_url: str | None = None,
    kind: str = "m",
):
    """提出された録音を採点ボタン付きで転送チャンネルへ送る（音声投稿・プロフ入力の両方から利用）。

    embed を渡すとそれ（プロフィール等）をそのまま使い、渡さなければ既定の告知embedを作る。
    """
    if embed is None:
        embed = discord.Embed(
            title="📥 アピール録音が提出されました",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="提出者", value=submitter.mention, inline=True)
        if source_channel is not None:
            embed.add_field(name="チャンネル", value=source_channel.mention, inline=True)
        if jump_url:
            embed.add_field(name="元メッセージ", value=f"[ジャンプ]({jump_url})", inline=False)
        embed.set_thumbnail(url=submitter.display_avatar.url)

    view = make_score_view(submitter.id, kind)
    try:
        files = [await a.to_file() for a in attachments]
    except discord.HTTPException as e:
        print(f"[ERROR] 録音ファイルの取得に失敗しました: {e}")
        files = []

    def add_audio_links():
        """アップロード容量超過時：音声を再添付せず、CDNリンクで案内する。"""
        if not attachments:
            return
        links = "\n".join(f"[{a.filename}]({a.url})" for a in attachments)
        embed.add_field(name="🎧 音声ファイル（リンク）", value=links[:1024], inline=False)

    async def _post(files_to_send):
        # フォーラムなら「ユーザー名」で新規ポスト、テキストなら通常メッセージ。
        # 戻り値は (採点対象のmessage_id, 結果を投稿するチャンネル)。
        if isinstance(forward_channel, discord.ForumChannel):
            tm = await forward_channel.create_thread(
                name=getattr(submitter, "display_name", str(submitter))[:100],
                embed=embed, files=files_to_send, view=view,
            )
            return tm.message.id, tm.thread
        else:
            msg = await forward_channel.send(embed=embed, files=files_to_send, view=view)
            return msg.id, forward_channel

    try:
        return await _post(files)
    except discord.HTTPException as e:
        # 413（容量超過）はファイルを外してリンク化して再送
        if e.status == 413:
            print(f"[WARN] 音声が容量上限を超えたためリンクに切り替えます: {e}")
            add_audio_links()
            try:
                return await _post([])
            except (discord.Forbidden, discord.HTTPException) as e2:
                print(f"[ERROR] 審査フォーラムへの投稿に失敗しました（リンク化後）: {e2}")
        else:
            print(f"[ERROR] 審査フォーラムへの投稿に失敗しました: {e}")
    except discord.Forbidden as e:
        print(f"[ERROR] 審査フォーラムへの投稿に失敗しました: {e}")
    return None
