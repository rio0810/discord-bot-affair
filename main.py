import discord
from discord.ext import commands
import logging
import os
import dotenv
from server import server_thread
from core.log_to_discord import setup_logging

logger = logging.getLogger(__name__)

dotenv.load_dotenv()
TOKEN = os.environ.get("TOKEN")
GUILD = os.environ.get("MY_GUILD")
MY_GUILD = discord.Object(id=GUILD)

class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = True
        
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # ロギングの初期化（コンソール＋設定時は ERROR 以上を Discord へ）
        setup_logging(self)

        # cog（拡張機能）の読み込み
        #  - cogs/*.py … 単体ファイルの cog
        #  - cogs/<feature>/__init__.py … パッケージ化した cog（内部モジュールは直接読み込まない）
        cogs_dir = "./cogs"
        for entry in sorted(os.listdir(cogs_dir)):
            full = os.path.join(cogs_dir, entry)
            if os.path.isfile(full) and entry.endswith(".py") and not entry.startswith("_"):
                await self.load_extension(f"cogs.{entry[:-3]}")
            elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                await self.load_extension(f"cogs.{entry}")
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
