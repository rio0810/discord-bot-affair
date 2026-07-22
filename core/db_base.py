import psycopg2
import psycopg2.extras
import os

class DatabaseBase:
    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', 'db'),
            'port': int(os.getenv('DB_PORT', 5432)),
            'user': os.getenv('DB_USER', 'user'),
            'password': os.getenv('DB_PASS', 'password'),
            'dbname': os.getenv('DB_NAME', 'postgres_db'),
            'sslmode': os.getenv('DB_SSLMODE', 'require') 
        }

    def get_db(self):
        """データベース接続"""
        return psycopg2.connect(**self.db_config)
