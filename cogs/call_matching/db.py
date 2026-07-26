import logging

from core.db_base import DatabaseBase

logger = logging.getLogger(__name__)


class CallDBMixin(DatabaseBase):
    """個通マッチングの DB アクセス（お試し履歴・ブロック・部屋数上限）。"""

    # ------------------------------------------------------------------ #
    # テーブル作成（init.sql は新規ボリューム時しか走らないため起動時にも作る）
    # ------------------------------------------------------------------ #
    def _ensure_tables(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS trial_invites (
                            recruiter_id BIGINT NOT NULL,
                            target_id BIGINT NOT NULL,
                            invited_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (recruiter_id, target_id)
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS call_blocks (
                            blocker_id BIGINT NOT NULL,
                            blocked_id BIGINT NOT NULL,
                            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (blocker_id, blocked_id)
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS call_room_limits (
                            user_id BIGINT PRIMARY KEY,
                            max_rooms INT NOT NULL
                        )
                    """)
                    conn.commit()
        except Exception as e:
            logger.error(f"テーブルの作成に失敗しました: {e}")

    def get_trial_invited_ids(self, recruiter_id: int) -> set[int]:
        """recruiter が過去にお試し個通へ誘った相手の ID 一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT target_id FROM trial_invites WHERE recruiter_id = %s",
                        (recruiter_id,),
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception as e:
            logger.error(f"お試し個通履歴の取得に失敗しました: {e}")
            return set()

    def record_trial_invite(self, recruiter_id: int, target_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO trial_invites (recruiter_id, target_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (recruiter_id, target_id),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"お試し個通履歴の記録に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # ブロック（お互いにお誘い相手一覧へ表示されなくなる）
    # ------------------------------------------------------------------ #
    def get_blocked_ids(self, blocker_id: int) -> set[int]:
        """自分がブロックしている相手の ID 一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT blocked_id FROM call_blocks WHERE blocker_id = %s", (blocker_id,)
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception as e:
            logger.error(f"ブロック一覧の取得に失敗しました: {e}")
            return set()

    def get_blockers_of(self, user_id: int) -> set[int]:
        """自分をブロックしている相手の ID 一覧。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT blocker_id FROM call_blocks WHERE blocked_id = %s", (user_id,)
                    )
                    return {row[0] for row in cur.fetchall()}
        except Exception as e:
            logger.error(f"被ブロック一覧の取得に失敗しました: {e}")
            return set()

    def is_blocked_between(self, user_a: int, user_b: int) -> bool:
        """どちらかがどちらかをブロックしているか。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM call_blocks WHERE (blocker_id = %s AND blocked_id = %s) "
                        "OR (blocker_id = %s AND blocked_id = %s) LIMIT 1",
                        (user_a, user_b, user_b, user_a),
                    )
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"ブロック状態の確認に失敗しました: {e}")
            return False

    def add_block(self, blocker_id: int, blocked_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO call_blocks (blocker_id, blocked_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (blocker_id, blocked_id),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"ブロックの追加に失敗しました: {e}")

    def get_all_blocks(self) -> list[tuple[int, int]]:
        """全ブロック関係 (blocker_id, blocked_id) の一覧（登録順）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT blocker_id, blocked_id FROM call_blocks ORDER BY created_at"
                    )
                    return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"ブロック全件の取得に失敗しました: {e}")
            return []

    def remove_block(self, blocker_id: int, blocked_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM call_blocks WHERE blocker_id = %s AND blocked_id = %s",
                        (blocker_id, blocked_id),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"ブロックの解除に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 個人ごとの部屋数上限（パネルのボタンで 1 件に制限 ⇔ 解除）
    # ------------------------------------------------------------------ #
    def get_room_limit(self, user_id: int) -> int | None:
        """個人設定の上限。未設定なら None（ロール別のデフォルトを使う）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT max_rooms FROM call_room_limits WHERE user_id = %s", (user_id,)
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            logger.error(f"部屋数上限の取得に失敗しました: {e}")
            return None

    def get_all_room_limits(self) -> dict[int, int]:
        """個人設定の上限を一括取得（一覧フィルタ用）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id, max_rooms FROM call_room_limits")
                    return {row[0]: row[1] for row in cur.fetchall()}
        except Exception as e:
            logger.error(f"部屋数上限の一括取得に失敗しました: {e}")
            return {}

    def set_room_limit(self, user_id: int, max_rooms: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO call_room_limits (user_id, max_rooms) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET max_rooms = EXCLUDED.max_rooms",
                        (user_id, max_rooms),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"部屋数上限の設定に失敗しました: {e}")

    def clear_room_limit(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM call_room_limits WHERE user_id = %s", (user_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"部屋数上限の解除に失敗しました: {e}")
