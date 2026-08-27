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
| `DM_OPEN_ROLE_ID` / `DM_CLOSED_ROLE_ID` / `DM_ACQUAINTED_ROLE_ID` / `DM_ASK_ROLE_ID` | Roles auto-granted (mutually exclusive) from the profile wizard's 「DM・フレンド申請の可否」 select — 誰でもOK / DM× / 話したことあるなら / 直接聞いてもらってから respectively. Common to both 雑談 and 恋愛 profiles (optional) |
| `ADMIN_ROLE_ID` | Role ID that can use admin commands |
| `ERROR_LOG_CHANNEL_ID` | Text channel that `ERROR`+ logs are streamed to (via `core/log_to_discord.py`'s logging handler). Console/Railway logging still works regardless; no Discord streaming if unset (optional) |
| `JOIN_LEAVE_LOG_CHANNEL_ID` | Channel where server join/leave logs are posted as embeds (`cogs/logs/join_leave_log.py`; 入室=green, 退室=red). Defaults to `1530463807418273913` if unset |
| `VC_LOG_CHANNEL_ID` | Channel where VC join/leave/move logs are posted as embeds (`cogs/logs/vc_log.py`; 参加=green, 退出=red, 移動=blurple). No VC logging if unset (optional) |
| `VC_LOG_EXCLUDED_CHANNEL_IDS` | Comma-separated VC **or category** IDs excluded from VC logging (optional) |
| `MESSAGE_LOG_CHANNEL_ID` | Channel where message edit/delete logs are posted as embeds (`cogs/logs/message_log.py`; 編集=gold with before/after + jump link, 削除=red with content + attachment filenames). No message logging if unset (optional) |
| `MESSAGE_LOG_EXCLUDED_CHANNEL_IDS` | Comma-separated channel / category / forum-parent IDs excluded from message-edit logging (optional) |
| `DB_POOL_MIN` / `DB_POOL_MAX` | PostgreSQL connection-pool bounds used by `core/db_base.py` (default 1 / 10) |
| `EXCLUDED_CHANNEL_IDS` | Comma-separated VC IDs excluded from VC-time tracking |
| `VC_RANK_REDUCED_CATEGORY_IDS` | Comma-separated category IDs where VC time accrues at 1/3 rate (fractional carry) |
| `INTERVIEW_ROOM_CATEGORY_ID` | Category ID under which per-member interview rooms are created (optional) |
| `RECORDING_FORWARD_CHANNEL_ID` | Text channel recordings/審査 are forwarded to (no forwarding if unset) |
| `RECORDING_FORUM_MALE_ID` / `RECORDING_FORUM_FEMALE_ID` | Per-gender 審査 forums (posts as a new thread titled with the submitter's name). Male reviews go to MALE, female to FEMALE; falls back to the `RECORDING_FORWARD_CHANNEL_ID` text channel |
| `MALE_ROLE_ID` / `FEMALE_ROLE_ID` | Role IDs for the 1-on-1 call matching feature |
| `NEWCOMER_ROLE_ID` | Members with this role can't use call matching (blocked from recruiting, hidden from target lists, and can't accept). Also the target of the 1-week newcomer review (`onboarding/newcomer_review.py`). Optional |
| `MEMBER_ROLE_ID` | Role granted (and `NEWCOMER_ROLE_ID` removed) when a newcomer is approved in the 1-week review (`onboarding/newcomer_review.py`) |
| `NEWCOMER_REVIEW_FORUM_ID` | Forum where the 1-week newcomer review is posted (a thread per member, titled with their name, carrying their saved profile + メンバー化/BAN buttons). No review runs if unset |
| `WAITING_ROLE_ID` / `WAITING_CATEGORY_ID` | `onboarding/waiting_room.py`: auto-assigns the waiting role on join; hides every category except `WAITING_CATEGORY_ID` (visible category is view-only, no send); removed when the role is taken away |
| `REVIEW_ROLE_ID` | Role removed on 合格 verdict (defaults to `WAITING_ROLE_ID` if unset). On 合格 the reviewer removes it and adds `NEWCOMER_ROLE_ID`; on 不合格 the user is banned. Verdict button lives on the 審査結果 panel |
| `MALE_PROFILE_CHANNEL_ID` / `FEMALE_PROFILE_CHANNEL_ID` | On 合格, the bot posts in the member's personal interview/profile channel (no DM fallback — skipped if the channel is missing) directing them to write their profile in the gender-matching channel here (male via `MALE_ROLE_ID`, female via `FEMALE_ROLE_ID`) |
| `ZERO_ROMANCE_PROFILE_CHANNEL_ID` | On 合格, members holding `ZERO_ROMANCE_ROLE_ID` (雑談) are directed here instead of the gender channel — the 雑談-user-only profile channel (optional; takes priority over the gender-based channels) |
| `GUIDELINE_CHANNEL_ID` | Guideline channel linked in the 合格 message so the member checks the server info (optional) |
| `UNSUBMITTED_BAN_HOURS` | Hours after **joining** that a member still holding `REVIEW_ROLE_ID` (falls back to `WAITING_ROLE_ID`) and with no 審査 submission is auto-banned (`onboarding/recording_score/`'s `unsubmitted_ban_loop`, every 10 min). Unset/0 disables the feature. Members who joined more than 7 days ago (`UNSUBMITTED_MAX_AGE`), admins, and registered reviewers are never targeted |
| `CALL_CATEGORY_ID` | Category ID for created call rooms (optional) |
| `CALL_LOG_CHANNEL_ID` | Channel ID for call-matching accept/decline logs (no logging if unset) |
| `MAX_ROOMS_PER_FEMALE` / `MAX_ROOMS_PER_MALE` | Max concurrent call rooms per user (default 2) |
| `TRIAL_WARNING_SOUND` | Soundboard sound (ID or name) played in the trial-call VC at the 5-min-remaining warning; bot briefly joins the VC to send it (needs PyNaCl). No sound if unset |
| `LOBBY_VC_ID` | Trigger VC(s) for the join-to-create temp-VC feature (`voice/temp_vc.py`), comma-separated for multiple. Joining one makes a personal VC. Disabled if unset |
| `TEMP_VC_CATEGORY_ID` | Category for created temp VCs (defaults to the trigger VC's category) |

## Architecture

**Directory layout:**
```
core/            共通基盤（config / DB / ロギング / 管理者ベース）
cogs/            機能ごとの cog。ドメインでフォルダ分けしている
  onboarding/    waiting_room · rules · interview_room · recording_score/ · newcomer_review
  profile/       profile · role_switch · wizard/（プロフィール作成ウィザード）
  matching/      call_matching/
  voice/         temp_vc · vc_rank/（cog.py + rank_card.py）
  economy/       mp_shop/
  moderation/    preban
  logs/          join_leave_log · vc_log · message_log（編集・削除）
  misc/          talk
assets/          icons/*.png · topics.json
docs/            運用ドキュメント
```

**Entry point:** `main.py` — creates the bot, recursively loads all cogs from `cogs/`, syncs slash commands globally and to `MY_GUILD`, then starts `server.py` (FastAPI health check on port 8080) in a background thread. `setup_hook` also calls `setup_logging(self)` (`core/log_to_discord.py`) and `bot.run(..., log_handler=None)` so the app's own logging config wins.

**Logging:** Use `logging` (module-level `logger = logging.getLogger(__name__)`), not `print`. `core/log_to_discord.py` adds a console handler (Railway logs) to the root logger, plus — when `ERROR_LOG_CHANNEL_ID` is set — a `DiscordLogHandler` that streams `ERROR`+ records to that channel. The handler queues records and flushes them on the bot loop (batched, failure-swallowing) to avoid rate-limit/recursion loops.

**Configuration:** every environment variable is read in **`core/config.py`** and nowhere else. Cogs do `from core import config` and use `config.NEWCOMER_ROLE_ID` etc. Unset IDs come back as `0`, so call sites gate on `if config.XXX_ID:`. `core/config.py` also calls `dotenv.load_dotenv()`, exposes `env_int`/`env_float`/`env_str`/`env_id_list` helpers, the `DB_CONFIG` dict, and the asset paths `ASSETS_DIR`/`ICON_DIR`. Add a new setting there (plus `.env.example`) rather than calling `os.getenv` inside a cog.

**Cog loading:** `main.py`'s `_load_cogs()` walks `cogs/` **recursively**. For each entry:
- `*.py` file → loaded as an extension (must expose `setup`)
- directory whose `__init__.py` defines/re-exports `setup` → loaded as one extension; its submodules are **not** loaded directly (e.g. `onboarding/recording_score/`)
- directory whose `__init__.py` sets `COG_GROUP = True` → a grouping folder, so the loader descends into it (e.g. `cogs/onboarding/`)
- any other package → skipped, so helper packages like `cogs/profile/wizard/` are safe to nest

Names starting with `_` or `.` are ignored. Small features stay a single file; larger ones are packages.

**Package layout for large cogs:** `call_matching`, `recording_score`, and `mp_shop` are packages split as `__init__.py` (re-exports `setup`) · `cog.py` (the `commands.Cog`) · `ui.py` (Views/Modals/Buttons) · `db.py` (a `DatabaseBase` mixin, e.g. `CallDBMixin`, that the cog inherits) · sometimes `constants.py`/`embeds.py`. UI classes reference the cog at runtime via `interaction.client.get_cog(...)` (or a stored `self.cog`) and only type-hint it under `TYPE_CHECKING`, so there's no import cycle. The profile wizard is `cogs/profile/wizard/` (`data.py` choices+builders · `roles.py` role-granting · `views.py` Views/Modals · `__init__.py` re-exports the public API like `ProfileStartView`/`RoomPanelView`); it has no `setup`, so the loader skips it and `cogs/profile/profile.py`・`role_switch.py` import from it.

**Inheritance chain for DB-backed cogs:**
```
commands.Cog + DatabaseBase (core/db_base.py)
    └── AdminCogBase (core/admin_base.py)
```

`DatabaseBase` owns the PostgreSQL connection and provides `get_db()`. All cogs that touch the DB extend it. `AdminCogBase` adds the `ADMIN_ROLE_ID` constant for admin-gated cogs.

**Database:** PostgreSQL (service name `db` in Docker). Schema in `init.sql`:
- `users(user_id, vc_minutes_total, rank)`
- `vc_daily(user_id, day, minutes)` — per-day VC minutes written alongside `users.vc_minutes_total` by `voice/vc_rank/`; used for period sums like the newcomer review's last-7-days figure (also auto-created at cog load)
- `trial_invites(recruiter_id, target_id, invited_at)` — one-shot trial-call invite history (also auto-created at cog load)
- `call_blocks(blocker_id, blocked_id, created_at)` — call-matching blocks, hides both users from each other's target list (also auto-created at cog load)
- `bot_settings(key, value)` — server-wide switches; currently `male_review_mode` (`audio` / `profile`), written by `/set_male_mode` (auto-created at cog load)
- `call_room_limits(user_id, max_rooms)` — per-user room-cap override, set via the panel's 1-room-limit toggle button or `/call_limit_set` (also auto-created at cog load)

**DB credentials** default to host=`db`, user=`user`, pass=`password`, db=`postgres_db`, sslmode=`require` (matching the `compose.yml` service) and are overridable via `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASS`/`DB_NAME`/`DB_SSLMODE`. They are assembled in `core/config.py` as `DB_CONFIG` and consumed by `core/db_base.py`.

**Connection pooling:** `core/db_base.py` keeps one process-wide `psycopg2.pool.ThreadedConnectionPool` (`DB_POOL_MIN`/`DB_POOL_MAX`, default 1/10). `get_db()` pings each connection with `SELECT 1` on checkout (a connection the server dropped while idle still reports `closed == 0`, so it is only detectable by using it) and discards/reconnects up to 3 times before raising. It returns a `PooledConnection` wrapper whose `close()` returns the connection to the pool and whose `with` block commits (or rolls back on exception) **and** returns it — so `with self.get_db() as conn:` needs no explicit close. `main.py` calls `close_pool()` on shutdown. psycopg2 is synchronous, so never hold a connection across an `await`, and push anything hot off the event loop with `DatabaseBase.run_db(fn, *args)` (a thin `asyncio.to_thread`) — `voice/vc_rank/` does this for its per-message and per-minute writes. Blocking the loop past 3s is what produces `404 Unknown interaction (10062)` in views.

**Log-channel helper:** `core/log_channel.py`'s `send_log_embed(bot, channel_id, embed, label=...)` sends an embed to a configured log channel, swallowing missing-channel/permission errors. Used by `logs/join_leave_log.py`, `logs/message_log.py` and `logs/vc_log.py`.

**UI components** live next to the feature that owns them (`<feature>/ui.py`, or the wizard package). They are Discord `View` subclasses with persistent buttons (custom_id prefixed `persistent:`) so they survive bot restarts — custom_ids are independent of file paths, so moving modules never invalidates already-posted panels.

**Key background tasks (discord.py `@tasks.loop`):**
- `voice/vc_rank/` — tracks VC time every 1 min and updates users' rank
- `voice/temp_vc.py` — join-to-create temp VCs; sweeps empty temp VCs every 5 min (`temp_vcs` table); rename panel in the VC's text chat
- `onboarding/recording_score/` — `review_deadline_loop` every 10 min: mentions unscored reviewers at 12h and force-finalizes the review at 21h (`interview_reviews` table)
- `onboarding/recording_score/` — `unsubmitted_ban_loop` every 10 min (only when `UNSUBMITTED_BAN_HOURS` > 0): bans members who still hold the review/waiting role and haven't submitted (`_has_submitted`: `interview_done` / `interview_reviews` / `interview_verdicts`) `UNSUBMITTED_BAN_HOURS` after `joined_at`. No warning DM and no Discord notification — console logging only
- `onboarding/newcomer_review.py` — `review_loop` hourly: when a `NEWCOMER_ROLE_ID` holder has been in the server ≥7 days (`joined_at`), posts a thread to `NEWCOMER_REVIEW_FORUM_ID` with their saved profile embed (`member_profiles`, stored by the profile wizard) + メンバー化/BAN buttons (anyone can press). Dedup via `newcomer_reviews`; double-verdict guard via `newcomer_verdicts`. Approval removes `NEWCOMER_ROLE_ID` and adds `MEMBER_ROLE_ID`. The post also shows the member's **VC time over the last 7 days**, read from `vc_daily` via `VCRank.get_recent_vc_minutes()` (`bot.get_cog("VCRank")`; shows 不明 if unavailable)

## Slash Commands Reference

| Command | Cog | Description |
|---|---|---|
| `/topic` | `misc/talk.py` | Random discussion topic |
| `/rank` | `voice/vc_rank/` | Show VC/text rank card (Pillow image) |
| `/set_appeal_panel` | `onboarding/interview_room.py` | Admin: place the 男性/女性 panel — 女性 creates a per-user profile channel; 男性 follows the server-wide male mode (`/set_male_mode`): **録音あり** creates an appeal-recording channel (topic `interview_room:`, audio forwarded to `RECORDING_FORWARD_CHANNEL_ID`, review waits for audio + profile) or **プロフ審査のみ** creates a profile channel (topic `profile_room:`) while still granting `MALE_ROLE_ID`, so the review goes straight to the male forum via `on_profile_only(..., kind="m")`. The wizard reads the topic prefix to decide whether to wait for audio and the prefix/`MALE_ROLE_ID` to decide gender. A user who already has a channel from one male route is blocked from the other. Males submit a recording by posting audio (file or Discord voice message) in their interview channel; the 採点 review is forwarded to `RECORDING_FORWARD_CHANNEL_ID` only once BOTH the audio and the profile exist (order-independent; `recording_score.py` waits via the `pending_interview` table). Only members registered as reviewers (`interview_reviewers` table, managed via `/reviewer_add`·`/reviewer_remove`·`/reviewer_list`) can score; they rate 4 categories (0–2 each) via a RadioGroup modal, and once the number of scores reaches the registered reviewer count the average is posted mentioning `ADMIN_ROLE_ID` (`recording_scores`/`recording_results` tables). No result is posted while zero reviewers are registered. Each forwarded review is tracked in `interview_reviews` (with `created_at`); a 10-min `tasks.loop` (`review_deadline_loop`) mentions still-unscored reviewers 12h after submission and force-finalizes the result 21h after submission (3h before the announced 24h deadline). The 採点 message also carries a 採点状況 button showing which registered reviewers have/haven't scored that submission. Both channels get a "プロフィールを作成する" button that runs a profile wizard (`cogs/profile/wizard/`): modal for name/hobby/type + sequential ephemeral steps — selects (>25 options auto-split) plus two modals of 4 `RadioGroup`s each (血液型/結婚/出会い/同居人, 休日/酒/タバコ/恋愛距離). A 「DM・フレンド申請の基準」 select is asked in both 雑談 and 恋愛 flows and auto-grants the matching `DM_OPEN`/`DM_CLOSED`/`DM_ACQUAINTED` role. Posts the result as an embed |
| `/set_call_panel` | `matching/call_matching` | Admin: place a 1-on-1 call recruit panel — male↔female pick each other via paged select + modal message, the target accepts/declines via DM buttons; accept creates a private VC + text room, declining auto-registers a block (decliner → recruiter), both outcomes logged to `CALL_LOG_CHANNEL_ID` (no logging if unset). Also has a trial-call button: same flow but the room auto-closes after 30 min (warning mention at 5 min remaining, checked by a 1-min `tasks.loop` against channel `created_at`), and each recruiter can trial-invite a given member only once ever (`trial_invites` table). Panel also has a block-edit button (`call_blocks` table) opening a `CheckboxGroup` modal (blocked members pre-checked, check/uncheck to block/unblock, capped at 50 candidates); blocked pairs are hidden from each other's target list both ways. A room-limit toggle button (`call_room_limits` table) lets a user cap their own concurrent rooms at 1 (press again to restore the role default); members at their room cap are hidden from target lists |
| `/set_male_mode` `/male_mode` | `onboarding/interview_room.py` | Admin: switch (and show) the server-wide male review mode — 録音あり（面接） / プロフ審査のみ. Stored in `bot_settings` so it survives restarts; the panel's 男性 button switches behaviour immediately, but an already-posted panel keeps its old description until re-posted |
| `/reviewer_add` `/reviewer_remove` `/reviewer_list` | `onboarding/recording_score` | Admin: register/remove/list the members who score profile reviews (`interview_reviewers` table). The required reviewer count equals the number of registered reviewers |
| `/preban` `/preban_remove` `/preban_list` | `moderation/preban.py` | Admin: ban/unban by raw user ID (works for users not in the guild — Discord keeps the ban and blocks their join), and list current bans. Accepts multiple IDs/mentions separated by spaces, commas, or newlines (max 20 per call); already-banned IDs are reported instead of re-banned. All responses are ephemeral; actions are logged via `logging` |
| `/admin_block_list` | `matching/call_matching` | Admin: list all call-matching blocks (blocker → blocked), optional member filter |
| `/call_limit_set` `/call_limit_reset` `/call_limit_list` | `matching/call_matching` | Admin: set a member's concurrent 個通 room cap (1–20, `call_room_limits`), clear it back to the role default (`MAX_ROOMS_PER_MALE`/`MAX_ROOMS_PER_FEMALE`), and list every member with an override. Responses are ephemeral embeds showing the effective cap, the role default, and how many rooms the member currently holds. Same table the panel's 1-room-limit toggle writes, so an admin override and the user's own toggle overwrite each other |
| `/newcomer_list` | `matching/call_matching` | Admin: list members who have `NEWCOMER_ROLE_ID` |
| `/set_mp_panel` | `economy/mp_shop` | Admin: place an MP-ticket panel — check balance button + redeem select (お試し個通リセット → clears the user's `trial_invites`; 個人専用テキストチャット作成 → modal for name + viewer-role checkboxes limited to `MALE_ROLE_ID`/`FEMALE_ROLE_ID`; ロール作成 → modal for name + hex color, creates the role at the bottom and assigns it; カラーロール作成 (`COLOR_ROLE_COST`, 15) → modal for name + style RadioGroup (グラデーション/ホログラフィック); creates a role with `secondary_color`/`tertiary_color` — gradient uses a gender-based base (male=blue, female=red via `MALE_ROLE_ID`/`FEMALE_ROLE_ID`), holographic uses Discord's fixed 3-color preset; requires the server's boost-gated enhanced role colors (refunds on failure); 雰囲気写真の閲覧権 (`MOOD_PHOTO_ROLE_ID`) with a 24h image-post deadline enforced by a loop; サーバー絵文字追加 → modal for name + image `FileUpload`, creates a guild custom emoji). Costs live in `cogs/economy/mp_shop/constants.py`. Tickets are the `users.mp_tickets` column granted on VC level-up |
| `/mp_give` `/mp_take` | `economy/mp_shop` | Admin: grant/confiscate a member's MP tickets (logged to `MP_LOG_CHANNEL_ID` if set) |
| `/mp_list` | `economy/mp_shop` | Admin: list members' MP ticket holdings (desc) |
| `/set_role_panel` | `profile/role_switch.py` | Admin: place a panel with 雑談/恋愛 buttons; members self-switch between `ZERO_ROMANCE_ROLE_ID` and `ROMANCE_ROLE_ID` (mutually exclusive; 雑談 hides the configured category). Switching has a 2-week cooldown per user (`role_switch_cooldowns` table); profile-creation role assignment is exempt |
