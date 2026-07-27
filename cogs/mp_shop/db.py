import logging
from datetime import datetime

import discord

from core.db_base import DatabaseBase

logger = logging.getLogger(__name__)


class MPShopDBMixin(DatabaseBase):
    """MPチケットショップの DB アクセス（チケット残高・個人テキストch・雰囲気写真ノルマ）。"""

    def _ensure_tables(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS mp_text_channels (
                            user_id BIGINT PRIMARY KEY,
                            channel_id BIGINT NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS mood_photo_deadlines (
                            user_id BIGINT PRIMARY KEY,
                            deadline TIMESTAMP NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS mp_color_roles (
                            user_id BIGINT PRIMARY KEY,
                            role_id BIGINT NOT NULL
                        )
                    """)
                    conn.commit()
        except Exception as e:
            logger.error(f"mp_shop テーブルの作成に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # チケット残高
    # ------------------------------------------------------------------ #
    def get_tickets(self, user_id: int) -> int:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT mp_tickets FROM users WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"チケット残高の取得に失敗しました: {e}")
            return 0

    def _grant(self, user_id: int, amount: int, user_name: str | None = None) -> int:
        """チケットを amount 枚増減し（負なら減少）、変更後の残高を返す。user_name があれば記録。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (user_id, mp_tickets, user_name) VALUES (%s, %s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET "
                        "mp_tickets = GREATEST(0, users.mp_tickets + EXCLUDED.mp_tickets), "
                        "user_name = COALESCE(EXCLUDED.user_name, users.user_name) "
                        "RETURNING mp_tickets",
                        (user_id, amount, user_name),
                    )
                    new_balance = cur.fetchone()[0]
                    conn.commit()
                    return new_balance
        except Exception as e:
            logger.error(f"チケットの増減に失敗しました: {e}")
            return self.get_tickets(user_id)

    def _spend(self, user_id: int, cost: int) -> bool:
        """残高が足りれば cost 枚を消費して True。足りなければ False（原子的）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET mp_tickets = mp_tickets - %s "
                        "WHERE user_id = %s AND mp_tickets >= %s RETURNING mp_tickets",
                        (cost, user_id, cost),
                    )
                    ok = cur.fetchone() is not None
                    conn.commit()
                    return ok
        except Exception as e:
            logger.error(f"チケットの消費に失敗しました: {e}")
            return False

    def _refund(self, user_id: int, cost: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET mp_tickets = mp_tickets + %s WHERE user_id = %s",
                        (cost, user_id),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"チケットの返還に失敗しました: {e}")

    def _list_ticket_holders(self) -> list[tuple[int, int]] | None:
        """所持者一覧 (user_id, mp_tickets) を枚数降順で返す。取得失敗時は None。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id, mp_tickets FROM users WHERE mp_tickets > 0 ORDER BY mp_tickets DESC"
                    )
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"チケット一覧の取得に失敗しました: {e}")
            return None

    # ------------------------------------------------------------------ #
    # 個人専用テキストチャット
    # ------------------------------------------------------------------ #
    def _existing_text_channel(self, guild: discord.Guild, user_id: int):
        """そのユーザーが作成済みで、今も存在する個人テキストチャットを返す（無ければNone）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT channel_id FROM mp_text_channels WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
        except Exception as e:
            logger.error(f"個人テキストチャットの確認に失敗しました: {e}")
            return None
        if not row:
            return None
        return guild.get_channel(row[0])  # 削除済みなら None

    def _save_text_channel(self, user_id: int, channel_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO mp_text_channels (user_id, channel_id) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
                        (user_id, channel_id),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"個人テキストチャットの記録に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # カラーロール（作り直し時に旧ロールを消すための記録）
    # ------------------------------------------------------------------ #
    def _get_color_role_id(self, user_id: int) -> int | None:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT role_id FROM mp_color_roles WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            logger.error(f"カラーロールの取得に失敗しました: {e}")
            return None

    def _save_color_role(self, user_id: int, role_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO mp_color_roles (user_id, role_id) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET role_id = EXCLUDED.role_id",
                        (user_id, role_id),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"カラーロールの記録に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 雰囲気写真ノルマ
    # ------------------------------------------------------------------ #
    def _set_mood_deadline(self, user_id: int, deadline: datetime):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO mood_photo_deadlines (user_id, deadline) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET deadline = EXCLUDED.deadline",
                        (user_id, deadline),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"雰囲気写真ノルマの登録に失敗しました: {e}")

    def _clear_mood_deadline(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM mood_photo_deadlines WHERE user_id = %s", (user_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"雰囲気写真ノルマのクリアに失敗しました: {e}")

    def _expired_mood_user_ids(self) -> list[int]:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT user_id FROM mood_photo_deadlines WHERE deadline <= %s", (datetime.now(),)
                    )
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"雰囲気写真の期限チェックに失敗しました: {e}")
            return []

    # ------------------------------------------------------------------ #
    # お試し個通の誘い履歴リセット（call_matching の trial_invites テーブル）
    # ------------------------------------------------------------------ #
    def _reset_trial_invites(self, user_id: int) -> bool:
        """リセットできたら True、DBエラー時は False。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM trial_invites WHERE recruiter_id = %s", (user_id,))
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"お試し個通のリセットに失敗しました: {e}")
            return False
