import discord
from discord.ext import commands
import os
from .db_base import DatabaseBase

class AdminCogBase(commands.Cog, DatabaseBase):
    ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
