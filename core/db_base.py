import psycopg2
import psycopg2.extras

from .config import DB_CONFIG


class DatabaseBase:
    def __init__(self):
        self.db_config = dict(DB_CONFIG)

    def get_db(self):
        """データベース接続"""
        return psycopg2.connect(**self.db_config)
