import discord
from discord import app_commands
from discord.ext import commands
import random
import json

from core import config

class TalkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_random_topic(self):
        json_path = config.ASSETS_DIR / "topics.json"

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return random.choice(data["talk_topics"])

    # スラッシュコマンドの定義
    @app_commands.command(name="topic", description="雑談のネタをランダムに出します")
    async def topic(self, interaction: discord.Interaction):
        topic_text = self.get_random_topic()
        await interaction.response.send_message(f"💬 **トークお題:** {topic_text}")

async def setup(bot):
    await bot.add_cog(TalkCog(bot))
