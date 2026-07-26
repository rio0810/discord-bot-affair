import logging
import os

import discord

from .data import DM_CRITERIA_ROLE_IDS

logger = logging.getLogger(__name__)

# 恋愛の割合で「0割」を選んだ人に付与する雑談ロール（未設定なら付与しない）
ZERO_ROMANCE_ROLE_ID = int(os.getenv("ZERO_ROMANCE_ROLE_ID", "0"))
# 恋愛の割合で「1割以上」を選んだ人に付与する恋愛ロール（未設定なら付与しない）
ROMANCE_ROLE_ID = int(os.getenv("ROMANCE_ROLE_ID", "0"))
# 雑談ロール保持者から隠すカテゴリ（未設定なら非表示処理をしない）
ZERO_ROMANCE_HIDDEN_CATEGORY_ID = int(os.getenv("ZERO_ROMANCE_HIDDEN_CATEGORY_ID", "0"))


async def _hide_category_from_role(guild: discord.Guild, role: discord.Role):
    """指定カテゴリに『このロールは閲覧不可』の上書きを設定する（未設定時のみ）。
    ロール単位なので、以後この上書きが保持者全員に自動適用される。"""
    if not ZERO_ROMANCE_HIDDEN_CATEGORY_ID:
        return
    category = guild.get_channel(ZERO_ROMANCE_HIDDEN_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        return
    if category.overwrites_for(role).view_channel is False:
        return  # 既に設定済み
    try:
        await category.set_permissions(
            role, view_channel=False, reason="恋愛の割合0割ロールからカテゴリを非表示"
        )
    except (discord.Forbidden, discord.HTTPException):
        logger.error(f"カテゴリ非表示の設定に失敗しました: category={ZERO_ROMANCE_HIDDEN_CATEGORY_ID}")


async def _apply_choice_role(guild: discord.Guild, member: discord.Member, casual: bool):
    """入口の「雑談 / 恋愛」選択に応じてロールを付与（両者は排他）。
    雑談なら恋愛ロールを外して雑談ロール＋恋愛カテゴリ非表示、恋愛ならその逆。"""
    if guild is None or not isinstance(member, discord.Member):
        return
    zero_role = guild.get_role(ZERO_ROMANCE_ROLE_ID) if ZERO_ROMANCE_ROLE_ID else None
    romance_role = guild.get_role(ROMANCE_ROLE_ID) if ROMANCE_ROLE_ID else None
    grant, remove = (zero_role, romance_role) if casual else (romance_role, zero_role)
    try:
        if remove is not None and remove in member.roles:
            await member.remove_roles(remove, reason="プロフィール種別（雑談/恋愛）の選択")
        if grant is not None and grant not in member.roles:
            await member.add_roles(grant, reason="プロフィール種別（雑談/恋愛）の選択")
    except (discord.Forbidden, discord.HTTPException):
        logger.error(f"種別ロールの付与に失敗しました: {member.id}")
    if casual and zero_role is not None:
        await _hide_category_from_role(guild, zero_role)


async def _apply_dm_criteria_role(guild: discord.Guild, member: discord.Member, choice: str):
    """DM・フレンド申請の可否の選択に応じてロールを付与（相互排他）。"""
    if guild is None or not isinstance(member, discord.Member):
        return
    grant_id = DM_CRITERIA_ROLE_IDS.get(choice, 0)
    grant = guild.get_role(grant_id) if grant_id else None
    # 他の基準ロールを外す（選び直しに対応）
    others = [
        guild.get_role(rid)
        for label, rid in DM_CRITERIA_ROLE_IDS.items()
        if rid and label != choice
    ]
    try:
        for role in others:
            if role is not None and role in member.roles:
                await member.remove_roles(role, reason="DM・フレンド申請の可否の選択（変更）")
        if grant is not None and grant not in member.roles:
            await member.add_roles(grant, reason="DM・フレンド申請の可否の選択")
    except (discord.Forbidden, discord.HTTPException):
        logger.error(f"DM基準ロールの付与に失敗しました: {member.id}")
