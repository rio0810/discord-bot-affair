"""ログを Discord チャンネルへ流す仕組み（方法1）。

- ルートロガーにコンソール（Railwayログ）ハンドラを付ける。
- `ERROR_LOG_CHANNEL_ID` が設定されていれば、ERROR 以上のログを
  そのチャンネルへ送る `DiscordLogHandler` を追加する。

logging.emit は同期・別スレッドから呼ばれ得るため、レコードはいったん
キューに積み、Bot のイベントループ上のタスクでまとめて送信する
（Discord へのレート制限・送信失敗による無限ループを避ける）。
"""

import asyncio
import logging
import os

import discord

_THIS_LOGGER_PREFIX = __name__  # 自分自身のログは弾いて無限ループを防ぐ


def _split(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i : i + size]


class DiscordLogHandler(logging.Handler):
    def __init__(self, bot: discord.Client, channel_id: int, level: int = logging.ERROR):
        super().__init__(level=level)
        self.bot = bot
        self.channel_id = channel_id
        self._queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=1000)
        self._task: asyncio.Task | None = None

    def start(self):
        """送信タスクを起動する（Bot のループが動いている場所から呼ぶ）。"""
        if self._task is None:
            self._task = self.bot.loop.create_task(self._sender())

    def emit(self, record: logging.LogRecord):
        # 自分自身（送信処理）由来のログは弾く（無限ループ防止）
        if record.name.startswith(_THIS_LOGGER_PREFIX):
            return
        try:
            msg = self.format(record)
        except Exception:
            return
        loop = getattr(self.bot, "loop", None)
        if loop is None or not loop.is_running():
            return
        # 別スレッドから呼ばれても安全にキューへ積む
        try:
            loop.call_soon_threadsafe(self._enqueue, msg)
        except RuntimeError:
            pass

    def _enqueue(self, msg: str):
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass  # 溢れたら捨てて Discord を守る

    async def _sender(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        while True:
            first = await self._queue.get()
            # 短時間に溜まった分をまとめる（レート制限・スパム対策）
            batch = [first]
            await asyncio.sleep(1.0)
            while not self._queue.empty() and len(batch) < 10:
                batch.append(self._queue.get_nowait())
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue
            text = "\n".join(batch)
            for chunk in _split(text, 1900):
                try:
                    await channel.send(f"```\n{chunk}\n```")
                except (discord.Forbidden, discord.HTTPException):
                    pass  # ここで失敗してもログに流さない（無限ループ防止）


def setup_logging(bot: discord.Client) -> DiscordLogHandler | None:
    """ルートロガーにコンソール＋（設定時）Discord ハンドラを付ける。
    Discord ハンドラを返す（未設定なら None）。呼び出しは Bot のループ内で行う。"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # コンソール（Railwayログ）用ハンドラ
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root.addHandler(console)

    channel_id = int(os.getenv("ERROR_LOG_CHANNEL_ID") or "0")
    if not channel_id:
        return None

    handler = DiscordLogHandler(bot, channel_id)
    handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    handler.start()
    return handler
