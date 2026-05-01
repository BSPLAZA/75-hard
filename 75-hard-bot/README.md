# Luke — 75 Hard Accountability Bot

> ↗ Take-home submission for Arize AI: [PLACEHOLDER]

A Telegram bot that runs a 75 Hard challenge for a closed group of friends. Tracks the 5 daily tasks, posts a daily card with one-tap buttons, holds an LLM-powered DM with each participant, and surprises the group with milestone callouts and "spicy moment" highlights.

Built for a specific friend group ("Locked In, 75 Hard"). The code is participant-agnostic — all roster info comes from env vars, so the same bot can run a different group with no code changes.

## What it does

**The 5 daily tasks** (per 75 Hard canon):
1. Two 45-min workouts — one indoor, one outdoor
2. Drink a gallon of water (16 cups)
3. Follow a chosen diet — no alcohol, no cheat meals
4. Read 10 pages of non-fiction
5. Take a daily progress photo

**Two ways to log:**

- The daily card in the group chat with inline buttons:
  ```
  💧 Water +1   🏋️ Workout   📖 Read   📸 Photo   🍽️ Diet
  ```

- DM the bot in plain English (Claude Opus 4.7 with tools):
  ```
  "ran 3 miles outside"          → workout logged
  "had 4 cups of water"          → water +4
  "stayed on diet today"         → diet confirmed
  "ate chicken & rice, 40g protein" → tracked vs goal
  "fix my water to 8" / "undo that workout"
  "I forgot reading yesterday"   → backfill until midnight PT next day
  "show my transformation"       → before/after image
  "make me a timelapse"          → MP4 of all photos
  ```

**Vision support:** send a photo with a caption like "how much protein is in this?" — Luke describes the plate item-by-item with an estimate, asks if you want to log it.

**Daily rhythm** (mixed ET/PT by design):

| Time | Event |
|---|---|
| 7am ET | Morning card: AI greeting + yesterday recap image + bookshelf + today's card with buttons |
| 9am ET | DM nudge for users with incomplete yesterday tasks |
| midnight PT next day | Yesterday locks (no more backfill) |
| 9pm ET | Daily AI "spicy moment" sweep — posts one notable highlight or stays silent |
| 10pm in user's TZ | Same-day DM nudge (ET/CT/MT/PT supported) |
| 8pm ET Sun | Weekly digest image + AI reflection + transformation DMs |

**Easter eggs that fire automatically:**
- Day milestones (1, 7, 14, 21, 30, 50, 60, **69 "Nice."**, 75)
- Streak milestones (7/14/21/30/50/75-day perfect streaks)
- First-finisher of the day
- Squad complete (all active users done) with lock-in time
- Comeback (completed today after missing yesterday)
- Two people working out within 15 min of each other

## Architecture

```
bot/
├── main.py             # entry point — wires handlers, runs polling
├── config.py           # env-driven config (PARTICIPANTS, ORGANIZER, TZs, etc)
├── database.py         # async SQLite layer (aiosqlite)
├── handlers/           # Telegram update handlers
│   ├── onboarding.py   # /start conversation flow
│   ├── daily_card.py   # the pinned card + refresh
│   ├── water.py        # 💧 button + /water set
│   ├── workout.py      # 🏋️ flow
│   ├── reading.py      # 📖 flow + book/diet commands
│   ├── photo.py        # 📸 flow + DM photo router (vision vs save)
│   ├── diet.py         # 🍽️ button
│   ├── transformation.py # /transformation /timelapse
│   ├── feedback.py     # /feedback /bug /suggest
│   └── admin.py        # /admin_* (gated by ADMIN_USER_ID)
├── jobs/scheduler.py   # all daily/weekly cron jobs
├── templates/messages.py # static UI strings
├── utils/
│   ├── luke_chat.py    # Claude DM agent (24+ tools)
│   ├── luke_ai.py      # AI helpers: morning greeting, weekly reflection, spicy moments
│   ├── easter_eggs.py  # all 6 milestone/streak/squad-complete/etc triggers
│   ├── card_renderer.py    # the daily card text
│   ├── image_generator.py  # recap + weekly digest images (Pillow)
│   ├── bookshelf.py    # bookshelf image with covers
│   ├── books.py        # iTunes book search
│   ├── photo_transform.py  # before/after composite
│   ├── timelapse.py    # MP4 timelapse via ffmpeg
│   └── progress.py     # day-number + completion helpers
└── assets/             # fonts (Inter)

tests/                  # pytest — 98 tests
```

**Key architectural choices:**
- **Card-based day resolution.** `get_current_challenge_day(db)` returns the day number from the most recent `daily_cards` row. Avoids timezone bugs where `today_et()` crosses midnight differently from card-post time.
- **Conversation logging.** Every DM exchange is persisted to `conversation_log` for debugging and pattern review (`/admin_conversations`). Luke's per-user chat history lazy-hydrates from this on cold start.
- **Persistent settings.** `bot_settings` table holds `group_chat_id` and `group_invite_link` so they survive deploys.
- **Stranger-DM gate.** Only `dm_registered=1` participants can invoke the LLM (cost protection). Strangers get a polite turn-away pointing to `/start`.
- **Personal start_day.** `users.start_day` (default 1) tracks each user's personal Day 1. Late joiners get a higher value and finish later than the group.
- **DB-driven timezones.** `users.timezone` controls which 10pm nudge the user gets. Seeded from `USER_TIMEZONES_*` env on first boot, then mutable via Luke's `set_user_timezone` tool.

## Conversation audit script

`scripts/audit_conversations.py` is a read-only ranker over the `conversation_log` table. It pulls the last N days of DM turns, scores each one with a small set of regex heuristics for state-claim patterns ("logged 135g", "you're at 8 cups", etc.) cross-referenced against the turn's `tools_called` JSON, and surfaces the top suspects for human review. It catches phantom-action candidates (state claim with no tool fired) and a handful of other anomalies. The detection is regex-based and intentionally narrow; the productized version of this lives in tracing and eval platforms that work against span data instead of stringly-typed log rows. Useful here as a fast local pass; not a substitute for a real evaluator.

## Tech stack

- Python 3.11
- [python-telegram-bot](https://docs.python-telegram-bot.org/) v20+ (with job queue)
- [aiosqlite](https://github.com/omnilib/aiosqlite) for async SQLite
- [anthropic](https://github.com/anthropics/anthropic-sdk-python) — Claude Opus 4.7 for chat, vision, morning greeting, weekly reflection, spicy moments
- [Pillow](https://pillow.readthedocs.io/) — daily card images, recap, bookshelf, weekly digest
- ffmpeg — MP4 timelapse generation
- [Fly.io](https://fly.io/) for deployment (worker process, persistent volume)

## Local development

```bash
git clone https://github.com/BSPLAZA/75-hard.git
cd 75-hard/75-hard-bot

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, ADMIN_USER_ID, etc

.venv/bin/python -m bot.main
```

Run tests:

```bash
.venv/bin/python -m pytest tests/ -q
```

For active development against a separate test bot:

```bash
cp .env.test.example .env.test
# fill in your test bot token, etc

./run_test_bot.sh   # sources .env.test then runs bot
```

## Deployment (Fly.io)

```bash
fly deploy --strategy immediate
```

Config is in `fly.toml`. Secrets are stored on Fly:

```bash
fly secrets list -a lockedin-75hard-bot
fly secrets set ANTHROPIC_API_KEY=xxx -a lockedin-75hard-bot
```

The DB lives at `/data/75hard.db` on a Fly volume (encrypted at rest). Backups happen daily at 3am ET via `daily_backup_job`.

## Environment variables

| Name | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot API token from @BotFather |
| `ANTHROPIC_API_KEY` | Claude API key |
| `ADMIN_USER_ID` | Telegram user ID of the organizer (used by `/admin_*` gating) |
| `GROUP_CHAT_ID` | Telegram chat ID of the group (auto-detected if bot is added) |
| `CHALLENGE_START_DATE` | ISO date — Day 1 of the challenge |
| `DATABASE_PATH` | Path to SQLite DB file |
| `PARTICIPANTS` | CSV of participant names — onboarding accepts only these |
| `ORGANIZER` | Display name of the organizer in user-facing copy |
| `ALREADY_PAID` | CSV of names that skip the Venmo step in onboarding |
| `VENMO_USERNAME` | For the buy-in deeplink |
| `USER_TIMEZONES_ET` | CSV of names in US/Eastern — seeds users.timezone on first boot |
| `USER_TIMEZONES_CT` | CSV of names in US/Central |
| `USER_TIMEZONES_MT` | CSV of names in US/Mountain |
| `USER_TIMEZONES_PT` | CSV of names in US/Pacific |
| `BUY_IN` | Buy-in amount in dollars (default 75) |

## Slash commands

**User:** `/start` `/card` `/water set N` `/workout_undo` `/reread` `/setbook` `/setdiet` `/bookshelf` `/transformation` `/timelapse` `/fail` `/redeem` `/feedback` `/bug` `/suggest`

**Admin** (DM-only responses, gated by `ADMIN_USER_ID`):

- `/admin_status` — roster snapshot
- `/admin_health` — DB + AI latency + feature usage health
- `/admin_reset_day` — repost today's card
- `/admin_test_recap` `/admin_test_morning` `/admin_test_nudge` `/admin_test_digest` `/admin_test_spicy` — preview scheduled jobs
- `/admin_test_transform` — render the organizer's transformation composite
- `/admin_feedback [type]` — list pending feedback items
- `/admin_resolve <id>` — mark feedback resolved
- `/admin_announce <text>` — post as the bot to the group (preserves newlines)
- `/admin_conversations [name] [limit]` — dump recent DM exchanges with Luke
- `/admin_eliminate <name>` — knock a user out
- `/admin_confirm_payment <name>` — manual payment verification
- `/admin_reset_db` — wipe checkins/cards/books (backs up first)

## License

Personal project. No license — all rights reserved. Reach out if you want to use it for something.
