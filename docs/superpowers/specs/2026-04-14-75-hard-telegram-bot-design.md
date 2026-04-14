# 75 Hard Challenge — Telegram Bot Design Spec

**Date:** 2026-04-14
**Status:** Approved
**Challenge start:** 2026-04-15

## Overview

A Telegram bot for a 5-person 75 Hard challenge that serves as the single daily interaction surface. The bot posts one "daily card" message each morning that all participants interact with throughout the day. The card updates in-place as people complete tasks, keeping the group chat clean for actual conversation.

### Goals

1. **Zero-friction daily tracking** — completing a check-in should be faster than not doing it
2. **Social accountability without shame** — everyone sees the card filling in, nobody gets called out
3. **Clean group chat** — one updating card, not 30+ bot messages per day
4. **Adaptive personality** — the bot feels like a 6th group member, not a corporate tool
5. **Data for the long haul** — structured tracking that enables Phase C features (photo timelines, reading logs, dashboards)

### Non-Goals (POC)

- LLM-powered adaptive messages (use templates, add Claude later)
- Photo composites/grids
- Book cover art
- Smartwatch integration
- Web dashboard
- Financial calculation automation

## Participants

| Name | Tier | Paid |
|------|------|------|
| Bryan Plaza | $75 | TBD |
| Kat Voynarovski | $75 | No |
| Yumna Fatima | $75 | Yes |
| Gaurav Sindhu | $75 | No |
| Dev Rana | $75 | No |

**Total prize pool:** $375

## The 5 Rules

Every day, for 75 consecutive days, each participant must:

1. **Two 45-minute workouts** — one indoor, one outdoor
2. **Drink a gallon of water** — 16 cups (128 oz)
3. **Follow a diet** — participant's choice, no alcohol, no cheat meals
4. **Read 10 pages of non-fiction**
5. **Take a progress photo**

Failure on any single day = elimination from the challenge. The bot flags missed days; Bryan makes the final call on elimination (to allow for grace in edge cases like bot downtime).

## Tech Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.11+ | Best Telegram bot ecosystem |
| Bot framework | python-telegram-bot v20+ (async) | Most mature, built-in JobQueue, ConversationHandler |
| Database | SQLite | 5 users, no need for a server. Persistent via Fly.io volume |
| Hosting | Fly.io | Free tier covers this. Supports long-running workers + volumes |
| Bot creation | BotFather on Telegram | Standard Telegram bot registration |
| LLM (Phase C) | Claude API | Adaptive messages, weekly digests |

### Dependencies

```
python-telegram-bot[job-queue]>=20.0
aiosqlite
python-dotenv
```

## Architecture

```
75-hard-bot/
  bot/
    __init__.py
    main.py              # Entry point, Application setup
    config.py            # Environment variables, constants
    database.py          # SQLite setup, queries
    handlers/
      __init__.py
      onboarding.py      # /start, registration flow
      daily_card.py      # Morning card posting, button callbacks
      workout.py         # Workout logging flow
      water.py           # Water increment handler
      reading.py         # Reading conversation flow (DM)
      photo.py           # Photo submission handler (DM)
      diet.py            # Diet confirmation handler
      scoreboard.py      # Evening scoreboard
      admin.py           # /fail, /status, manual commands
    jobs/
      __init__.py
      scheduler.py       # Morning card, evening scoreboard, 11 PM nudge
    templates/
      messages.py        # All bot message templates
    utils/
      card_renderer.py   # Formats the daily card text
      progress.py        # Calculates completion status
  requirements.txt
  Procfile
  fly.toml
  .env.example
```

## Data Model

### users

| Column | Type | Description |
|--------|------|-------------|
| telegram_id | INTEGER PRIMARY KEY | Telegram user ID |
| name | TEXT NOT NULL | Display name |
| phone | TEXT | Phone number (from sign-up sheet) |
| tier | INTEGER DEFAULT 75 | Entry fee ($75) |
| paid | BOOLEAN DEFAULT FALSE | Payment received |
| active | BOOLEAN DEFAULT TRUE | Still in the challenge |
| failed_day | INTEGER | Day they failed (null if active) |
| dm_registered | BOOLEAN DEFAULT FALSE | Has the user /started the bot in DM |
| created_at | TIMESTAMP | Registration time |

### daily_checkins

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-increment |
| telegram_id | INTEGER | FK to users |
| day_number | INTEGER | 1-75 |
| date | DATE | Calendar date |
| workout_1_type | TEXT | run, lift, yoga, bike, swim, other |
| workout_1_location | TEXT | indoor, outdoor |
| workout_1_done | BOOLEAN DEFAULT FALSE | |
| workout_2_type | TEXT | |
| workout_2_location | TEXT | |
| workout_2_done | BOOLEAN DEFAULT FALSE | |
| water_cups | INTEGER DEFAULT 0 | 0-16 |
| diet_done | BOOLEAN DEFAULT FALSE | |
| reading_done | BOOLEAN DEFAULT FALSE | |
| book_title | TEXT | Current book |
| reading_takeaway | TEXT | Quote or takeaway |
| photo_done | BOOLEAN DEFAULT FALSE | |
| photo_file_id | TEXT | Telegram file ID for the photo |
| completed_at | TIMESTAMP | When all 5 tasks were done (null if incomplete) |

### daily_cards

| Column | Type | Description |
|--------|------|-------------|
| day_number | INTEGER PRIMARY KEY | 1-75 |
| date | DATE | Calendar date |
| message_id | INTEGER | Telegram message ID (for editing) |
| chat_id | INTEGER | Group chat ID |

## Interaction Design

### The Daily Card (Core UI)

One message. Posted at 7:00 AM ET. Stays in the group all day. Updates in-place as people tap buttons.

**Initial state (morning):**

```
DAY 1 / 75 — 5/5 STANDING — $375

Bryan    ..  .. 0/16  ..  ..  ..
Kat      ..  .. 0/16  ..  ..  ..
Yumna    ..  .. 0/16  ..  ..  ..
Gaurav   ..  .. 0/16  ..  ..  ..
Dev      ..  .. 0/16  ..  ..  ..

        W1  W2  WATER  PIC READ DIET
```

**Inline keyboard buttons (2 rows):**

```
Row 1: [💧 Water +1] [🏋️ Workout] [📖 Read]
Row 2: [📸 Photo]    [🍽️ Diet ✓]
```

**Mid-day state (after activity):**

```
DAY 1 / 75 — 5/5 STANDING — $375

Bryan    ✅  ..  ▓▓▓▓▓▓░░░░  6/16  ..  ✅  ..
Kat      ✅  ✅  ▓▓▓▓▓▓▓▓▓░  9/16  ✅  ..  ✅
Yumna    ..  ..  ▓▓░░░░░░░░  2/16  ..  ..  ..
Gaurav   ..  ..  ░░░░░░░░░░  0/16  ..  ..  ..
Dev      ✅  ..  ▓▓▓▓░░░░░░  4/16  ✅  ✅  ..

        W1  W2     WATER      PIC READ DIET
```

**End of day (complete):**

```
DAY 1 / 75 — 5/5 STILL STANDING — $375

Bryan    ✅  ✅  ▓▓▓▓▓▓▓▓▓▓ 16/16  ✅  ✅  ✅  ⭐
Kat      ✅  ✅  ▓▓▓▓▓▓▓▓▓▓ 16/16  ✅  ✅  ✅  ⭐
Yumna    ✅  ✅  ▓▓▓▓▓▓▓▓▓▓ 16/16  ✅  ✅  ✅  ⭐
Gaurav   ✅  ✅  ▓▓▓▓▓▓▓▓░░ 14/16  ✅  ✅  ✅
Dev      ✅  ✅  ▓▓▓▓▓▓▓▓▓▓ 16/16  ✅  ✅  ✅  ⭐

        W1  W2     WATER      PIC READ DIET
```

The ⭐ appears when ALL tasks (including 16/16 water) are complete.

**Character budget:** Each user row is ~55 chars. Header + 5 rows + legend + padding = ~450 chars. Well under the 4096 limit.

### Interaction: Workout

**Trigger:** User taps `[🏋️ Workout]` on the daily card.

**Flow:**
1. Bot answers the callback with a new message in the group:
   ```
   @Bryan — Log your workout:
   [🏃 Run] [🏋️ Lift] [🧘 Yoga] [🚴 Bike] [🏊 Swim] [💪 Other]
   ```
2. User taps type (e.g., `[🏃 Run]`). Message edits to:
   ```
   @Bryan — 🏃 Run
   [🌳 Outdoor] [🏠 Indoor]
   ```
3. User taps location. Message edits to confirmation + the daily card updates:
   ```
   🏃 Bryan — Outdoor Run · Workout 1/2 ✅
   ```

If both workouts are done, the confirmation says:
```
🏋️ Bryan — Indoor Lift · Both workouts done ✅✅
```

**Validation:** One workout must be outdoor, one must be indoor. If user tries to log two indoor workouts, bot prompts: "Your other workout was indoor. This one needs to be outdoor."

### Interaction: Water

**Trigger:** User taps `[💧 Water +1]` on the daily card.

**Flow:**
1. Bot identifies the user from `callback_query.from`
2. Increments their water_cups in the database
3. Edits the daily card message with updated water count and progress bar
4. Answers the callback with a brief popup: "💧 7/16"

**Progress bar rendering:** 10-character bar, maps 0-16 cups to 0-10 blocks:
- 0 cups: `░░░░░░░░░░`
- 8 cups: `▓▓▓▓▓░░░░░`
- 16 cups: `▓▓▓▓▓▓▓▓▓▓`

**Edge case:** User taps when already at 16/16. Bot answers callback: "You already hit your gallon! 🎉" No increment.

**Concurrency:** If two users tap simultaneously, both edits attempt. One will succeed, the other may get "message not modified" if the message hasn't updated yet. Solution: read from database (source of truth), not from the message text. Retry the edit once if it fails.

### Interaction: Reading

**Trigger:** User taps `[📖 Read]` on the daily card.

**Flow (ConversationHandler, in DM):**
1. Bot answers callback in group: popup "Check your DMs 📖"
2. Bot DMs user: "What book are you reading?"
3. User types: `Atomic Habits`
4. Bot DMs: "Share a takeaway, favorite quote, or what stuck with you:"
5. User types their takeaway
6. Bot updates the daily card (📖 ✅)
7. Bot posts a **reading card** in the group (this is the one separate message worth posting):

```
📖 Bryan is reading "Atomic Habits"

"You don't rise to the level of your goals. You fall to the level of your systems."

Bryan's take: This is why willpower-based approaches always fail.
```

**Why this posts as a separate message:** Reading cards are the richest content in the chat. They create conversation, inspire others, and build a collective reading journal over 75 days. They earn their space.

**If the user has already logged reading for today:** Bot DMs: "You already logged reading today! If you want to update your entry, type /reread."

### Interaction: Progress Photo

**Trigger:** User taps `[📸 Photo]` on the daily card.

**Flow:**
1. Bot answers callback in group: popup "Send your photo in DMs 📸"
2. Bot DMs user: "Send me your progress photo for Day X."
3. User sends a photo
4. Bot stores the `file_id` in the database
5. Bot DMs: "Day X photo saved! 📸"
6. Bot updates the daily card (📸 ✅)
7. Bot posts a brief notification in the group: `📸 Bryan checked in (3/5 today)`

**Privacy:** Photos are NEVER posted to the group unless the user explicitly shares them. The bot stores file_ids for the Phase C transformation timeline feature.

### Interaction: Diet

**Trigger:** User taps `[🍽️ Diet ✓]` on the daily card.

**Flow:**
1. Bot identifies user, updates database
2. Edits daily card (🍽️ ✅)
3. Answers callback with popup: "Diet logged ✅"

**One tap. No conversation. No DM.** This is the simplest interaction.

## Scheduled Jobs

### Morning Card — 7:00 AM ET daily

1. Calculate day number from start date (April 15)
2. Create daily_checkins rows for all active users
3. Format and post the daily card to the group
4. Store the message_id in daily_cards table (for editing)
5. Pin the message (replacing yesterday's pin)

### Evening Scoreboard — 10:00 PM ET daily

Post a summary message (separate from the card, which stays interactive):

```
📊 DAY 1 WRAP-UP — 5/5 STILL STANDING

All tasks complete: Bryan, Kat, Dev ⭐
Almost there: Yumna (missing: photo), Gaurav (missing: workout 2, water)

📖 Today's reads:
  Bryan — "Atomic Habits"
  Kat — "Can't Hurt Me"
  Dev — "Deep Work"

💰 $375 · Day 1 in the books. 74 to go.
```

### 11 PM Nudge — 11:00 PM ET daily

DM each user who has incomplete tasks:

```
Hey [name] — you have unchecked tasks for today:

  ⬜ Workout 2
  ⬜ Water (8/16)
  ⬜ Progress photo

If you've done them, log them now.
You can also backfill until noon tomorrow.
```

### Noon Cutoff — 12:00 PM ET daily

Lock the previous day's checkins. Any incomplete tasks become permanently incomplete. If a user has ANY incomplete task for a locked day, they are flagged for review (not auto-eliminated — Bryan decides).

## Onboarding Flow

### Step 1: Bot Setup

Bryan creates a Telegram group, adds all 5 participants, then adds the bot. Bot needs admin permissions: `can_pin_messages`.

### Step 2: Welcome Message

When the bot is added to the group, it posts:

```
👋 I'm the 75 Hard bot. I'll be tracking your challenge starting tomorrow.

Before we begin, I need each of you to DM me once so I can send you
private check-ins (photos, reading prompts, reminders).

Tap this link to start: t.me/[bot_username]?start=register

Waiting for: Bryan, Kat, Yumna, Gaurav, Dev (0/5)
```

This message updates as people register:

```
Waiting for: Gaurav, Dev (3/5 registered)
```

### Step 3: DM Registration

When a user DMs `/start register` to the bot:

1. Bot asks: "What's your name?"
2. User types their name. Bot fuzzy-matches against the participant list (Bryan, Kat, Yumna, Gaurav, Dev)
3. If match found: "Welcome, Bryan! You're registered for 75 Hard. I'll send you check-in prompts here and track your progress in the group."
4. If no match: "I don't see that name on the participant list. Ask Bryan to add you."
5. Bot stores the Telegram user ID → participant mapping
6. Bot updates the welcome message in the group

### Step 4: All Registered

When 5/5 are registered:

```
✅ Everyone's in! 75 Hard starts tomorrow, April 15.

I'll post the first daily card at 7:00 AM ET.
Check the pinned message for rules and FAQ.

Let's do this. 💪
```

## Pinned Message: Rules & FAQ

Pinned permanently in the group:

```
📌 75 HARD — RULES & FAQ

━━━ THE RULES ━━━
Every day for 75 days. Miss one task, you're out.

1. 🏋️  Two 45-min workouts (one indoor, one outdoor)
2. 💧  Drink a gallon of water (16 cups)
3. 🍽️  Follow your diet (your choice — no alcohol, no cheat meals)
4. 📖  Read 10 pages of non-fiction
5. 📸  Take a progress photo

━━━ HOW TO USE THE BOT ━━━
Each morning I post a daily card with buttons:
  💧 Water +1  — tap each time you drink a cup
  🏋️ Workout   — tap to log type + indoor/outdoor
  📖 Read       — tap and I'll DM you for book + takeaway
  📸 Photo      — tap and send your photo in DMs
  🍽️ Diet       — tap to confirm you followed your diet

━━━ FAQ ━━━
Q: What if I work out after midnight?
A: Log it for the day you're in. If it's 1 AM Tuesday, it counts for Tuesday.

Q: What if I forget to log something?
A: You can backfill until noon the next day. I'll remind you at 11 PM
   if you have unchecked tasks.

Q: What counts as a "cup" of water?
A: 8 oz / ~250 ml. A gallon = 16 cups = 128 oz.

Q: Can I change my diet mid-challenge?
A: Yes. The rule is "follow A diet." Whatever you commit to, follow it.
   No alcohol and no cheat meals are non-negotiable.

Q: What if I fail?
A: DM me /fail. You'll get $1 back for each day completed. The rest
   stays in the prize pool. You stay in the group to cheer everyone on.

Q: Can I see my stats?
A: DM me /stats for your personal progress breakdown.

Q: What if the bot goes down?
A: Bryan will fix it. Log your tasks when it's back up. Honor system
   in the meantime.

━━━ STAKES ━━━
💰 $75 buy-in · $375 total prize pool
Winners split the pot from those who didn't make it.
If everyone finishes — everyone gets their $75 back. Respect.

━━━ START DATE ━━━
April 15, 2026 → June 28, 2026
```

## Edge Cases

### Late Logging (Grace Period)

- Tasks can be backfilled until **noon the next day**
- The bot tracks `date` per checkin, not real-time completion
- At noon, previous day locks. Any unchecked tasks are permanently incomplete
- The 11 PM DM nudge is the primary reminder

### Failure

1. User DMs bot `/fail`
2. Bot confirms: "Are you sure? This is final. Type CONFIRM to proceed."
3. User types `CONFIRM`
4. Bot updates user record: `active = FALSE`, `failed_day = current_day`
5. Bot posts to group:
   ```
   [name] completed [X] days of 75 Hard. That's further than
   most people ever get. 💪

   $[X] returned · $[75-X] stays in the prize pool
   Prize pool: $[total] · [N] still standing
   ```
6. User stays in the group. Their row disappears from the daily card.

### Race Conditions (Water Button)

Two users tap `[💧 Water +1]` at the same time:
1. Both callback queries arrive
2. Handler reads each user's count from SQLite (source of truth)
3. Increments and saves
4. Both attempt to edit the message
5. First edit succeeds. Second edit may succeed (different content) or fail with "message not modified"
6. On failure: re-read all counts from DB, re-render card, retry edit once

### Bot Downtime

If the bot goes down and comes back up:
- It checks the current day number
- If no daily card exists for today, it posts one (even if late)
- Existing check-in data in SQLite is preserved (Fly.io volume)
- The pinned FAQ covers this: "Log your tasks when it's back up"

### User Not Registered in DMs

If a user taps [📖 Read] or [📸 Photo] but hasn't DM'd the bot:
- Bot answers callback: "I can't DM you yet! Tap t.me/[bot_username] to start a chat with me first."

## Admin Commands

Available only to Bryan (hardcoded admin user ID):

| Command | Description |
|---------|-------------|
| `/admin_status` | Show full database state |
| `/admin_reset_day` | Repost today's card (if something breaks) |
| `/admin_mark [user] [task]` | Manually mark a task complete |
| `/admin_eliminate [user]` | Manually eliminate a participant |
| `/admin_announce [message]` | Post a message as the bot |

## Phase C Roadmap

Features to add during the challenge (weeks 1-4):

| Week | Feature | Description |
|------|---------|-------------|
| 1 | Claude personality | LLM-generated morning cards, context-aware encouragement |
| 1 | Easter eggs | Day milestones (7, 30, 50, 69, 75), early bird awards, simultaneous workouts |
| 2 | Weekly digest | Sunday recap with stats, reading log, trends |
| 2 | Photo timeline | DM users their Day 1 vs current side-by-side |
| 3 | Book covers | Auto-fetch cover art for reading cards |
| 3 | Web dashboard | Simple page showing group progress, reading log |
| 4 | Smartwatch | Apple Health shortcuts, Garmin API for auto-logging workouts |

## Hosting & Deployment

### Fly.io Configuration

```toml
# fly.toml
app = "75-hard-bot"
primary_region = "ewr"  # Newark, NJ (close to East Coast participants)

[build]
  builder = "paketobuildpacks/builder:base"

[mounts]
  source = "data"
  destination = "/data"

[env]
  DATABASE_PATH = "/data/75hard.db"
```

```
# Procfile
worker: python -m bot.main
```

### Environment Variables

```
TELEGRAM_BOT_TOKEN=<from BotFather>
ADMIN_USER_ID=<Bryan's Telegram user ID>
GROUP_CHAT_ID=<set after creating the group>
CHALLENGE_START_DATE=2026-04-15
ANTHROPIC_API_KEY=<for Phase C>
```

### Deployment Steps

1. Create bot via BotFather on Telegram
2. `flyctl launch` from project directory
3. `flyctl volumes create data --size 1` (1 GB persistent volume)
4. Set secrets: `flyctl secrets set TELEGRAM_BOT_TOKEN=xxx`
5. `flyctl deploy`
6. Add bot to Telegram group, grant admin permissions
7. Set GROUP_CHAT_ID after bot detects the group

## Success Criteria (POC)

The POC is successful when:
- [ ] Bot posts daily card at 7 AM ET
- [ ] All 5 button interactions work (water, workout, reading, photo, diet)
- [ ] Daily card updates in-place correctly
- [ ] Reading cards post to the group
- [ ] Photo submissions work via DM
- [ ] Evening scoreboard posts at 10 PM ET
- [ ] 11 PM nudge DMs go to users with incomplete tasks
- [ ] Pinned rules/FAQ message is in place
- [ ] All 5 participants have registered via DM
