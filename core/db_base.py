"""DB接続の共通基盤。

psycopg2 は同期ライブラリなので、接続確立（TLSハンドシェイク込み）をイベント
ループ上で毎回行うと Bot 全体が数百ms単位で止まる。止まっている間に届いた
Interaction は3秒の応答期限を過ぎ、`404 Unknown interaction (10062)` になる。

対策として接続はプールで使い回し（`ThreadedConnectionPool`）、時間のかかる
DB処理は `run_db()` でスレッドへ逃がす。`get_db()` の使い方は従来どおりで、
返るのはプールへ返却する薄いラッパー。
"""

import asyncio
import logging
import threading

import psycopg2
import psycopg2.extras
from psycopg2 import pool as psycopg2_pool

from .config import DB_CONFIG, DB_POOL_MAX, DB_POOL_MIN

logger = logging.getLogger(__name__)

# プールはプロセスで1つ。生成はスレッドセーフにする
_pool: psycopg2_pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

# 接続が張りっぱなしで切られていた場合に気付けるようにする（Railway等の idle 切断対策）
_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def _get_pool() -> psycopg2_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2_pool.ThreadedConnectionPool(
                    max(1, DB_POOL_MIN),
                    max(1, DB_POOL_MAX),
                    **DB_CONFIG,
                    **_KEEPALIVE_KWARGS,
                )
                logger.info(
                    f"DBコネクションプールを作成しました（min={DB_POOL_MIN} max={DB_POOL_MAX}）"
                )
    return _pool


class PooledConnection:
    """プールから借りた接続のラッパー。

    - `close()` は実際には閉じずプールへ返却する
    - `with` を抜けるときに commit/rollback したうえで返却する
      （psycopg2 の `with conn:` は返却まではしないが、この基盤では
       `with self.get_db() as conn:` が返却まで担う）
    - それ以外の属性は元の接続へそのまま委譲する
    """

    def __init__(self, conn):
        self._conn = conn
        self._returned = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self._conn.commit()
            except psycopg2.Error:
                logger.exception("commit に失敗しました")
                raise
            finally:
                self.close()
        else:
            try:
                self._conn.rollback()
            except psycopg2.Error:
                # ロールバックできない＝接続が壊れているので破棄して返す
                self.close(discard=True)
                return False
            self.close()
        return False

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def close(self, discard: bool = False):
        """プールへ返却する（二重返却は無視）。discard=True なら接続を破棄。"""
        if self._returned:
            return
        self._returned = True
        broken = discard or self._conn.closed
        try:
            _get_pool().putconn(self._conn, close=broken)
        except psycopg2_pool.PoolError:
            # プールが閉じられている等。接続を閉じるだけにする
            try:
                self._conn.close()
            except psycopg2.Error:
                pass


class DatabaseBase:
    def __init__(self):
        self.db_config = dict(DB_CONFIG)

    def get_db(self) -> PooledConnection:
        """プールから接続を借りる。使い終わったら close()（または with で自動返却）。"""
        conn = _get_pool().getconn()
        if conn.closed:
            # 切断済みの接続を掴んだら破棄して張り直す
            _get_pool().putconn(conn, close=True)
            conn = _get_pool().getconn()
        return PooledConnection(conn)

    @staticmethod
    async def run_db(func, *args, **kwargs):
        """同期のDB処理をスレッドで実行する。イベントループを止めないための入口。"""
        if kwargs:
            return await asyncio.to_thread(lambda: func(*args, **kwargs))
        return await asyncio.to_thread(func, *args)


def close_pool():
    """終了処理用。プールの接続をすべて閉じる。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
