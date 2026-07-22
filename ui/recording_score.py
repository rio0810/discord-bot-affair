import os

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

# 何人が採点したら結果を出すか（環境変数で変更可・既定4人）
SCORE_REVIEWER_COUNT = int(os.getenv("SCORE_REVIEWER_COUNT") or "4")
# 合計平均がこの点数以上なら合格（8点満点）
PASS_THRESHOLD = 5.0


class ScoreModal(discord.ui.Modal, title="録音の採点"):
    def __init__(self, submitter_id: int, message_id: int):
        super().__init__()
        self.submitter_id = submitter_id
        self.message_id = message_id
        self.groups: dict[str, discord.ui.RadioGroup] = {}
        for key, label in SCORE_CATEGORIES:
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
        await cog.submit_score(interaction, self.message_id, self.submitter_id, scores, reason)


class ScoreButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"rec_score:(?P<submitter>[0-9]+)",
):
    def __init__(self, submitter_id: int):
        self.submitter_id = submitter_id
        super().__init__(
            discord.ui.Button(
                label="採点する",
                style=discord.ButtonStyle.green,
                emoji="📝",
                custom_id=f"rec_score:{submitter_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["submitter"]))

    async def callback(self, interaction: discord.Interaction):
        # （検証用）自分の録音も採点可能にしている
        await interaction.response.send_modal(ScoreModal(self.submitter_id, interaction.message.id))


def make_score_view(submitter_id: int) -> discord.ui.View:
    """録音メッセージに付ける採点ボタン入りのView。"""
    view = discord.ui.View(timeout=None)
    view.add_item(ScoreButton(submitter_id))
    return view


async def forward_recording(
    forward_channel,
    submitter: discord.abc.User,
    attachments,
    embed: discord.Embed | None = None,
    source_channel=None,
    jump_url: str | None = None,
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

    view = make_score_view(submitter.id)
    try:
        files = [await a.to_file() for a in attachments]
    except discord.HTTPException as e:
        print(f"[ERROR] 録音ファイルの取得に失敗しました: {e}")
        files = []

    # フォーラムなら「ユーザー名」で新規ポストを作成、テキストなら通常メッセージ
    if isinstance(forward_channel, discord.ForumChannel):
        try:
            await forward_channel.create_thread(
                name=getattr(submitter, "display_name", str(submitter))[:100],
                embed=embed,
                files=files,
                view=view,
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[ERROR] 審査フォーラムへの投稿に失敗しました: {e}")
    else:
        await forward_channel.send(embed=embed, files=files, view=view)
