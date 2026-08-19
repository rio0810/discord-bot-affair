from discord.ext import commands

from . import config
from .db_base import DatabaseBase


class AdminCogBase(commands.Cog, DatabaseBase):
    ADMIN_ROLE_ID = config.ADMIN_ROLE_ID

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
