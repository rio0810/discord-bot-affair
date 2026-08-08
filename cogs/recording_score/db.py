import json
import logging

import discord

from core.db_base import DatabaseBase

logger = logging.getLogger(__name__)


class RecordingDBMixin(DatabaseBase):
    """プロフ審査の DB アクセス（審査メンバー・採点・待機プロフィール・期限管理）。"""

    def _ensure_tables(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS recording_scores (
                            message_id BIGINT NOT NULL,
                            reviewer_id BIGINT NOT NULL,
                            submitter_id BIGINT NOT NULL,
                            s_profile INT NOT NULL,
                            s_voice INT NOT NULL,
                            s_talk INT NOT NULL,
                            s_character INT NOT NULL,
                            reason TEXT,
                            PRIMARY KEY (message_id, reviewer_id)
                        )
                    """)
                    cur.execute("ALTER TABLE recording_scores ADD COLUMN IF NOT EXISTS reason TEXT")
                    cur.execute("ALTER TABLE recording_scores ADD COLUMN IF NOT EXISTS kind CHAR(1) DEFAULT 'm'")
                    # 女性は voice/talk を採点しないため NULL 許可にする
                    cur.execute("ALTER TABLE recording_scores ALTER COLUMN s_voice DROP NOT NULL")
                    cur.execute("ALTER TABLE recording_scores ALTER COLUMN s_talk DROP NOT NULL")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS recording_results (
                            message_id BIGINT PRIMARY KEY
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS pending_interview (
                            user_id BIGINT PRIMARY KEY,
                            embed_json TEXT NOT NULL
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS interview_done (
                            user_id BIGINT PRIMARY KEY
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS interview_verdicts (
                            submitter_id BIGINT PRIMARY KEY
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS profile_created (
                            user_id BIGINT PRIMARY KEY
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS interview_reviewers (
                            user_id BIGINT PRIMARY KEY
                        )
                    """)
                    # 転送済みの審査（未採点メンション・強制結果の期限管理用）
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS interview_reviews (
                            message_id BIGINT PRIMARY KEY,
                            channel_id BIGINT NOT NULL,
                            submitter_id BIGINT NOT NULL,
                            kind CHAR(1) DEFAULT 'm',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            reminded BOOLEAN NOT NULL DEFAULT FALSE
                        )
                    """)
                    conn.commit()
        except Exception as e:
            logger.error(f"recording_scores テーブルの作成に失敗しました: {e}")

    # ------------------------------------------------------------------ #
    # 審査メンバー（採点者）
    # ------------------------------------------------------------------ #
    def is_reviewer(self, user_id: int) -> bool:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM interview_reviewers WHERE user_id = %s", (user_id,))
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"審査メンバーの照会に失敗しました: {e}")
            return False

    def _reviewer_count(self) -> int:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM interview_reviewers")
                    return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"審査メンバー数の取得に失敗しました: {e}")
            return 0

    def _add_reviewer(self, user_id: int) -> bool:
        """登録できたら True、既に登録済みなら False。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO interview_reviewers (user_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING RETURNING user_id",
                        (user_id,),
                    )
                    added = cur.fetchone() is not None
                    conn.commit()
                    return added
        except Exception as e:
            logger.error(f"審査メンバーの登録に失敗しました: {e}")
            return False

    def _remove_reviewer(self, user_id: int) -> bool:
        """削除できたら True、元々登録が無ければ False。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM interview_reviewers WHERE user_id = %s RETURNING user_id",
                        (user_id,),
                    )
                    removed = cur.fetchone() is not None
                    conn.commit()
                    return removed
        except Exception as e:
            logger.error(f"審査メンバーの削除に失敗しました: {e}")
            return False

    def scored_reviewer_ids(self, message_id: int) -> set[int]:
        """指定の審査メッセージに採点した人のID集合。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT reviewer_id FROM recording_scores WHERE message_id = %s",
                        (message_id,),
                    )
                    return {r[0] for r in cur.fetchall()}
        except Exception as e:
            logger.error(f"採点状況の取得に失敗しました: {e}")
            return set()

    def _list_reviewers(self) -> list[int]:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM interview_reviewers")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"審査メンバー一覧の取得に失敗しました: {e}")
            return []

    # ------------------------------------------------------------------ #
    # 待機プロフィール・各種フラグ
    # ------------------------------------------------------------------ #
    def _store_pending(self, user_id: int, embed: discord.Embed):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO pending_interview (user_id, embed_json) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET embed_json = EXCLUDED.embed_json",
                        (user_id, json.dumps(embed.to_dict())),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"待機プロフィールの保存に失敗しました: {e}")

    def _mark_done(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO interview_done (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                        (user_id,),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"審査済みフラグの記録に失敗しました: {e}")

    def _is_done(self, user_id: int) -> bool:
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM interview_done WHERE user_id = %s", (user_id,))
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"審査済みフラグの取得に失敗しました: {e}")
            return False

    def mark_profile_created(self, user_id: int):
        """プロフィール作成済みとして記録（2回目の作成を防ぐ）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO profile_created (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                        (user_id,),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"プロフィール作成フラグの記録に失敗しました: {e}")

    def has_profile(self, user_id: int) -> bool:
        """プロフィールを作成済みか。DB失敗時は False（作成を許可＝フェイルオープン）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM profile_created WHERE user_id = %s", (user_id,))
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"プロフィール作成フラグの取得に失敗しました: {e}")
            return False

    def _has_submitted(self, user_id: int) -> bool:
        """審査に提出済みか（提出済みフラグ・審査中・判定済みのいずれか）。
        DB失敗時は True（＝未提出とみなさない）でフェイルセーフにする。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 WHERE EXISTS (SELECT 1 FROM interview_done WHERE user_id = %s) "
                        "OR EXISTS (SELECT 1 FROM interview_reviews WHERE submitter_id = %s) "
                        "OR EXISTS (SELECT 1 FROM interview_verdicts WHERE submitter_id = %s)",
                        (user_id, user_id, user_id),
                    )
                    return cur.fetchone() is not None
        except Exception as e:
            logger.error(f"審査提出状況の取得に失敗しました: {e}")
            return True

    def _pop_pending(self, user_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT embed_json FROM pending_interview WHERE user_id = %s", (user_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    cur.execute("DELETE FROM pending_interview WHERE user_id = %s", (user_id,))
                    conn.commit()
                    return json.loads(row[0])
        except Exception as e:
            logger.error(f"待機プロフィールの取得に失敗しました: {e}")
            return None

    # ------------------------------------------------------------------ #
    # 合否判定・結果出力の権利（二重処理防止）
    # ------------------------------------------------------------------ #
    def _claim_verdict(self, submitter_id: int) -> bool:
        """合否判定の権利を取る（1人につき最初の1回だけ True）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO interview_verdicts (submitter_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING RETURNING submitter_id",
                        (submitter_id,),
                    )
                    claimed = cur.fetchone() is not None
                    conn.commit()
                    return claimed
        except Exception as e:
            logger.error(f"合否判定フラグの取得に失敗しました: {e}")
            return False

    def _unclaim_verdict(self, submitter_id: int):
        """判定処理が失敗したときにロックを解除して再判定できるようにする。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM interview_verdicts WHERE submitter_id = %s",
                        (submitter_id,),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"合否判定フラグの解除に失敗しました: {e}")

    def _claim_result(self, message_id: int) -> bool:
        """結果出力の権利を取る（複数回出力しないよう最初の1回だけ True）。"""
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO recording_results (message_id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING RETURNING message_id",
                        (message_id,),
                    )
                    claimed = cur.fetchone() is not None
                    conn.commit()
                    return claimed
        except Exception as e:
            logger.error(f"結果権利の取得に失敗しました: {e}")
            return False

    # ------------------------------------------------------------------ #
    # 審査の期限管理テーブル
    # ------------------------------------------------------------------ #
    def _register_review(self, result, submitter_id: int, kind: str):
        """転送された審査を期限管理テーブルに登録する。result は forward_recording の戻り値。"""
        if not result:
            return
        message_id, channel = result
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO interview_reviews (message_id, channel_id, submitter_id, kind) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT (message_id) DO NOTHING",
                        (message_id, channel.id, submitter_id, kind),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"審査の期限登録に失敗しました: {e}")

    def _delete_review(self, message_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM interview_reviews WHERE message_id = %s", (message_id,))
                    conn.commit()
        except Exception as e:
            logger.error(f"審査の期限削除に失敗しました: {e}")

    def _list_pending_reviews(self):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT message_id, channel_id, submitter_id, kind, created_at, reminded "
                        "FROM interview_reviews"
                    )
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"未処理審査の取得に失敗しました: {e}")
            return []

    def _mark_reminded(self, message_id: int):
        try:
            with self.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE interview_reviews SET reminded = TRUE WHERE message_id = %s",
                        (message_id,),
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"未採点メンション済みフラグの記録に失敗しました: {e}")
