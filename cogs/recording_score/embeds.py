import discord


def submitted_embed() -> discord.Embed:
    """提出が揃って審査に回ったときの案内（男女共通）。"""
    return discord.Embed(
        title="✅ 提出を受け付けました",
        description=(
            "運営の審査に回ります。\n"
            "📩 結果は **24時間以内** にお知らせしますので、少々お待ちください🙏"
        ),
        color=discord.Color.green(),
    )


def profile_received_embed() -> discord.Embed:
    """プロフィールは受け付けたが、まだ録音待ちのとき（男性）。"""
    return discord.Embed(
        title="🎙️ プロフィールを受け付けました",
        description=(
            "続いて **音声ファイル、または Discordの録音機能** で録音を"
            "このチャンネルに投稿してください。\n"
            "投稿されると運営の審査に回ります。"
        ),
        color=discord.Color.blurple(),
    )


def submit_canceled_embed() -> discord.Embed:
    """提出確認で「やり直す」を押されたとき。"""
    return discord.Embed(
        title="🔁 提出をキャンセルしました",
        description="別の音声をこのチャンネルに投稿してください。",
        color=discord.Color.orange(),
    )


def recording_received_embed() -> discord.Embed:
    """録音は受け付けたが、まだプロフィール未作成のとき（男性）。"""
    return discord.Embed(
        title="🎤 録音を受け付けました",
        description=(
            "続いて **「📝 プロフィールを作成する」** ボタンから"
            "プロフィールを作成してください。\n"
            "（プロフィールの作成が完了すると、運営の審査に回ります）"
        ),
        color=discord.Color.blurple(),
    )
