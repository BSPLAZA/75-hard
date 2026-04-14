# 75 Hard Telegram Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot that posts a daily interactive card for a 5-person 75 Hard challenge, tracking workouts, water, diet, reading, and progress photos.

**Architecture:** Python async bot using python-telegram-bot v20+. SQLite for persistence. Single "daily card" message with inline keyboard buttons — users tap to log tasks, the card edits in-place. Scheduled jobs handle morning card, evening scoreboard, and late-night nudges.

**Tech Stack:** Python 3.11+, python-telegram-bot[job-queue]>=20.0, aiosqlite, SQLite, Fly.io

**Spec:** `docs/superpowers/specs/2026-04-14-75-hard-telegram-bot-design.md`

---

## File Map

```
75-hard-bot/                    # NEW directory at project root
  bot/
    __init__.py                 # Empty package init
    main.py                     # Entry point: build Application, register handlers, run polling
    config.py                   # Load env vars, define constants (participants, start date, etc.)
    database.py                 # SQLite schema creation, all DB query functions
    handlers/
      __init__.py               # Empty
      onboarding.py             # /start registration, welcome message, group detection
      daily_card.py             # Post daily card, handle all 5 button callbacks, /card command
      workout.py                # Workout type+location inline keyboard flow
      water.py                  # Water +1 callback, /water command for corrections
      reading.py                # ConversationHandler for reading flow (DM), reading cards
      photo.py                  # Photo DM handler, photo notification in group
      diet.py                   # Diet toggle callback
      feedback.py               # /feedback, /bug, /suggest commands
      admin.py                  # Admin-only commands (/admin_*, /fail)
    jobs/
      __init__.py               # Empty
      scheduler.py              # Schedule morning card, evening scoreboard, 11pm nudge, noon cutoff
    utils/
      __init__.py               # Empty
      card_renderer.py          # Render daily card text from DB state
      progress.py               # Water progress bar, completion checks, day calculation
  tests/
    __init__.py
    test_card_renderer.py       # Unit tests for card rendering
    test_progress.py            # Unit tests for progress bar, day calc, completion
    test_database.py            # Unit tests for DB operations
  requirements.txt
  Procfile
  fly.toml
  .env.example
  README.md
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `75-hard-bot/requirements.txt`
- Create: `75-hard-bot/.env.example`
- Create: `75-hard-bot/Procfile`
- Create: `75-hard-bot/fly.toml`
- Create: `75-hard-bot/bot/__init__.py`
- Create: `75-hard-bot/bot/handlers/__init__.py`
- Create: `75-hard-bot/bot/jobs/__init__.py`
- Create: `75-hard-bot/bot/utils/__init__.py`
- Create: `75-hard-bot/tests/__init__.py`

- [ ] **Step 1: Create the bot project directory structure**

```bash
mkdir -p 75-hard-bot/bot/handlers 75-hard-bot/bot/jobs 75-hard-bot/bot/utils 75-hard-bot/tests
touch 75-hard-bot/bot/__init__.py 75-hard-bot/bot/handlers/__init__.py 75-hard-bot/bot/jobs/__init__.py 75-hard-bot/bot/utils/__init__.py 75-hard-bot/tests/__init__.py
```

- [ ] **Step 2: Create requirements.txt**

```
# 75-hard-bot/requirements.txt
python-telegram-bot[job-queue]>=20.0,<22.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0
```

- [ ] **Step 3: Create .env.example**

```
# 75-hard-bot/.env.example
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
ADMIN_USER_ID=your-telegram-user-id
GROUP_CHAT_ID=set-after-adding-bot-to-group
CHALLENGE_START_DATE=2026-04-15
DATABASE_PATH=data/75hard.db
```

- [ ] **Step 4: Create Procfile**

```
# 75-hard-bot/Procfile
worker: python -m bot.main
```

- [ ] **Step 5: Create fly.toml**

```toml
# 75-hard-bot/fly.toml
app = "75-hard-bot"
primary_region = "ewr"

[build]
  builder = "paketobuildpacks/builder:base"

[mounts]
  source = "data"
  destination = "/data"

[env]
  DATABASE_PATH = "/data/75hard.db"
```

- [ ] **Step 6: Install dependencies locally**

```bash
cd 75-hard-bot && pip install -r requirements.txt
```

- [ ] **Step 7: Commit**

```bash
git add 75-hard-bot/
git commit -m "feat: scaffold 75 Hard bot project structure"
```

---

### Task 2: Config Module

**Files:**
- Create: `75-hard-bot/bot/config.py`

- [ ] **Step 1: Create config.py**

```python
# 75-hard-bot/bot/config.py
import os
from datetime import date
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))
CHALLENGE_START_DATE = date.fromisoformat(os.environ.get("CHALLENGE_START_DATE", "2026-04-15"))
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/75hard.db")

CHALLENGE_DAYS = 75
WATER_GOAL = 16  # cups
WORKOUT_TYPES = ["run", "lift", "yoga", "bike", "swim", "other"]
WORKOUT_LOCATIONS = ["outdoor", "indoor"]

# Pre-loaded participant names for registration matching
PARTICIPANTS = ["Bryan", "Kat", "Yumna", "Gaurav", "Dev"]

# Callback data prefixes (used to route inline button presses)
CB_WATER = "water_plus"
CB_WORKOUT = "workout_start"
CB_WORKOUT_TYPE = "wtype_"
CB_WORKOUT_LOC = "wloc_"
CB_READ = "read_start"
CB_READ_SAME = "read_same"
CB_READ_NEW = "read_new"
CB_PHOTO = "photo_start"
CB_DIET = "diet_toggle"
```

- [ ] **Step 2: Commit**

```bash
git add 75-hard-bot/bot/config.py
git commit -m "feat: add config module with env vars and constants"
```

---

### Task 3: Database Module

**Files:**
- Create: `75-hard-bot/bot/database.py`
- Create: `75-hard-bot/tests/test_database.py`

- [ ] **Step 1: Write failing database tests**

```python
# 75-hard-bot/tests/test_database.py
import asyncio
import pytest
from bot.database import Database

@pytest.fixture
def db():
    """Create an in-memory database for testing."""
    database = Database(":memory:")
    asyncio.get_event_loop().run_until_complete(database.init())
    yield database
    asyncio.get_event_loop().run_until_complete(database.close())

def test_add_and_get_user(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan", "9735551234"))
    user = loop.run_until_complete(db.get_user(12345))
    assert user["name"] == "Bryan"
    assert user["active"] == 1
    assert user["dm_registered"] == 0

def test_register_dm(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.register_dm(12345))
    user = loop.run_until_complete(db.get_user(12345))
    assert user["dm_registered"] == 1

def test_create_and_get_checkin(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.create_checkin(12345, 1, "2026-04-15"))
    checkin = loop.run_until_complete(db.get_checkin(12345, 1))
    assert checkin["water_cups"] == 0
    assert checkin["diet_done"] == 0

def test_increment_water(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.create_checkin(12345, 1, "2026-04-15"))
    new_count = loop.run_until_complete(db.increment_water(12345, 1))
    assert new_count == 1
    new_count = loop.run_until_complete(db.increment_water(12345, 1))
    assert new_count == 2

def test_water_caps_at_16(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.create_checkin(12345, 1, "2026-04-15"))
    loop.run_until_complete(db.set_water(12345, 1, 16))
    new_count = loop.run_until_complete(db.increment_water(12345, 1))
    assert new_count == 16  # capped, no increment

def test_toggle_diet(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.create_checkin(12345, 1, "2026-04-15"))
    result = loop.run_until_complete(db.toggle_diet(12345, 1))
    assert result is True  # toggled on
    result = loop.run_until_complete(db.toggle_diet(12345, 1))
    assert result is False  # toggled off

def test_log_workout(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.create_checkin(12345, 1, "2026-04-15"))
    num = loop.run_until_complete(db.log_workout(12345, 1, "run", "outdoor"))
    assert num == 1
    num = loop.run_until_complete(db.log_workout(12345, 1, "lift", "indoor"))
    assert num == 2

def test_get_active_users(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(111, "Bryan"))
    loop.run_until_complete(db.add_user(222, "Kat"))
    loop.run_until_complete(db.add_user(333, "Dev"))
    loop.run_until_complete(db.eliminate_user(222, 5))
    users = loop.run_until_complete(db.get_active_users())
    assert len(users) == 2
    names = [u["name"] for u in users]
    assert "Kat" not in names

def test_add_and_finish_book(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    loop.run_until_complete(db.set_current_book(12345, "Atomic Habits", 1))
    user = loop.run_until_complete(db.get_user(12345))
    assert user["current_book"] == "Atomic Habits"
    loop.run_until_complete(db.finish_book(12345, 20))
    loop.run_until_complete(db.set_current_book(12345, "Can't Hurt Me", 21))
    user = loop.run_until_complete(db.get_user(12345))
    assert user["current_book"] == "Can't Hurt Me"

def test_add_feedback(db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.add_user(12345, "Bryan"))
    fid = loop.run_until_complete(db.add_feedback(12345, "suggestion", "Add dark mode", "day 5"))
    assert fid == 1
    items = loop.run_until_complete(db.get_feedback())
    assert len(items) == 1
    assert items[0]["text"] == "Add dark mode"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd 75-hard-bot && python -m pytest tests/test_database.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'bot.database'`

- [ ] **Step 3: Implement database.py**

```python
# 75-hard-bot/bot/database.py
import aiosqlite
from datetime import datetime

class Database:
    def __init__(self, path: str):
        self.path = path
        self.db: aiosqlite.Connection | None = None

    async def init(self):
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self):
        if self.db:
            await self.db.close()

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                tier INTEGER DEFAULT 75,
                paid INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                failed_day INTEGER,
                dm_registered INTEGER DEFAULT 0,
                current_book TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS daily_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                date TEXT NOT NULL,
                workout_1_type TEXT,
                workout_1_location TEXT,
                workout_1_done INTEGER DEFAULT 0,
                workout_2_type TEXT,
                workout_2_location TEXT,
                workout_2_done INTEGER DEFAULT 0,
                water_cups INTEGER DEFAULT 0,
                diet_done INTEGER DEFAULT 0,
                reading_done INTEGER DEFAULT 0,
                book_title TEXT,
                reading_takeaway TEXT,
                photo_done INTEGER DEFAULT 0,
                photo_file_id TEXT,
                completed_at TEXT,
                UNIQUE(telegram_id, day_number)
            );
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                started_day INTEGER NOT NULL,
                finished_day INTEGER
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                type TEXT NOT NULL,
                text TEXT NOT NULL,
                context TEXT,
                status TEXT DEFAULT 'new',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS daily_cards (
                day_number INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                message_id INTEGER,
                chat_id INTEGER
            );
        """)
        await self.db.commit()

    # --- Users ---

    async def add_user(self, telegram_id: int, name: str, phone: str = None):
        await self.db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, name, phone) VALUES (?, ?, ?)",
            (telegram_id, name, phone),
        )
        await self.db.commit()

    async def get_user(self, telegram_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_users(self) -> list[dict]:
        async with self.db.execute("SELECT * FROM users ORDER BY created_at") as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def get_active_users(self) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM users WHERE active = 1 ORDER BY created_at"
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def register_dm(self, telegram_id: int):
        await self.db.execute(
            "UPDATE users SET dm_registered = 1 WHERE telegram_id = ?", (telegram_id,)
        )
        await self.db.commit()

    async def eliminate_user(self, telegram_id: int, day: int):
        await self.db.execute(
            "UPDATE users SET active = 0, failed_day = ? WHERE telegram_id = ?",
            (day, telegram_id),
        )
        await self.db.commit()

    async def get_unregistered_names(self) -> list[str]:
        async with self.db.execute(
            "SELECT name FROM users WHERE dm_registered = 0"
        ) as cursor:
            return [r["name"] for r in await cursor.fetchall()]

    # --- Daily Checkins ---

    async def create_checkin(self, telegram_id: int, day_number: int, date_str: str):
        await self.db.execute(
            "INSERT OR IGNORE INTO daily_checkins (telegram_id, day_number, date) VALUES (?, ?, ?)",
            (telegram_id, day_number, date_str),
        )
        await self.db.commit()

    async def get_checkin(self, telegram_id: int, day_number: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM daily_checkins WHERE telegram_id = ? AND day_number = ?",
            (telegram_id, day_number),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_checkins_for_day(self, day_number: int) -> list[dict]:
        async with self.db.execute(
            "SELECT dc.*, u.name FROM daily_checkins dc JOIN users u ON dc.telegram_id = u.telegram_id WHERE dc.day_number = ? AND u.active = 1 ORDER BY u.created_at",
            (day_number,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def increment_water(self, telegram_id: int, day_number: int) -> int:
        checkin = await self.get_checkin(telegram_id, day_number)
        if not checkin or checkin["water_cups"] >= 16:
            return checkin["water_cups"] if checkin else 0
        new_count = checkin["water_cups"] + 1
        await self.db.execute(
            "UPDATE daily_checkins SET water_cups = ? WHERE telegram_id = ? AND day_number = ?",
            (new_count, telegram_id, day_number),
        )
        await self.db.commit()
        return new_count

    async def set_water(self, telegram_id: int, day_number: int, cups: int):
        cups = max(0, min(16, cups))
        await self.db.execute(
            "UPDATE daily_checkins SET water_cups = ? WHERE telegram_id = ? AND day_number = ?",
            (cups, telegram_id, day_number),
        )
        await self.db.commit()

    async def toggle_diet(self, telegram_id: int, day_number: int) -> bool:
        checkin = await self.get_checkin(telegram_id, day_number)
        if not checkin:
            return False
        new_val = 0 if checkin["diet_done"] else 1
        await self.db.execute(
            "UPDATE daily_checkins SET diet_done = ? WHERE telegram_id = ? AND day_number = ?",
            (new_val, telegram_id, day_number),
        )
        await self.db.commit()
        await self._check_completion(telegram_id, day_number)
        return bool(new_val)

    async def log_workout(self, telegram_id: int, day_number: int, wtype: str, location: str) -> int:
        checkin = await self.get_checkin(telegram_id, day_number)
        if not checkin:
            return 0
        if not checkin["workout_1_done"]:
            await self.db.execute(
                "UPDATE daily_checkins SET workout_1_type=?, workout_1_location=?, workout_1_done=1 WHERE telegram_id=? AND day_number=?",
                (wtype, location, telegram_id, day_number),
            )
            await self.db.commit()
            await self._check_completion(telegram_id, day_number)
            return 1
        elif not checkin["workout_2_done"]:
            await self.db.execute(
                "UPDATE daily_checkins SET workout_2_type=?, workout_2_location=?, workout_2_done=1 WHERE telegram_id=? AND day_number=?",
                (wtype, location, telegram_id, day_number),
            )
            await self.db.commit()
            await self._check_completion(telegram_id, day_number)
            return 2
        return 2  # both already done

    async def undo_last_workout(self, telegram_id: int, day_number: int) -> bool:
        checkin = await self.get_checkin(telegram_id, day_number)
        if not checkin:
            return False
        if checkin["workout_2_done"]:
            await self.db.execute(
                "UPDATE daily_checkins SET workout_2_type=NULL, workout_2_location=NULL, workout_2_done=0 WHERE telegram_id=? AND day_number=?",
                (telegram_id, day_number),
            )
        elif checkin["workout_1_done"]:
            await self.db.execute(
                "UPDATE daily_checkins SET workout_1_type=NULL, workout_1_location=NULL, workout_1_done=0 WHERE telegram_id=? AND day_number=?",
                (telegram_id, day_number),
            )
        else:
            return False
        await self.db.commit()
        return True

    async def log_reading(self, telegram_id: int, day_number: int, book_title: str, takeaway: str):
        await self.db.execute(
            "UPDATE daily_checkins SET reading_done=1, book_title=?, reading_takeaway=? WHERE telegram_id=? AND day_number=?",
            (book_title, takeaway, telegram_id, day_number),
        )
        await self.db.commit()
        await self._check_completion(telegram_id, day_number)

    async def log_photo(self, telegram_id: int, day_number: int, file_id: str):
        await self.db.execute(
            "UPDATE daily_checkins SET photo_done=1, photo_file_id=? WHERE telegram_id=? AND day_number=?",
            (file_id, telegram_id, day_number),
        )
        await self.db.commit()
        await self._check_completion(telegram_id, day_number)

    async def _check_completion(self, telegram_id: int, day_number: int):
        checkin = await self.get_checkin(telegram_id, day_number)
        if not checkin:
            return
        all_done = (
            checkin["workout_1_done"]
            and checkin["workout_2_done"]
            and checkin["water_cups"] >= 16
            and checkin["diet_done"]
            and checkin["reading_done"]
            and checkin["photo_done"]
        )
        if all_done and not checkin["completed_at"]:
            await self.db.execute(
                "UPDATE daily_checkins SET completed_at = ? WHERE telegram_id = ? AND day_number = ?",
                (datetime.now().isoformat(), telegram_id, day_number),
            )
            await self.db.commit()

    # --- Books ---

    async def set_current_book(self, telegram_id: int, title: str, day_number: int):
        await self.db.execute(
            "UPDATE users SET current_book = ? WHERE telegram_id = ?",
            (title, telegram_id),
        )
        await self.db.execute(
            "INSERT INTO books (telegram_id, title, started_day) VALUES (?, ?, ?)",
            (telegram_id, title, day_number),
        )
        await self.db.commit()

    async def finish_book(self, telegram_id: int, day_number: int):
        await self.db.execute(
            "UPDATE books SET finished_day = ? WHERE telegram_id = ? AND finished_day IS NULL",
            (day_number, telegram_id),
        )
        await self.db.commit()

    # --- Daily Cards ---

    async def save_card(self, day_number: int, date_str: str, message_id: int, chat_id: int):
        await self.db.execute(
            "INSERT OR REPLACE INTO daily_cards (day_number, date, message_id, chat_id) VALUES (?, ?, ?, ?)",
            (day_number, date_str, message_id, chat_id),
        )
        await self.db.commit()

    async def get_card(self, day_number: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM daily_cards WHERE day_number = ?", (day_number,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_card_by_message_id(self, message_id: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM daily_cards WHERE message_id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # --- Feedback ---

    async def add_feedback(self, telegram_id: int, fb_type: str, text: str, context: str = None) -> int:
        cursor = await self.db.execute(
            "INSERT INTO feedback (telegram_id, type, text, context) VALUES (?, ?, ?, ?)",
            (telegram_id, fb_type, text, context),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def get_feedback(self, fb_type: str = None, status: str = "new") -> list[dict]:
        if fb_type:
            sql = "SELECT f.*, u.name FROM feedback f LEFT JOIN users u ON f.telegram_id = u.telegram_id WHERE f.type = ? AND f.status = ? ORDER BY f.created_at DESC"
            params = (fb_type, status)
        else:
            sql = "SELECT f.*, u.name FROM feedback f LEFT JOIN users u ON f.telegram_id = u.telegram_id WHERE f.status = ? ORDER BY f.created_at DESC"
            params = (status,)
        async with self.db.execute(sql, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def resolve_feedback(self, feedback_id: int, status: str):
        await self.db.execute(
            "UPDATE feedback SET status = ? WHERE id = ?", (status, feedback_id)
        )
        await self.db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd 75-hard-bot && python -m pytest tests/test_database.py -v
```

Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add 75-hard-bot/bot/database.py 75-hard-bot/tests/test_database.py
git commit -m "feat: add database module with full CRUD operations and tests"
```

---

### Task 4: Progress Utilities

**Files:**
- Create: `75-hard-bot/bot/utils/progress.py`
- Create: `75-hard-bot/tests/test_progress.py`

- [ ] **Step 1: Write failing tests for progress utilities**

```python
# 75-hard-bot/tests/test_progress.py
from datetime import date
from bot.utils.progress import water_bar, get_day_number, is_all_complete, get_missing_tasks

def test_water_bar_empty():
    assert water_bar(0) == "░░░░░░░░░░"

def test_water_bar_half():
    assert water_bar(8) == "▓▓▓▓▓░░░░░"

def test_water_bar_full():
    assert water_bar(16) == "▓▓▓▓▓▓▓▓▓▓"

def test_water_bar_partial():
    assert water_bar(5) == "▓▓▓░░░░░░░"

def test_day_number_start():
    assert get_day_number(date(2026, 4, 15), date(2026, 4, 15)) == 1

def test_day_number_day_10():
    assert get_day_number(date(2026, 4, 15), date(2026, 4, 24)) == 10

def test_day_number_before_start():
    assert get_day_number(date(2026, 4, 15), date(2026, 4, 14)) == 0

def test_day_number_after_end():
    assert get_day_number(date(2026, 4, 15), date(2026, 6, 29)) == 76

def test_is_all_complete_true():
    checkin = {
        "workout_1_done": 1, "workout_2_done": 1,
        "water_cups": 16, "diet_done": 1,
        "reading_done": 1, "photo_done": 1,
    }
    assert is_all_complete(checkin) is True

def test_is_all_complete_missing_water():
    checkin = {
        "workout_1_done": 1, "workout_2_done": 1,
        "water_cups": 14, "diet_done": 1,
        "reading_done": 1, "photo_done": 1,
    }
    assert is_all_complete(checkin) is False

def test_get_missing_tasks():
    checkin = {
        "workout_1_done": 1, "workout_2_done": 0,
        "water_cups": 8, "diet_done": 1,
        "reading_done": 0, "photo_done": 1,
    }
    missing = get_missing_tasks(checkin)
    assert "Workout 2" in missing
    assert "Water (8/16)" in missing
    assert "Reading" in missing
    assert len(missing) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd 75-hard-bot && python -m pytest tests/test_progress.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement progress.py**

```python
# 75-hard-bot/bot/utils/progress.py
from datetime import date

WATER_GOAL = 16
BAR_LENGTH = 10


def water_bar(cups: int) -> str:
    filled = round(cups / WATER_GOAL * BAR_LENGTH)
    filled = max(0, min(BAR_LENGTH, filled))
    return "▓" * filled + "░" * (BAR_LENGTH - filled)


def get_day_number(start_date: date, today: date) -> int:
    delta = (today - start_date).days
    return delta + 1  # Day 1 on start_date


def is_all_complete(checkin: dict) -> bool:
    return bool(
        checkin["workout_1_done"]
        and checkin["workout_2_done"]
        and checkin["water_cups"] >= WATER_GOAL
        and checkin["diet_done"]
        and checkin["reading_done"]
        and checkin["photo_done"]
    )


def get_missing_tasks(checkin: dict) -> list[str]:
    missing = []
    if not checkin["workout_1_done"]:
        missing.append("Workout 1")
    if not checkin["workout_2_done"]:
        missing.append("Workout 2")
    if checkin["water_cups"] < WATER_GOAL:
        missing.append(f"Water ({checkin['water_cups']}/{WATER_GOAL})")
    if not checkin["reading_done"]:
        missing.append("Reading")
    if not checkin["photo_done"]:
        missing.append("Progress photo")
    if not checkin["diet_done"]:
        missing.append("Diet")
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd 75-hard-bot && python -m pytest tests/test_progress.py -v
```

Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add 75-hard-bot/bot/utils/progress.py 75-hard-bot/tests/test_progress.py
git commit -m "feat: add progress utilities with water bar, day calc, completion checks"
```

---

### Task 5: Card Renderer

**Files:**
- Create: `75-hard-bot/bot/utils/card_renderer.py`
- Create: `75-hard-bot/tests/test_card_renderer.py`

- [ ] **Step 1: Write failing tests for card rendering**

```python
# 75-hard-bot/tests/test_card_renderer.py
from bot.utils.card_renderer import render_daily_card

def _make_checkin(name, w1=0, w2=0, water=0, photo=0, read=0, diet=0):
    return {
        "name": name,
        "workout_1_done": w1, "workout_2_done": w2,
        "water_cups": water, "diet_done": diet,
        "reading_done": read, "photo_done": photo,
    }

def test_render_empty_card():
    checkins = [
        _make_checkin("Bryan"),
        _make_checkin("Kat"),
    ]
    text = render_daily_card(1, 2, 150, checkins)
    assert "DAY 1 / 75" in text
    assert "2/2 STANDING" in text
    assert "$150" in text
    assert "Bryan" in text
    assert "Kat" in text
    assert "0/16" in text

def test_render_partial_card():
    checkins = [
        _make_checkin("Bryan", w1=1, water=8, read=1),
        _make_checkin("Kat", w1=1, w2=1, water=16, photo=1, read=1, diet=1),
    ]
    text = render_daily_card(5, 2, 150, checkins)
    assert "DAY 5 / 75" in text
    # Bryan has partial
    assert "8/16" in text
    # Kat has everything — should get star
    assert "⭐" in text

def test_render_card_all_complete():
    checkins = [
        _make_checkin("Bryan", 1, 1, 16, 1, 1, 1),
    ]
    text = render_daily_card(10, 1, 75, checkins)
    assert "STILL STANDING" in text
    assert "⭐" in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd 75-hard-bot && python -m pytest tests/test_card_renderer.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement card_renderer.py**

```python
# 75-hard-bot/bot/utils/card_renderer.py
from bot.utils.progress import water_bar, is_all_complete

TICK = "✅"
EMPTY = ".."
STAR = "⭐"


def render_daily_card(day_number: int, active_count: int, prize_pool: int, checkins: list[dict]) -> str:
    all_done = all(is_all_complete(c) for c in checkins) and len(checkins) > 0
    standing_text = "STILL STANDING" if all_done else "STANDING"

    header = f"DAY {day_number} / 75 — {active_count}/{active_count} {standing_text} — ${prize_pool}"
    lines = [header, ""]

    max_name_len = max((len(c["name"]) for c in checkins), default=5)

    for c in checkins:
        name = c["name"].ljust(max_name_len)
        w1 = TICK if c["workout_1_done"] else EMPTY
        w2 = TICK if c["workout_2_done"] else EMPTY
        bar = water_bar(c["water_cups"])
        water_text = f"{c['water_cups']}/16"
        pic = TICK if c["photo_done"] else EMPTY
        read = TICK if c["reading_done"] else EMPTY
        diet = TICK if c["diet_done"] else EMPTY
        star = f"  {STAR}" if is_all_complete(c) else ""

        lines.append(f"{name}  {w1}  {w2}  {bar} {water_text:>5}  {pic}  {read}  {diet}{star}")

    lines.append("")
    col_header = " " * max_name_len + "  W1  W2     WATER      PIC READ DIET"
    lines.append(col_header)

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd 75-hard-bot && python -m pytest tests/test_card_renderer.py -v
```

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add 75-hard-bot/bot/utils/card_renderer.py 75-hard-bot/tests/test_card_renderer.py
git commit -m "feat: add card renderer with progress bars and star completions"
```

---

### Task 6: Message Templates

**Files:**
- Create: `75-hard-bot/bot/templates/messages.py`
- Create: `75-hard-bot/bot/templates/__init__.py`

- [ ] **Step 1: Create templates package init**

```bash
touch 75-hard-bot/bot/templates/__init__.py
```

- [ ] **Step 2: Create messages.py with all bot message templates**

```python
# 75-hard-bot/bot/templates/messages.py

WELCOME_GROUP = """👋 I'm the 75 Hard bot. I'll be tracking your challenge starting tomorrow.

Before we begin, I need each of you to DM me once so I can send you private check-ins (photos, reading prompts, reminders).

Tap this link to start: t.me/{bot_username}?start=register

Waiting for: {waiting_names} ({registered}/{total})"""

WELCOME_ALL_REGISTERED = """✅ Everyone's in! 75 Hard starts tomorrow, April 15.

I'll post the first daily card at 7:00 AM ET.
Check the pinned message for rules and FAQ.

Let's do this. 💪"""

DM_REGISTRATION_ASK_NAME = "Welcome! What's your name? I'll match you to the participant list."

DM_REGISTRATION_SUCCESS = "Welcome, {name}! You're registered for 75 Hard. I'll send you check-in prompts here and track your progress in the group."

DM_REGISTRATION_NOT_FOUND = "I don't see that name on the participant list. Ask Bryan to add you."

DM_REGISTRATION_ALREADY = "You're already registered, {name}! Nothing to do here."

# Workout
WORKOUT_PICK_TYPE = "@{username} — Log your workout:"
WORKOUT_PICK_LOCATION = "@{username} — {emoji} {wtype}"
WORKOUT_LOGGED = "{emoji} {name} — {location} {wtype} · Workout {num}/2 ✅"
WORKOUT_BOTH_DONE = "{emoji} {name} — {location} {wtype} · Both workouts done ✅✅"
WORKOUT_WRONG_LOCATION = "Your other workout was {other_loc}. This one needs to be {needed_loc}."
WORKOUT_ALREADY_DONE = "You've already logged both workouts today! Use /workout_undo to fix a mistake."

# Reading
READ_CHECK_DM = "Check your DMs 📖"
READ_SAME_BOOK = 'Still reading "{book}"?'
READ_ASK_BOOK = "What book are you reading?"
READ_ASK_TAKEAWAY = "Share a takeaway, favorite quote, or what stuck with you:"
READ_ALREADY_DONE = "You already logged reading today! If you want to update your entry, type /reread."

READ_CARD = """📖 {name} is reading "{book}" (day {days_with_book} with this book)

"{takeaway}"

{name}'s take: {takeaway}"""

READ_CARD_NEW_BOOK = """📖 {name} finished "{old_book}" after {old_days} days! Now starting "{new_book}"

"{takeaway}"

{name}'s take: {takeaway}"""

# Photo
PHOTO_CHECK_DM = "Send your photo in DMs 📸"
PHOTO_ASK = "Send me your progress photo for Day {day}."
PHOTO_SAVED = "Day {day} photo saved! 📸"
PHOTO_UPDATED = "Day {day} photo updated! 📸"
PHOTO_NOT_REGISTERED = "I can't DM you yet! Tap t.me/{bot_username}?start=register to start a chat with me first."
PHOTO_GROUP_NOTIFY = "📸 {name} checked in ({count}/{total} today)"
PHOTO_NEED_PHOTO = "I need a photo, not a file! Send me a picture."

# Diet
DIET_ON = "Diet logged ✅"
DIET_OFF = "Diet un-logged. Re-confirm when ready."

# Water
WATER_POPUP = "💧 {cups}/16"
WATER_FULL = "You already hit your gallon! 🎉"
WATER_SET = "Water set to {cups}/16 cups."

# Scoreboard
SCOREBOARD = """📊 DAY {day} WRAP-UP — {active}/{active} STILL STANDING

{complete_section}
{almost_section}

📖 Today's reads:
{reads_section}

💰 ${pool} · Day {day} in the books. {remaining} to go."""

# Nudge
NUDGE = """Hey {name} — you have unchecked tasks for today:

{missing_list}

If you've done them, log them now.
You can also backfill until noon tomorrow."""

# Failure
FAIL_CONFIRM = "Are you sure? This is final. Type CONFIRM to proceed."
FAIL_DONE = """{name} completed {days} days of 75 Hard. That's further than most people ever get. 💪

${returned} returned · ${remaining} stays in the prize pool
Prize pool: ${pool} · {active} still standing"""

# Feedback
FEEDBACK_CONFIRM = "Got it — logged your {type}. Bryan will see it. 👍"
FEEDBACK_HEADER = "📋 Open {type}:\n\n"
FEEDBACK_ITEM = "#{id} {name}: \"{text}\"\n"
FEEDBACK_RESOLVED = "Your {type} was addressed! Thanks for making the bot better."

# Card expired
CARD_EXPIRED = "This card has expired. Use today's card ☝️ or type /card to jump to it."

# Pinned FAQ — stored as a constant for posting
PINNED_FAQ = """📌 75 HARD — RULES & FAQ

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
A: You can backfill until noon the next day. I'll remind you at 11 PM if you have unchecked tasks.

Q: What counts as a "cup" of water?
A: 8 oz / ~250 ml. A gallon = 16 cups = 128 oz.

Q: Can I change my diet mid-challenge?
A: Yes. The rule is "follow A diet." Whatever you commit to, follow it. No alcohol and no cheat meals are non-negotiable.

Q: What if I fail?
A: DM me /fail. You'll get $1 back for each day completed. The rest stays in the prize pool. You stay in the group to cheer everyone on.

Q: I tapped water too many times. How do I fix it?
A: Type /water set [number] to correct your cup count.

Q: I logged the wrong workout. How do I fix it?
A: Type /workout_undo to clear your last workout, then re-log it.

Q: Can I see my stats?
A: DM me /stats for your personal progress breakdown.

Q: I have an idea to make this bot better.
A: Type /suggest [your idea] and Bryan will see it.

Q: Something is broken.
A: Type /bug [what happened] and Bryan will fix it.

Q: Where is today's card? I can't find it.
A: Type /card and I'll link you to it. It's also pinned.

Q: What if the bot goes down?
A: Bryan will fix it. Log your tasks when it's back up. Honor system in the meantime.

━━━ STAKES ━━━
💰 $75 buy-in · $375 total prize pool
Winners split the pot from those who didn't make it.
If everyone finishes — everyone gets their $75 back. Respect.

━━━ START DATE ━━━
April 15, 2026 → June 28, 2026"""
```

- [ ] **Step 3: Commit**

```bash
git add 75-hard-bot/bot/templates/
git commit -m "feat: add all message templates for bot interactions"
```

---

### Task 7: Onboarding Handler

**Files:**
- Create: `75-hard-bot/bot/handlers/onboarding.py`

- [ ] **Step 1: Implement onboarding.py**

```python
# 75-hard-bot/bot/handlers/onboarding.py
from difflib import SequenceMatcher
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, ConversationHandler, filters

from bot.config import PARTICIPANTS, GROUP_CHAT_ID
from bot.templates.messages import (
    DM_REGISTRATION_ASK_NAME, DM_REGISTRATION_SUCCESS,
    DM_REGISTRATION_NOT_FOUND, DM_REGISTRATION_ALREADY,
    WELCOME_GROUP, WELCOME_ALL_REGISTERED,
)

AWAITING_NAME = 0


def _fuzzy_match(name: str, candidates: list[str]) -> str | None:
    name_lower = name.strip().lower()
    best_match = None
    best_ratio = 0.0
    for c in candidates:
        ratio = SequenceMatcher(None, name_lower, c.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = c
    return best_match if best_ratio >= 0.5 else None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start in DMs — begin registration."""
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)

    if user and user["dm_registered"]:
        await update.message.reply_text(
            DM_REGISTRATION_ALREADY.format(name=user["name"])
        )
        return ConversationHandler.END

    await update.message.reply_text(DM_REGISTRATION_ASK_NAME)
    return AWAITING_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed their name — match against participant list."""
    db = context.bot_data["db"]
    typed_name = update.message.text.strip()
    matched = _fuzzy_match(typed_name, PARTICIPANTS)

    if not matched:
        await update.message.reply_text(DM_REGISTRATION_NOT_FOUND)
        return ConversationHandler.END

    # Check if already registered by someone else
    existing = None
    all_users = await db.get_all_users()
    for u in all_users:
        if u["name"] == matched and u["dm_registered"]:
            await update.message.reply_text(f"{matched} is already registered by someone else.")
            return ConversationHandler.END

    # Register or update
    existing = None
    for u in all_users:
        if u["name"] == matched:
            existing = u
            break

    if existing:
        # User was pre-loaded but not DM-registered — update telegram_id
        if existing["telegram_id"] != update.effective_user.id:
            # Need to update the telegram_id mapping
            await db.db.execute(
                "UPDATE users SET telegram_id = ?, dm_registered = 1 WHERE name = ?",
                (update.effective_user.id, matched),
            )
            await db.db.commit()
        else:
            await db.register_dm(update.effective_user.id)
    else:
        await db.add_user(update.effective_user.id, matched)
        await db.register_dm(update.effective_user.id)

    await update.message.reply_text(
        DM_REGISTRATION_SUCCESS.format(name=matched)
    )

    # Update welcome message in group if we have the chat ID
    await _update_welcome_message(context)

    return ConversationHandler.END


async def _update_welcome_message(context: ContextTypes.DEFAULT_TYPE):
    """Update the group welcome message with registration progress."""
    db = context.bot_data["db"]
    welcome_msg_id = context.bot_data.get("welcome_message_id")
    chat_id = context.bot_data.get("group_chat_id")

    if not welcome_msg_id or not chat_id:
        return

    unregistered = await db.get_unregistered_names()
    all_users = await db.get_all_users()
    total = len(all_users)
    registered = total - len(unregistered)

    if len(unregistered) == 0:
        # Everyone registered!
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=welcome_msg_id,
                text=WELCOME_ALL_REGISTERED,
            )
        except Exception:
            pass
    else:
        bot_info = await context.bot.get_me()
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=welcome_msg_id,
                text=WELCOME_GROUP.format(
                    bot_username=bot_info.username,
                    waiting_names=", ".join(unregistered),
                    registered=registered,
                    total=total,
                ),
            )
        except Exception:
            pass


def get_onboarding_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            AWAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
        },
        fallbacks=[CommandHandler("start", start_command)],
        per_chat=True,
        per_user=True,
    )
```

- [ ] **Step 2: Commit**

```bash
git add 75-hard-bot/bot/handlers/onboarding.py
git commit -m "feat: add onboarding handler with fuzzy name matching"
```

---

### Task 8: Daily Card, Water & Diet Handlers

**Files:**
- Create: `75-hard-bot/bot/handlers/daily_card.py`
- Create: `75-hard-bot/bot/handlers/water.py`
- Create: `75-hard-bot/bot/handlers/diet.py`

- [ ] **Step 1: Implement daily_card.py — posting the card and building the keyboard**

```python
# 75-hard-bot/bot/handlers/daily_card.py
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from telegram.error import BadRequest

from bot.config import CB_WATER, CB_WORKOUT, CB_READ, CB_PHOTO, CB_DIET, CHALLENGE_START_DATE, CHALLENGE_DAYS
from bot.utils.card_renderer import render_daily_card
from bot.utils.progress import get_day_number


def build_card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💧 Water +1", callback_data=CB_WATER),
            InlineKeyboardButton("🏋️ Workout", callback_data=CB_WORKOUT),
            InlineKeyboardButton("📖 Read", callback_data=CB_READ),
        ],
        [
            InlineKeyboardButton("📸 Photo", callback_data=CB_PHOTO),
            InlineKeyboardButton("🍽️ Diet ✓", callback_data=CB_DIET),
        ],
    ])


async def post_daily_card(context: ContextTypes.DEFAULT_TYPE, chat_id: int = None):
    """Post a new daily card to the group. Called by scheduler or manually."""
    db = context.bot_data["db"]
    if not chat_id:
        chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    today = date.today()
    day_number = get_day_number(CHALLENGE_START_DATE, today)

    if day_number < 1 or day_number > CHALLENGE_DAYS:
        return  # Outside challenge window

    active_users = await db.get_active_users()
    if not active_users:
        return

    # Create checkin rows for today
    for user in active_users:
        await db.create_checkin(user["telegram_id"], day_number, today.isoformat())

    # Get all checkins for rendering
    checkins = await db.get_all_checkins_for_day(day_number)
    prize_pool = sum(u["tier"] for u in await db.get_all_users())

    card_text = render_daily_card(day_number, len(active_users), prize_pool, checkins)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=card_text,
        reply_markup=build_card_keyboard(),
    )

    # Save card reference
    await db.save_card(day_number, today.isoformat(), msg.message_id, chat_id)

    # Pin the message
    try:
        await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
    except BadRequest:
        pass  # Bot might not have pin permissions yet


async def refresh_card(context: ContextTypes.DEFAULT_TYPE, day_number: int):
    """Re-render and edit the daily card in-place."""
    db = context.bot_data["db"]
    card = await db.get_card(day_number)
    if not card:
        return

    active_users = await db.get_active_users()
    checkins = await db.get_all_checkins_for_day(day_number)
    prize_pool = sum(u["tier"] for u in await db.get_all_users())

    card_text = render_daily_card(day_number, len(active_users), prize_pool, checkins)

    try:
        await context.bot.edit_message_text(
            chat_id=card["chat_id"],
            message_id=card["message_id"],
            text=card_text,
            reply_markup=build_card_keyboard(),
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/card — link to the current pinned daily card."""
    db = context.bot_data["db"]
    today = date.today()
    day_number = get_day_number(CHALLENGE_START_DATE, today)
    card = await db.get_card(day_number)

    if card:
        await update.message.reply_text(f"Today's card is pinned above ☝️ (Day {day_number})")
    else:
        await update.message.reply_text("No card posted yet for today.")


def get_card_command_handler():
    return CommandHandler("card", card_command)
```

- [ ] **Step 2: Implement water.py**

```python
# 75-hard-bot/bot/handlers/water.py
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from bot.config import CB_WATER, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card
from bot.utils.progress import get_day_number
from bot.templates.messages import WATER_POPUP, WATER_FULL, WATER_SET


async def water_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 💧 Water +1 button tap."""
    query = update.callback_query
    db = context.bot_data["db"]
    user_id = query.from_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    checkin = await db.get_checkin(user_id, day_number)
    if not checkin:
        await query.answer("No checkin found for today.")
        return

    if checkin["water_cups"] >= 16:
        await query.answer(WATER_FULL)
        return

    new_count = await db.increment_water(user_id, day_number)
    await query.answer(WATER_POPUP.format(cups=new_count))
    await refresh_card(context, day_number)


async def water_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/water set N — manually set water cup count."""
    if not context.args or len(context.args) < 2 or context.args[0] != "set":
        await update.message.reply_text("Usage: /water set [number]")
        return

    try:
        cups = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Usage: /water set [number]")
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    cups = max(0, min(16, cups))
    await db.set_water(user_id, day_number, cups)
    await update.message.reply_text(WATER_SET.format(cups=cups))
    await refresh_card(context, day_number)


def get_water_callback_handler():
    return CallbackQueryHandler(water_callback, pattern=f"^{CB_WATER}$")

def get_water_command_handler():
    return CommandHandler("water", water_set_command)
```

- [ ] **Step 3: Implement diet.py**

```python
# 75-hard-bot/bot/handlers/diet.py
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler

from bot.config import CB_DIET, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card
from bot.utils.progress import get_day_number
from bot.templates.messages import DIET_ON, DIET_OFF


async def diet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 🍽️ Diet ✓ button tap — toggle on/off."""
    query = update.callback_query
    db = context.bot_data["db"]
    user_id = query.from_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    result = await db.toggle_diet(user_id, day_number)
    await query.answer(DIET_ON if result else DIET_OFF)
    await refresh_card(context, day_number)


def get_diet_callback_handler():
    return CallbackQueryHandler(diet_callback, pattern=f"^{CB_DIET}$")
```

- [ ] **Step 4: Commit**

```bash
git add 75-hard-bot/bot/handlers/daily_card.py 75-hard-bot/bot/handlers/water.py 75-hard-bot/bot/handlers/diet.py
git commit -m "feat: add daily card posting, water increment, and diet toggle handlers"
```

---

### Task 9: Workout Handler

**Files:**
- Create: `75-hard-bot/bot/handlers/workout.py`

- [ ] **Step 1: Implement workout.py with multi-step inline keyboard flow**

```python
# 75-hard-bot/bot/handlers/workout.py
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

from bot.config import (
    CB_WORKOUT, CB_WORKOUT_TYPE, CB_WORKOUT_LOC,
    WORKOUT_TYPES, CHALLENGE_START_DATE,
)
from bot.handlers.daily_card import refresh_card
from bot.utils.progress import get_day_number
from bot.templates.messages import (
    WORKOUT_PICK_TYPE, WORKOUT_PICK_LOCATION, WORKOUT_LOGGED,
    WORKOUT_BOTH_DONE, WORKOUT_WRONG_LOCATION, WORKOUT_ALREADY_DONE,
)

WORKOUT_EMOJIS = {
    "run": "🏃", "lift": "🏋️", "yoga": "🧘",
    "bike": "🚴", "swim": "🏊", "other": "💪",
}
LOCATION_EMOJIS = {"outdoor": "🌳", "indoor": "🏠"}


async def workout_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped [Workout] — show type picker."""
    query = update.callback_query
    db = context.bot_data["db"]
    user_id = query.from_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    checkin = await db.get_checkin(user_id, day_number)
    if not checkin:
        await query.answer("No checkin found for today.")
        return

    if checkin["workout_1_done"] and checkin["workout_2_done"]:
        await query.answer(WORKOUT_ALREADY_DONE)
        return

    # Store user_id in callback data so only they can interact
    buttons = []
    row = []
    for wtype in WORKOUT_TYPES:
        emoji = WORKOUT_EMOJIS[wtype]
        row.append(InlineKeyboardButton(
            f"{emoji} {wtype.title()}",
            callback_data=f"{CB_WORKOUT_TYPE}{user_id}_{wtype}",
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    username = query.from_user.username or query.from_user.first_name
    await query.answer()
    await query.message.reply_text(
        WORKOUT_PICK_TYPE.format(username=username),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def workout_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked workout type — show location picker."""
    query = update.callback_query
    data = query.data.replace(CB_WORKOUT_TYPE, "")
    user_id_str, wtype = data.split("_", 1)
    user_id = int(user_id_str)

    # Only the user who started the flow can interact
    if query.from_user.id != user_id:
        await query.answer("This isn't your workout flow!")
        return

    context.user_data["pending_workout_type"] = wtype
    emoji = WORKOUT_EMOJIS.get(wtype, "💪")
    username = query.from_user.username or query.from_user.first_name

    buttons = [[
        InlineKeyboardButton("🌳 Outdoor", callback_data=f"{CB_WORKOUT_LOC}{user_id}_outdoor"),
        InlineKeyboardButton("🏠 Indoor", callback_data=f"{CB_WORKOUT_LOC}{user_id}_indoor"),
    ]]

    await query.edit_message_text(
        WORKOUT_PICK_LOCATION.format(username=username, emoji=emoji, wtype=wtype.title()),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def workout_location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked location — log the workout."""
    query = update.callback_query
    data = query.data.replace(CB_WORKOUT_LOC, "")
    user_id_str, location = data.split("_", 1)
    user_id = int(user_id_str)

    if query.from_user.id != user_id:
        await query.answer("This isn't your workout flow!")
        return

    db = context.bot_data["db"]
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())
    wtype = context.user_data.get("pending_workout_type", "other")

    # Validate indoor/outdoor requirement
    checkin = await db.get_checkin(user_id, day_number)
    if checkin and checkin["workout_1_done"] and not checkin["workout_2_done"]:
        first_loc = checkin["workout_1_location"]
        if first_loc == location:
            needed = "outdoor" if location == "indoor" else "indoor"
            await query.edit_message_text(
                WORKOUT_WRONG_LOCATION.format(other_loc=first_loc, needed_loc=needed)
            )
            return

    num = await db.log_workout(user_id, day_number, wtype, location)
    emoji = WORKOUT_EMOJIS.get(wtype, "💪")
    name = query.from_user.first_name

    if num == 2:
        text = WORKOUT_BOTH_DONE.format(
            emoji=emoji, name=name,
            location=location.title(), wtype=wtype.title(),
        )
    else:
        text = WORKOUT_LOGGED.format(
            emoji=emoji, name=name,
            location=location.title(), wtype=wtype.title(),
            num=num,
        )

    await query.edit_message_text(text)
    await refresh_card(context, day_number)


async def workout_undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/workout_undo — clear the last logged workout for today."""
    db = context.bot_data["db"]
    user_id = update.effective_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    result = await db.undo_last_workout(user_id, day_number)
    if result:
        await update.message.reply_text("Last workout cleared. Tap 🏋️ Workout on the card to re-log.")
        await refresh_card(context, day_number)
    else:
        await update.message.reply_text("No workouts to undo today.")


def get_workout_handlers():
    return [
        CallbackQueryHandler(workout_start_callback, pattern=f"^{CB_WORKOUT}$"),
        CallbackQueryHandler(workout_type_callback, pattern=f"^{CB_WORKOUT_TYPE}"),
        CallbackQueryHandler(workout_location_callback, pattern=f"^{CB_WORKOUT_LOC}"),
        CommandHandler("workout_undo", workout_undo_command),
    ]
```

- [ ] **Step 2: Commit**

```bash
git add 75-hard-bot/bot/handlers/workout.py
git commit -m "feat: add workout handler with type/location flow and indoor/outdoor validation"
```

---

### Task 10: Reading & Photo Handlers

**Files:**
- Create: `75-hard-bot/bot/handlers/reading.py`
- Create: `75-hard-bot/bot/handlers/photo.py`

- [ ] **Step 1: Implement reading.py with ConversationHandler**

```python
# 75-hard-bot/bot/handlers/reading.py
from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters,
)

from bot.config import CB_READ, CB_READ_SAME, CB_READ_NEW, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card
from bot.utils.progress import get_day_number
from bot.templates.messages import (
    READ_CHECK_DM, READ_SAME_BOOK, READ_ASK_BOOK,
    READ_ASK_TAKEAWAY, READ_ALREADY_DONE, READ_CARD, READ_CARD_NEW_BOOK,
    PHOTO_NOT_REGISTERED,
)

BOOK_TITLE, TAKEAWAY = range(2)


async def read_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped [Read] on the daily card."""
    query = update.callback_query
    db = context.bot_data["db"]
    user_id = query.from_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    user = await db.get_user(user_id)
    if not user or not user["dm_registered"]:
        bot_info = await context.bot.get_me()
        await query.answer(PHOTO_NOT_REGISTERED.format(bot_username=bot_info.username))
        return

    checkin = await db.get_checkin(user_id, day_number)
    if checkin and checkin["reading_done"]:
        await query.answer(READ_ALREADY_DONE)
        return

    await query.answer(READ_CHECK_DM)

    # Store context for the DM conversation
    context.user_data["reading_day"] = day_number
    context.user_data["reading_new_book"] = False

    if user["current_book"]:
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, same book", callback_data=CB_READ_SAME),
            InlineKeyboardButton("Started a new book", callback_data=CB_READ_NEW),
        ]])
        await context.bot.send_message(
            chat_id=user_id,
            text=READ_SAME_BOOK.format(book=user["current_book"]),
            reply_markup=buttons,
        )
    else:
        await context.bot.send_message(chat_id=user_id, text=READ_ASK_BOOK)
        # We need to return a conversation state, but this is a callback not a ConversationHandler entry
        context.user_data["awaiting_book_title"] = True


async def read_same_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed same book — ask for takeaway."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(READ_ASK_TAKEAWAY)
    context.user_data["awaiting_takeaway"] = True


async def read_new_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User is reading a new book — ask for title."""
    query = update.callback_query
    await query.answer()
    context.user_data["reading_new_book"] = True
    await query.edit_message_text(READ_ASK_BOOK)
    context.user_data["awaiting_book_title"] = True


async def handle_dm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages in DM for reading flow."""
    if update.effective_chat.type != "private":
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id

    if context.user_data.get("awaiting_book_title"):
        context.user_data["awaiting_book_title"] = False
        context.user_data["new_book_title"] = update.message.text.strip()
        await update.message.reply_text(READ_ASK_TAKEAWAY)
        context.user_data["awaiting_takeaway"] = True
        return

    if context.user_data.get("awaiting_takeaway"):
        context.user_data["awaiting_takeaway"] = False
        takeaway = update.message.text.strip()
        day_number = context.user_data.get("reading_day", get_day_number(CHALLENGE_START_DATE, date.today()))
        user = await db.get_user(user_id)
        is_new_book = context.user_data.get("reading_new_book", False)
        new_title = context.user_data.get("new_book_title")
        old_book = user["current_book"] if user else None

        if is_new_book and new_title:
            # Finish old book, start new one
            if old_book:
                await db.finish_book(user_id, day_number)
            await db.set_current_book(user_id, new_title, day_number)
            book_title = new_title
        else:
            book_title = user["current_book"] if user else "Unknown"

        # Log the reading
        await db.log_reading(user_id, day_number, book_title, takeaway)
        await update.message.reply_text(f"Reading logged! ✅")

        # Refresh card
        await refresh_card(context, day_number)

        # Post reading card to group
        chat_id = context.bot_data.get("group_chat_id")
        if chat_id:
            name = user["name"] if user else "Someone"
            if is_new_book and old_book:
                # Calculate days with old book
                card_text = READ_CARD_NEW_BOOK.format(
                    name=name, old_book=old_book, old_days="?",
                    new_book=book_title, takeaway=takeaway,
                )
            else:
                card_text = READ_CARD.format(
                    name=name, book=book_title,
                    days_with_book="?", takeaway=takeaway,
                )
            # Simplify: just use the takeaway as both quote and take
            simple_card = f'📖 {name} is reading "{book_title}"\n\n"{takeaway}"'
            await context.bot.send_message(chat_id=chat_id, text=simple_card)

        # Clear user data
        for key in ["reading_day", "reading_new_book", "new_book_title"]:
            context.user_data.pop(key, None)
        return


async def reread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reread — update today's reading entry."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /reread in DMs with me.")
        return

    day_number = get_day_number(CHALLENGE_START_DATE, date.today())
    context.user_data["reading_day"] = day_number
    context.user_data["reading_new_book"] = False
    context.user_data["awaiting_takeaway"] = True
    await update.message.reply_text(READ_ASK_TAKEAWAY)


def get_reading_handlers():
    return [
        CallbackQueryHandler(read_start_callback, pattern=f"^{CB_READ}$"),
        CallbackQueryHandler(read_same_callback, pattern=f"^{CB_READ_SAME}$"),
        CallbackQueryHandler(read_new_callback, pattern=f"^{CB_READ_NEW}$"),
        CommandHandler("reread", reread_command),
    ]
```

- [ ] **Step 2: Implement photo.py**

```python
# 75-hard-bot/bot/handlers/photo.py
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from bot.config import CB_PHOTO, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card
from bot.utils.progress import get_day_number
from bot.templates.messages import (
    PHOTO_CHECK_DM, PHOTO_ASK, PHOTO_SAVED, PHOTO_UPDATED,
    PHOTO_NOT_REGISTERED, PHOTO_GROUP_NOTIFY, PHOTO_NEED_PHOTO,
)


async def photo_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped [Photo] on the daily card."""
    query = update.callback_query
    db = context.bot_data["db"]
    user_id = query.from_user.id

    user = await db.get_user(user_id)
    if not user or not user["dm_registered"]:
        bot_info = await context.bot.get_me()
        await query.answer(PHOTO_NOT_REGISTERED.format(bot_username=bot_info.username))
        return

    day_number = get_day_number(CHALLENGE_START_DATE, date.today())
    await query.answer(PHOTO_CHECK_DM)

    context.user_data["photo_day"] = day_number
    await context.bot.send_message(
        chat_id=user_id,
        text=PHOTO_ASK.format(day=day_number),
    )


async def handle_dm_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo sent in DMs."""
    if update.effective_chat.type != "private":
        return

    if not update.message.photo:
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    day_number = context.user_data.get(
        "photo_day",
        get_day_number(CHALLENGE_START_DATE, date.today()),
    )

    # Get highest quality photo
    file_id = update.message.photo[-1].file_id

    checkin = await db.get_checkin(user_id, day_number)
    is_update = checkin and checkin["photo_done"] if checkin else False

    await db.log_photo(user_id, day_number, file_id)

    if is_update:
        await update.message.reply_text(PHOTO_UPDATED.format(day=day_number))
    else:
        await update.message.reply_text(PHOTO_SAVED.format(day=day_number))

    # Refresh card
    await refresh_card(context, day_number)

    # Notify group
    chat_id = context.bot_data.get("group_chat_id")
    if chat_id and not is_update:
        user = await db.get_user(user_id)
        name = user["name"] if user else "Someone"
        # Count photo submissions for today
        checkins = await db.get_all_checkins_for_day(day_number)
        photo_count = sum(1 for c in checkins if c["photo_done"])
        total = len(checkins)
        await context.bot.send_message(
            chat_id=chat_id,
            text=PHOTO_GROUP_NOTIFY.format(name=name, count=photo_count, total=total),
        )

    context.user_data.pop("photo_day", None)


async def handle_dm_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject non-photo files in DMs when photo is expected."""
    if update.effective_chat.type != "private":
        return
    if context.user_data.get("photo_day"):
        await update.message.reply_text(PHOTO_NEED_PHOTO)


def get_photo_handlers():
    return [
        CallbackQueryHandler(photo_start_callback, pattern=f"^{CB_PHOTO}$"),
    ]


def get_dm_photo_handler():
    """Returns handler for photos in DMs. Register on the application level."""
    return MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_dm_photo)
```

- [ ] **Step 3: Commit**

```bash
git add 75-hard-bot/bot/handlers/reading.py 75-hard-bot/bot/handlers/photo.py
git commit -m "feat: add reading handler with book memory and photo DM handler"
```

---

### Task 11: Feedback & Admin Handlers

**Files:**
- Create: `75-hard-bot/bot/handlers/feedback.py`
- Create: `75-hard-bot/bot/handlers/admin.py`

- [ ] **Step 1: Implement feedback.py**

```python
# 75-hard-bot/bot/handlers/feedback.py
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from bot.config import CHALLENGE_START_DATE
from bot.utils.progress import get_day_number
from bot.templates.messages import FEEDBACK_CONFIRM


async def _handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE, fb_type: str):
    if not context.args:
        await update.message.reply_text(f"Usage: /{fb_type} <your message>")
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    text = " ".join(context.args)
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())
    ctx = f"day {day_number}"

    await db.add_feedback(user_id, fb_type, text, ctx)
    await update.message.reply_text(FEEDBACK_CONFIRM.format(type=fb_type))


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_feedback(update, context, "feedback")

async def bug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_feedback(update, context, "bug")

async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_feedback(update, context, "suggestion")


def get_feedback_handlers():
    return [
        CommandHandler("feedback", feedback_command),
        CommandHandler("bug", bug_command),
        CommandHandler("suggest", suggest_command),
    ]
```

- [ ] **Step 2: Implement admin.py**

```python
# 75-hard-bot/bot/handlers/admin.py
from datetime import date
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, MessageHandler, filters

from bot.config import ADMIN_USER_ID, CHALLENGE_START_DATE
from bot.utils.progress import get_day_number
from bot.handlers.daily_card import post_daily_card, refresh_card
from bot.templates.messages import FAIL_CONFIRM, FAIL_DONE

AWAITING_CONFIRM = 0


def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_USER_ID


async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    db = context.bot_data["db"]
    users = await db.get_all_users()
    day = get_day_number(CHALLENGE_START_DATE, date.today())

    lines = [f"📊 Admin Status — Day {day}", ""]
    for u in users:
        status = "✅ active" if u["active"] else f"❌ failed day {u['failed_day']}"
        paid = "💰" if u["paid"] else "⬜"
        dm = "📱" if u["dm_registered"] else "⬜"
        lines.append(f"{u['name']}: {status} {paid}paid {dm}dm")

    feedback = await db.get_feedback()
    lines.append(f"\n📋 Open feedback: {len(feedback)}")
    await update.message.reply_text("\n".join(lines))


async def admin_reset_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    chat_id = context.bot_data.get("group_chat_id")
    await post_daily_card(context, chat_id)
    await update.message.reply_text("Card reposted.")


async def admin_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    db = context.bot_data["db"]

    fb_type = None
    if context.args and context.args[0] in ("bugs", "suggestions", "feedback"):
        type_map = {"bugs": "bug", "suggestions": "suggestion", "feedback": "feedback"}
        fb_type = type_map[context.args[0]]

    items = await db.get_feedback(fb_type=fb_type)
    if not items:
        await update.message.reply_text("No open feedback.")
        return

    lines = ["📋 Open feedback:\n"]
    for item in items[:20]:
        name = item.get("name", "?")
        lines.append(f"#{item['id']} [{item['type']}] {name}: \"{item['text']}\" ({item['context']})")

    await update.message.reply_text("\n".join(lines))


async def admin_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /admin_resolve [id] [status]")
        return

    db = context.bot_data["db"]
    try:
        fid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return

    status = context.args[1]
    if status not in ("acknowledged", "implemented", "wontfix"):
        await update.message.reply_text("Status must be: acknowledged, implemented, or wontfix")
        return

    await db.resolve_feedback(fid, status)
    await update.message.reply_text(f"Feedback #{fid} marked as {status}.")


async def admin_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /admin_announce <message>")
        return
    chat_id = context.bot_data.get("group_chat_id")
    if chat_id:
        text = " ".join(context.args)
        await context.bot.send_message(chat_id=chat_id, text=text)


async def fail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fail — user wants to withdraw."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /fail in DMs with me.")
        return
    await update.message.reply_text(FAIL_CONFIRM)
    return AWAITING_CONFIRM


async def fail_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() != "CONFIRM":
        await update.message.reply_text("Type CONFIRM (all caps) to confirm, or anything else to cancel.")
        return ConversationHandler.END

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    day_number = get_day_number(CHALLENGE_START_DATE, date.today())

    user = await db.get_user(user_id)
    if not user or not user["active"]:
        await update.message.reply_text("You're not currently active in the challenge.")
        return ConversationHandler.END

    await db.eliminate_user(user_id, day_number)

    name = user["name"]
    days = day_number - 1  # completed days (failed on current day)
    returned = days
    remaining = user["tier"] - returned
    active_users = await db.get_active_users()
    all_users = await db.get_all_users()
    pool = sum(u["tier"] for u in all_users) - returned

    await update.message.reply_text(f"Confirmed. You completed {days} days. Respect. 💪")

    chat_id = context.bot_data.get("group_chat_id")
    if chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=FAIL_DONE.format(
                name=name, days=days, returned=returned,
                remaining=remaining, pool=pool, active=len(active_users),
            ),
        )
        await refresh_card(context, day_number)

    return ConversationHandler.END


def get_admin_handlers():
    return [
        CommandHandler("admin_status", admin_status),
        CommandHandler("admin_reset_day", admin_reset_day),
        CommandHandler("admin_feedback", admin_feedback),
        CommandHandler("admin_resolve", admin_resolve),
        CommandHandler("admin_announce", admin_announce),
    ]

def get_fail_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("fail", fail_command)],
        states={
            AWAITING_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, fail_confirm)],
        },
        fallbacks=[],
        per_chat=True,
        per_user=True,
    )
```

- [ ] **Step 3: Commit**

```bash
git add 75-hard-bot/bot/handlers/feedback.py 75-hard-bot/bot/handlers/admin.py
git commit -m "feat: add feedback system and admin commands including /fail flow"
```

---

### Task 12: Scheduled Jobs

**Files:**
- Create: `75-hard-bot/bot/jobs/scheduler.py`

- [ ] **Step 1: Implement scheduler.py**

```python
# 75-hard-bot/bot/jobs/scheduler.py
from datetime import date, time, datetime
import pytz
from telegram.ext import ContextTypes

from bot.config import CHALLENGE_START_DATE, CHALLENGE_DAYS
from bot.handlers.daily_card import post_daily_card
from bot.utils.progress import get_day_number, get_missing_tasks, is_all_complete

ET = pytz.timezone("US/Eastern")


async def morning_card_job(context: ContextTypes.DEFAULT_TYPE):
    """Post the daily card at 7 AM ET."""
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    if day < 1 or day > CHALLENGE_DAYS:
        return
    await post_daily_card(context)


async def evening_scoreboard_job(context: ContextTypes.DEFAULT_TYPE):
    """Post the evening wrap-up at 10 PM ET."""
    db = context.bot_data["db"]
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    day = get_day_number(CHALLENGE_START_DATE, date.today())
    if day < 1 or day > CHALLENGE_DAYS:
        return

    checkins = await db.get_all_checkins_for_day(day)
    active_users = await db.get_active_users()
    if not checkins:
        return

    complete = [c["name"] for c in checkins if is_all_complete(c)]
    incomplete = [(c["name"], get_missing_tasks(c)) for c in checkins if not is_all_complete(c)]

    lines = [f"📊 DAY {day} WRAP-UP — {len(active_users)}/{len(active_users)} STILL STANDING", ""]

    if complete:
        lines.append(f"All tasks complete: {', '.join(complete)} ⭐")
    if incomplete:
        almost_parts = [f"{name} (missing: {', '.join(missing)})" for name, missing in incomplete]
        lines.append(f"Almost there: {', '.join(almost_parts)}")

    # Reading section
    reads = [(c["name"], c["book_title"]) for c in checkins if c["reading_done"] and c["book_title"]]
    if reads:
        lines.append("")
        lines.append("📖 Today's reads:")
        for name, book in reads:
            lines.append(f"  {name} — \"{book}\"")

    all_users = await db.get_all_users()
    pool = sum(u["tier"] for u in all_users)
    remaining = CHALLENGE_DAYS - day
    lines.append(f"\n💰 ${pool} · Day {day} in the books. {remaining} to go.")

    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))


async def nudge_job(context: ContextTypes.DEFAULT_TYPE):
    """DM users with incomplete tasks at 11 PM ET."""
    db = context.bot_data["db"]
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    if day < 1 or day > CHALLENGE_DAYS:
        return

    checkins = await db.get_all_checkins_for_day(day)
    for c in checkins:
        if is_all_complete(c):
            continue

        missing = get_missing_tasks(c)
        if not missing:
            continue

        user = await db.get_user(c["telegram_id"])
        if not user or not user["dm_registered"]:
            continue

        missing_list = "\n".join(f"  ⬜ {m}" for m in missing)
        text = f"Hey {user['name']} — you have unchecked tasks for today:\n\n{missing_list}\n\nIf you've done them, log them now.\nYou can also backfill until noon tomorrow."

        try:
            await context.bot.send_message(chat_id=c["telegram_id"], text=text)
        except Exception:
            pass  # User may have blocked the bot


async def noon_cutoff_job(context: ContextTypes.DEFAULT_TYPE):
    """Lock the previous day's checkins at noon."""
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    yesterday = day - 1
    if yesterday < 1:
        return

    db = context.bot_data["db"]
    checkins = await db.get_all_checkins_for_day(yesterday)

    for c in checkins:
        if not is_all_complete(c):
            missing = get_missing_tasks(c)
            # Flag for Bryan — send admin notification
            chat_id = context.bot_data.get("group_chat_id")
            if chat_id:
                user = await db.get_user(c["telegram_id"])
                name = user["name"] if user else "?"
                # Don't auto-eliminate, just flag
                try:
                    from bot.config import ADMIN_USER_ID
                    await context.bot.send_message(
                        chat_id=ADMIN_USER_ID,
                        text=f"⚠️ {name} has incomplete tasks for Day {yesterday}: {', '.join(missing)}. Use /admin_eliminate if needed.",
                    )
                except Exception:
                    pass


def schedule_jobs(job_queue):
    """Register all daily scheduled jobs."""
    # 7:00 AM ET
    job_queue.run_daily(
        morning_card_job,
        time=time(7, 0, tzinfo=ET),
        name="morning_card",
    )
    # 10:00 PM ET
    job_queue.run_daily(
        evening_scoreboard_job,
        time=time(22, 0, tzinfo=ET),
        name="evening_scoreboard",
    )
    # 11:00 PM ET
    job_queue.run_daily(
        nudge_job,
        time=time(23, 0, tzinfo=ET),
        name="nudge",
    )
    # 12:00 PM ET (noon — lock previous day)
    job_queue.run_daily(
        noon_cutoff_job,
        time=time(12, 0, tzinfo=ET),
        name="noon_cutoff",
    )
```

- [ ] **Step 2: Add pytz to requirements.txt**

Append to `75-hard-bot/requirements.txt`:
```
pytz>=2024.1
```

- [ ] **Step 3: Commit**

```bash
git add 75-hard-bot/bot/jobs/scheduler.py 75-hard-bot/requirements.txt
git commit -m "feat: add scheduled jobs for morning card, scoreboard, nudge, and noon cutoff"
```

---

### Task 13: Main Entry Point

**Files:**
- Create: `75-hard-bot/bot/main.py`

- [ ] **Step 1: Implement main.py — wire everything together**

```python
# 75-hard-bot/bot/main.py
import asyncio
import logging
import os
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import BOT_TOKEN, DATABASE_PATH, GROUP_CHAT_ID, PARTICIPANTS
from bot.database import Database
from bot.handlers.onboarding import get_onboarding_handler
from bot.handlers.daily_card import get_card_command_handler
from bot.handlers.water import get_water_callback_handler, get_water_command_handler
from bot.handlers.diet import get_diet_callback_handler
from bot.handlers.workout import get_workout_handlers
from bot.handlers.reading import get_reading_handlers, handle_dm_text
from bot.handlers.photo import get_photo_handlers, get_dm_photo_handler
from bot.handlers.feedback import get_feedback_handlers
from bot.handlers.admin import get_admin_handlers, get_fail_handler
from bot.jobs.scheduler import schedule_jobs
from bot.templates.messages import PINNED_FAQ

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application):
    """Initialize database and bot data after the bot starts."""
    # Ensure data directory exists
    db_path = DATABASE_PATH
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    db = Database(db_path)
    await db.init()
    application.bot_data["db"] = db
    application.bot_data["group_chat_id"] = GROUP_CHAT_ID if GROUP_CHAT_ID else None

    # Pre-populate participants if not already in DB
    for name in PARTICIPANTS:
        existing = None
        all_users = await db.get_all_users()
        for u in all_users:
            if u["name"] == name:
                existing = u
                break
        if not existing:
            # Use a placeholder telegram_id — will be updated during registration
            import random
            placeholder_id = random.randint(900000000, 999999999)
            await db.add_user(placeholder_id, name)

    logger.info("Database initialized. %d users loaded.", len(await db.get_all_users()))

    # Schedule jobs
    schedule_jobs(application.job_queue)
    logger.info("Scheduled jobs registered.")


async def post_shutdown(application: Application):
    """Clean up database connection on shutdown."""
    db = application.bot_data.get("db")
    if db:
        await db.close()


async def handle_new_group(update: Update, context):
    """Detect when bot is added to a group and store chat_id."""
    if update.my_chat_member:
        new_status = update.my_chat_member.new_chat_member.status
        if new_status in ("member", "administrator"):
            chat_id = update.my_chat_member.chat.id
            context.bot_data["group_chat_id"] = chat_id
            logger.info("Bot added to group: %s (chat_id: %d)", update.my_chat_member.chat.title, chat_id)

            # Post welcome message
            from bot.templates.messages import WELCOME_GROUP
            db = context.bot_data["db"]
            unregistered = await db.get_unregistered_names()
            all_users = await db.get_all_users()
            bot_info = await context.bot.get_me()

            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=WELCOME_GROUP.format(
                    bot_username=bot_info.username,
                    waiting_names=", ".join(unregistered),
                    registered=len(all_users) - len(unregistered),
                    total=len(all_users),
                ),
            )
            context.bot_data["welcome_message_id"] = msg.message_id

            # Post and pin the FAQ
            faq_msg = await context.bot.send_message(chat_id=chat_id, text=PINNED_FAQ)
            try:
                await context.bot.pin_chat_message(chat_id=chat_id, message_id=faq_msg.message_id, disable_notification=True)
            except Exception:
                pass


def main():
    """Build and run the bot."""
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Onboarding (ConversationHandler for DM registration)
    app.add_handler(get_onboarding_handler())

    # Fail handler (ConversationHandler for /fail confirmation)
    app.add_handler(get_fail_handler())

    # Daily card command
    app.add_handler(get_card_command_handler())

    # Callback handlers for daily card buttons
    app.add_handler(get_water_callback_handler())
    app.add_handler(get_diet_callback_handler())
    for handler in get_workout_handlers():
        app.add_handler(handler)
    for handler in get_reading_handlers():
        app.add_handler(handler)
    for handler in get_photo_handlers():
        app.add_handler(handler)

    # Water correction command
    app.add_handler(get_water_command_handler())

    # Feedback commands
    for handler in get_feedback_handlers():
        app.add_handler(handler)

    # Admin commands
    for handler in get_admin_handlers():
        app.add_handler(handler)

    # DM handlers (must be after conversation handlers)
    app.add_handler(get_dm_photo_handler())
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_dm_text,
    ))

    # Group join detection
    from telegram.ext import ChatMemberHandler
    app.add_handler(ChatMemberHandler(handle_new_group, ChatMemberHandler.MY_CHAT_MEMBER))

    logger.info("Starting 75 Hard bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the bot starts locally (dry run)**

Create a `.env` file with a test token, then:

```bash
cd 75-hard-bot && python -m bot.main
```

Expected: Bot starts, logs "Starting 75 Hard bot...", polls for updates. Ctrl+C to stop.

**Note:** You need a real bot token from BotFather for this to work. See Task 14 for BotFather setup.

- [ ] **Step 3: Commit**

```bash
git add 75-hard-bot/bot/main.py
git commit -m "feat: add main entry point wiring all handlers and scheduled jobs"
```

---

### Task 14: BotFather Setup & Local Testing

**Files:** None (Telegram setup + manual testing)

- [ ] **Step 1: Create the bot via BotFather**

1. Open Telegram, message `@BotFather`
2. Send `/newbot`
3. Name: `75 Hard Tracker` (or similar)
4. Username: `seventyfive_hard_bot` (must be unique, end with `bot`)
5. Copy the bot token

- [ ] **Step 2: Create .env file**

```bash
cd 75-hard-bot
cp .env.example .env
# Edit .env and paste the bot token
# Set ADMIN_USER_ID to Bryan's Telegram user ID (get from @userinfobot)
```

- [ ] **Step 3: Run the bot locally**

```bash
cd 75-hard-bot && python -m bot.main
```

- [ ] **Step 4: Test onboarding**

1. DM the bot `/start`
2. Type your name (e.g., "Bryan")
3. Verify registration success message

- [ ] **Step 5: Create a test group and add the bot**

1. Create a Telegram group
2. Add the bot
3. Promote bot to admin with "Pin messages" permission
4. Verify welcome message appears
5. Note the `group_chat_id` from the bot logs
6. Update `.env` with `GROUP_CHAT_ID`

- [ ] **Step 6: Test daily card manually**

Send `/admin_reset_day` in the group to trigger a card post. Test:
- Tap 💧 Water +1 — verify card updates
- Tap 🏋️ Workout — verify type → location → confirmation flow
- Tap 🍽️ Diet ✓ — verify toggle on/off
- Tap 📖 Read — verify DM conversation + reading card in group
- Tap 📸 Photo — verify DM prompt + photo submission

- [ ] **Step 7: Commit .env.example update if needed**

```bash
git add -A 75-hard-bot/
git commit -m "chore: finalize bot setup and verify all interactions work"
```

---

### Task 15: Deploy to Fly.io

**Files:**
- Modify: `75-hard-bot/fly.toml` (if needed)

- [ ] **Step 1: Install flyctl if not present**

```bash
brew install flyctl
```

- [ ] **Step 2: Login and launch**

```bash
cd 75-hard-bot
flyctl auth login
flyctl launch --no-deploy
```

Select region `ewr` (Newark).

- [ ] **Step 3: Create persistent volume**

```bash
flyctl volumes create data --size 1 --region ewr
```

- [ ] **Step 4: Set secrets**

```bash
flyctl secrets set TELEGRAM_BOT_TOKEN=<token>
flyctl secrets set ADMIN_USER_ID=<bryan_telegram_id>
flyctl secrets set GROUP_CHAT_ID=<group_chat_id>
flyctl secrets set CHALLENGE_START_DATE=2026-04-15
```

- [ ] **Step 5: Deploy**

```bash
flyctl deploy
```

- [ ] **Step 6: Verify the bot is running**

```bash
flyctl logs
```

Check that scheduled jobs are registered. Test the bot in Telegram — it should respond to commands.

- [ ] **Step 7: Final commit**

```bash
git add 75-hard-bot/
git commit -m "feat: deploy 75 Hard bot to Fly.io"
```

---

## Post-Deployment Checklist

- [ ] All 5 participants DM the bot and register
- [ ] Welcome message shows 5/5 registered
- [ ] FAQ is pinned in the group
- [ ] Daily card posts at 7 AM ET on April 15
- [ ] All 5 interactions work (water, workout, reading, photo, diet)
- [ ] Evening scoreboard posts at 10 PM ET
- [ ] 11 PM nudge DMs work
- [ ] `/feedback`, `/bug`, `/suggest` commands work
- [ ] Admin commands work for Bryan
