# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

**With Docker (recommended):**
```bash
docker compose up -d
```

**Locally:**
```bash
pip install -r requirements.txt
python main.py
```

No test suite or linter is configured.

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|---|---|
| `TOKEN` | Discord bot token |
| `MY_GUILD` | Target guild ID for slash command sync |
| `PROFILE_TARGET_CHANNEL_IDS` | Comma-separated channel IDs for auto profile display |
| `ZERO_ROMANCE_ROLE_ID` | 雑談 role auto-granted when a user picks 恋愛の割合「0割」 in the profile wizard (optional). Mutually exclusive with `ROMANCE_ROLE_ID` |
| `ROMANCE_ROLE_ID` | 恋愛 role auto-granted when a user picks 恋愛の割合「1割以上」 (optional). Mutually exclusive with `ZERO_ROMANCE_ROLE_ID` |
| `ZERO_ROMANCE_HIDDEN_CATEGORY_ID` | Category hidden (role-level `view_channel=False` overwrite) from `ZERO_ROMANCE_ROLE_ID` holders; set when the role is first granted (optional) |
| `ADMIN_ROLE_ID` | Role ID that can use admin commands |
| `EXCLUDED_CHANNEL_IDS` | Comma-separated VC IDs excluded from VC-time tracking |
| `VC_RANK_REDUCED_CATEGORY_IDS` | Comma-separated category IDs where VC time accrues at 1/3 rate (fractional carry) |
| `INTERVIEW_ROOM_CATEGORY_ID` | Category ID under which per-member interview rooms are created (optional) |
| `RECORDING_FORWARD_CHANNEL_ID` | Text channel recordings/審査 are forwarded to (no forwarding if unset) |
| `RECORDING_FORUM_MALE_ID` / `RECORDING_FORUM_FEMALE_ID` | Per-gender 審査 forums (posts as a new thread titled with the submitter's name). Male reviews go to MALE, female to FEMALE; falls back to the `RECORDING_FORWARD_CHANNEL_ID` text channel |
| `SCORE_REVIEWER_COUNT` | How many reviewers must score a recording before the result is posted (default 4) |
| `MALE_ROLE_ID` / `FEMALE_ROLE_ID` | Role IDs for the 1-on-1 call matching feature |
| `NEWCOMER_ROLE_ID` | Members with this role can't use call matching (blocked from recruiting, hidden from target lists, and can't accept). Optional |
| `WAITING_ROLE_ID` / `WAITING_CATEGORY_ID` | `waiting_room.py`: auto-assigns the waiting role on join; hides every category except `WAITING_CATEGORY_ID` (visible category is view-only, no send); removed when the role is taken away |
| `REVIEW_ROLE_ID` | Role removed on 合格 verdict (defaults to `WAITING_ROLE_ID` if unset). On 合格 the reviewer removes it and adds `NEWCOMER_ROLE_ID`; on 不合格 the user is banned. Verdict button lives on the 審査結果 panel |
| `MALE_PROFILE_CHANNEL_ID` / `FEMALE_PROFILE_CHANNEL_ID` | On 合格, the bot posts in the member's personal interview/profile channel (DM fallback) directing them to write their profile in the gender-matching channel here (male via `MALE_ROLE_ID`, female via `FEMALE_ROLE_ID`) |
| `GUIDELINE_CHANNEL_ID` | Guideline channel linked in the 合格 message so the member checks the server info (optional) |
| `CALL_CATEGORY_ID` | Category ID for created call rooms (optional) |
| `CALL_LOG_CHANNEL_ID` | Channel ID for call-matching accept/decline logs (no logging if unset) |
| `MAX_ROOMS_PER_FEMALE` / `MAX_ROOMS_PER_MALE` | Max concurrent call rooms per user (default 2) |
| `TRIAL_WARNING_SOUND` | Soundboard sound (ID or name) played in the trial-call VC at the 5-min-remaining warning; bot briefly joins the VC to send it (needs PyNaCl). No sound if unset |
| `LOBBY_VC_ID` | Trigger VC(s) for the join-to-create temp-VC feature (`temp_vc.py`), comma-separated for multiple. Joining one makes a personal VC. Disabled if unset |
| `TEMP_VC_CATEGORY_ID` | Category for created temp VCs (defaults to the trigger VC's category) |

## Architecture

**Entry point:** `main.py` — creates the bot, recursively loads all cogs from `cogs/`, syncs slash commands globally and to `MY_GUILD`, then starts `server.py` (FastAPI health check on port 8080) in a background thread.

**Cog loading:** Any `.py` file under `cogs/` with a `setup(bot)` function is loaded automatically. New features go in a new cog file.

**Inheritance chain for DB-backed cogs:**
```
commands.Cog + DatabaseBase (core/db_base.py)
    └── AdminCogBase (core/admin_base.py)
```

`DatabaseBase` owns the PostgreSQL connection and provides `get_db()`. All cogs that touch the DB extend it. `AdminCogBase` adds the `ADMIN_ROLE_ID` constant for admin-gated cogs.

**Database:** PostgreSQL (service name `db` in Docker). Schema in `init.sql`:
- `users(user_id, vc_minutes_total, rank)`
- `trial_invites(recruiter_id, target_id, invited_at)` — one-shot trial-call invite history (also auto-created at cog load)
- `call_blocks(blocker_id, blocked_id, created_at)` — call-matching blocks, hides both users from each other's target list (also auto-created at cog load)
- `call_room_limits(user_id, max_rooms)` — per-user room-cap override set via the panel's 1-room-limit toggle button (also auto-created at cog load)

**DB credentials** are hardcoded in `core/db_base.py` (host=`db`, user=`user`, pass=`password`, db=`postgres_db`, sslmode=`require`). These match the `compose.yml` service.

**UI components** (`ui/`) are Discord `View` subclasses with persistent buttons (custom_id prefixed `persistent:`) so they survive bot restarts.

**Key background tasks (discord.py `@tasks.loop`):**
- `vc_rank.py` — tracks VC time every 1 min and updates users' rank
- `temp_vc.py` — join-to-create temp VCs; sweeps empty temp VCs every 5 min (`temp_vcs` table); rename panel in the VC's text chat

## Slash Commands Reference

| Command | Cog | Description |
|---|---|---|
| `/topic` | `talk.py` | Random discussion topic |
| `/rank` | `vc_rank.py` | Show VC/text rank card (Pillow image) |
| `/set_appeal_panel` | `interview_room.py` | Admin: place an A/B panel — A creates a per-user appeal-recording channel (audio forwarded to `RECORDING_FORWARD_CHANNEL_ID`), B creates a per-user profile channel. Males submit a recording by posting audio (file or Discord voice message) in their interview channel; the 採点 review is forwarded to `RECORDING_FORWARD_CHANNEL_ID` only once BOTH the audio and the profile exist (order-independent; `recording_score.py` waits via the `pending_interview` table). Reviewers score 4 categories (0–2 each) via a RadioGroup modal; once 4 reviewers submit, the average is posted mentioning `ADMIN_ROLE_ID` (`recording_scores`/`recording_results` tables). Both channels get a "プロフィールを作成する" button that runs a profile wizard (`ui/profile_wizard.py`): modal for name/hobby/type + 8 sequential ephemeral steps — selects (>25 options auto-split) plus two modals of 4 `RadioGroup`s each (血液型/結婚/出会い/同居人, 休日/酒/タバコ/恋愛距離), posting the result as an embed |
| `/set_call_panel` | `call_matching.py` | Admin: place a 1-on-1 call recruit panel — male↔female pick each other via paged select + modal message, the target accepts/declines via DM buttons; accept creates a private VC + text room, declining auto-registers a block (decliner → recruiter), both outcomes logged to `CALL_LOG_CHANNEL_ID` (no logging if unset). Also has a trial-call button: same flow but the room auto-closes after 30 min (warning mention at 5 min remaining, checked by a 1-min `tasks.loop` against channel `created_at`), and each recruiter can trial-invite a given member only once ever (`trial_invites` table). Panel also has a block-edit button (`call_blocks` table) opening a `CheckboxGroup` modal (blocked members pre-checked, check/uncheck to block/unblock, capped at 50 candidates); blocked pairs are hidden from each other's target list both ways. A room-limit toggle button (`call_room_limits` table) lets a user cap their own concurrent rooms at 1 (press again to restore the role default); members at their room cap are hidden from target lists |
| `/admin_block_list` | `call_matching.py` | Admin: list all call-matching blocks (blocker → blocked), optional member filter |
| `/newcomer_list` | `call_matching.py` | Admin: list members who have `NEWCOMER_ROLE_ID` |
| `/set_mp_panel` | `mp_shop.py` | Admin: place an MP-ticket panel — check balance button + redeem select (お試し個通リセット 10枚 → clears the user's `trial_invites`; 個人専用テキストチャット作成 6枚 → modal for name + viewer-role checkboxes limited to `MALE_ROLE_ID`/`FEMALE_ROLE_ID`; ロール作成 1枚 → modal for name + hex color, creates the role at the bottom and assigns it; 雰囲気写真の閲覧権 (`MOOD_PHOTO_ROLE_ID`) with a 24h image-post deadline enforced by a loop; サーバー絵文字追加 1枚 → modal for name + image `FileUpload`, creates a guild custom emoji). Tickets are the `users.mp_tickets` column granted on VC level-up |
| `/mp_give` `/mp_take` | `mp_shop.py` | Admin: grant/confiscate a member's MP tickets (logged to `MP_LOG_CHANNEL_ID` if set) |
| `/mp_list` | `mp_shop.py` | Admin: list members' MP ticket holdings (desc) |
| `/set_role_panel` | `role_switch.py` | Admin: place a panel with 雑談/恋愛 buttons; members self-switch between `ZERO_ROMANCE_ROLE_ID` and `ROMANCE_ROLE_ID` (mutually exclusive; 雑談 hides the configured category). Switching has a 2-week cooldown per user (`role_switch_cooldowns` table); profile-creation role assignment is exempt |
