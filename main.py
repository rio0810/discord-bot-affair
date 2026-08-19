import logging
import re
from pathlib import Path

import discord
from discord.ext import commands

from core.config import BASE_DIR, MY_GUILD_ID, TOKEN
from core.log_to_discord import setup_logging
from server import server_thread

logger = logging.getLogger(__name__)

MY_GUILD = discord.Object(id=MY_GUILD_ID)

COGS_DIR = BASE_DIR / "cogs"


def _init_kind(path: Path) -> str:
    """パッケージの __init__.py を見て、その役割を判定する。

    - "cog"   … setup を持つ = それ自体が拡張機能（例: onboarding/recording_score）
    - "group" … COG_GROUP = True = 分類用のフォルダなので中を再帰的に読む
    - "skip"  … どちらでもない補助パッケージ（例: profile/wizard）
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return "skip"
    # 自前で定義しているか、サブモジュールから re-export しているか
    if re.search(r"^\s*(async\s+)?def setup\b", source, re.M) or re.search(
        r"^from \.\S+ import [^\n]*\bsetup\b", source, re.M
    ):
        return "cog"
    if re.search(r"^COG_GROUP\s*=\s*True", source, re.M):
        return "group"
    return "skip"


class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = True
        
        super().__init__(command_prefix="!", intents=intents)

    async def _load_cogs(self, directory: Path):
        """cogs/ を再帰的に走査して拡張機能を読み込む。

        - `<dir>/*.py` … 単体ファイルの cog
        - `<dir>/<feature>/__init__.py` に setup … パッケージ化した cog（中は直接読まない）
        - `<dir>/<group>/__init__.py` に COG_GROUP … 分類フォルダなので下の階層へ降りる
        """
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith((".", "_")):
                continue
            # BASE_DIR からの相対パスをドット区切りのモジュール名にする
            module = ".".join(entry.relative_to(BASE_DIR).with_suffix("").parts)
            if entry.is_file() and entry.suffix == ".py":
                await self.load_extension(module)
            elif entry.is_dir():
                init = entry / "__init__.py"
                kind = _init_kind(init) if init.is_file() else "group"
                if kind == "cog":
                    await self.load_extension(module)
                elif kind == "group":
                    await self._load_cogs(entry)

    async def setup_hook(self):
        # ロギングの初期化（コンソール＋設定時は ERROR 以上を Discord へ）
        setup_logging(self)

        # cog（拡張機能）の読み込み
        await self._load_cogs(COGS_DIR)
        try:
            await self.tree.sync()
        except discord.HTTPException as e:
            logger.warning(f"グローバルコマンドの同期に失敗しました: {e}")

        # そのサーバー専用に同期（失敗してもBotは起動を続ける）
        try:
            self.tree.copy_global_to(guild=MY_GUILD)
            await self.tree.sync(guild=MY_GUILD)
            logger.info("Slash commands synced!")
        except discord.Forbidden as e:
            logger.warning(
                "ギルドコマンドの同期に失敗しました（Missing Access）。"
                "MY_GUILD のサーバーにBotが参加しているか、"
                "applications.commands スコープ付きで招待されているかを確認してください: "
                f"{e}"
            )
        except discord.HTTPException as e:
            logger.warning(f"ギルドコマンドの同期に失敗しました: {e}")

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')

if __name__ == "__main__":
    server_thread()
    bot = DiscordBot()
    # log_handler=None にして、discord.py 側のロギング初期化で
    # 自前のハンドラ（setup_logging）が上書きされないようにする
    bot.run(TOKEN, log_handler=None)
