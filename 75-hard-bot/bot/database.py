"""Async SQLite database layer for the 75 Hard Telegram bot."""

from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone


class Database:
    """Async wrapper around SQLite for all 75 Hard bot persistence."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    async def init(self) -> None:
        """Open the database and create tables."""
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                tier INTEGER DEFAULT 75,
                paid INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                failed_day INTEGER,
                redeemed INTEGER DEFAULT 0,
                redemption_fee INTEGER DEFAULT 0,
                dm_registered INTEGER DEFAULT 0,
                current_book TEXT,
                diet_plan TEXT,
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
                finished_day INTEGER,
                cover_url TEXT
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

            CREATE TABLE IF NOT EXISTS diet_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                entry_text TEXT NOT NULL,
                extracted_value REAL,
                extracted_unit TEXT,
                extracted_json TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                user_name TEXT,
                event_type TEXT NOT NULL,
                event_detail TEXT,
                latency_ms INTEGER,
                error TEXT
            );
            """
        )
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        """Add columns that may not exist in older databases."""
        migrations = [
            "ALTER TABLE users ADD COLUMN diet_plan TEXT",
            "ALTER TABLE users ADD COLUMN redeemed INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN redemption_fee INTEGER DEFAULT 0",
            "ALTER TABLE books ADD COLUMN cover_url TEXT",
            "CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)",
        ]
        for sql in migrations:
            try:
                await self._conn.execute(sql)
                await self._conn.commit()
            except Exception:
                pass

    async def get_setting(self, key: str) -> str | None:
        async with self._conn.execute(
            "SELECT value FROM bot_settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str):
        await self._conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._conn.commit()

    # ── users ──────────────────────────────────────────────────────────

    async def add_user(
        self, telegram_id: int, name: str, *, tier: int = 75
    ) -> None:
        """Insert or update a participant."""
        await self._conn.execute(
            """
            INSERT INTO users (telegram_id, name, tier)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name, tier=excluded.tier
            """,
            (telegram_id, name, tier),
        )
        await self._conn.commit()

    async def get_user(self, telegram_id: int) -> aiosqlite.Row | None:
        async with self._conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            return await cur.fetchone()

    async def get_active_users(self) -> list[aiosqlite.Row]:
        async with self._conn.execute(
            "SELECT * FROM users WHERE active = 1"
        ) as cur:
            return await cur.fetchall()

    async def get_all_users(self) -> list[aiosqlite.Row]:
        """Return all users regardless of status."""
        async with self._conn.execute("SELECT * FROM users") as cur:
            return await cur.fetchall()

    async def get_user_by_name(self, name: str) -> aiosqlite.Row | None:
        """Look up a user by their display name."""
        async with self._conn.execute(
            "SELECT * FROM users WHERE name = ?", (name,)
        ) as cur:
            return await cur.fetchone()

    async def update_telegram_id(self, name: str, telegram_id: int) -> None:
        """Re-map a participant name to a new telegram_id and mark DM-registered.

        Also migrates any existing checkin rows from the old placeholder ID.
        """
        # Get the old ID before updating
        user = await self.get_user_by_name(name)
        old_id = user["telegram_id"] if user else None

        await self._conn.execute(
            "UPDATE users SET telegram_id = ?, dm_registered = 1 WHERE name = ?",
            (telegram_id, name),
        )

        # Migrate checkin rows from old placeholder ID to real ID
        if old_id and old_id != telegram_id:
            await self._conn.execute(
                "UPDATE daily_checkins SET telegram_id = ? WHERE telegram_id = ?",
                (telegram_id, old_id),
            )
            await self._conn.execute(
                "UPDATE books SET telegram_id = ? WHERE telegram_id = ?",
                (telegram_id, old_id),
            )

        await self._conn.commit()

    async def register_dm(self, telegram_id: int) -> None:
        """Mark that this user has started a DM with the bot."""
        await self._conn.execute(
            "UPDATE users SET dm_registered = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await self._conn.commit()

    async def eliminate_user(self, telegram_id: int, *, failed_day: int) -> None:
        """Knock a user out of the challenge."""
        await self._conn.execute(
            "UPDATE users SET active = 0, failed_day = ? WHERE telegram_id = ?",
            (failed_day, telegram_id),
        )
        await self._conn.commit()

    async def redeem_user(self, telegram_id: int, fee: int) -> None:
        """Reactivate a failed user with a redemption fee."""
        await self._conn.execute(
            "UPDATE users SET active = 1, failed_day = NULL, redeemed = 1, redemption_fee = ? WHERE telegram_id = ?",
            (fee, telegram_id),
        )
        await self._conn.commit()

    async def get_unregistered_names(self) -> list[str]:
        """Return names of active users who haven't DM-registered."""
        async with self._conn.execute(
            "SELECT name FROM users WHERE active = 1 AND dm_registered = 0 ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()
        return [r["name"] for r in rows]

    # ── daily check-ins ────────────────────────────────────────────────

    async def create_checkin(
        self, telegram_id: int, day_number: int, date: str
    ) -> aiosqlite.Row:
        """Create a new check-in row (or return existing)."""
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO daily_checkins (telegram_id, day_number, date)
            VALUES (?, ?, ?)
            """,
            (telegram_id, day_number, date),
        )
        await self._conn.commit()
        return await self.get_checkin(telegram_id, day_number)

    async def get_checkin(
        self, telegram_id: int, day_number: int
    ) -> aiosqlite.Row | None:
        async with self._conn.execute(
            "SELECT * FROM daily_checkins WHERE telegram_id = ? AND day_number = ?",
            (telegram_id, day_number),
        ) as cur:
            return await cur.fetchone()

    async def get_all_checkins_for_day(self, day_number: int) -> list[aiosqlite.Row]:
        """Return check-ins for a given day, joined with user name, active users only."""
        async with self._conn.execute(
            """
            SELECT dc.*, u.name
            FROM daily_checkins dc
            JOIN users u ON dc.telegram_id = u.telegram_id
            WHERE dc.day_number = ? AND u.active = 1
            """,
            (day_number,),
        ) as cur:
            return await cur.fetchall()

    # ── water ──────────────────────────────────────────────────────────

    async def increment_water(self, telegram_id: int, day_number: int) -> tuple[int, bool]:
        """Add one cup, capped at 16. Returns (new_count, just_completed)."""
        await self._conn.execute(
            """
            UPDATE daily_checkins
            SET water_cups = MIN(water_cups + 1, 16)
            WHERE telegram_id = ? AND day_number = ?
            """,
            (telegram_id, day_number),
        )
        await self._conn.commit()
        checkin = await self.get_checkin(telegram_id, day_number)
        new_count = checkin["water_cups"]
        just_completed = await self._check_completion(telegram_id, day_number)
        return new_count, just_completed

    async def set_water(self, telegram_id: int, day_number: int, cups: int) -> bool:
        """Directly set water count (for corrections). Returns True if just completed."""
        await self._conn.execute(
            "UPDATE daily_checkins SET water_cups = ? WHERE telegram_id = ? AND day_number = ?",
            (cups, telegram_id, day_number),
        )
        await self._conn.commit()
        return await self._check_completion(telegram_id, day_number)

    # ── diet ───────────────────────────────────────────────────────────

    async def toggle_diet(self, telegram_id: int, day_number: int) -> tuple[bool, bool]:
        """Flip diet_done between 0 and 1. Returns (now_on, just_completed)."""
        checkin = await self.get_checkin(telegram_id, day_number)
        new_val = 0 if checkin["diet_done"] else 1
        await self._conn.execute(
            "UPDATE daily_checkins SET diet_done = ? WHERE telegram_id = ? AND day_number = ?",
            (new_val, telegram_id, day_number),
        )
        await self._conn.commit()
        just_completed = await self._check_completion(telegram_id, day_number)
        return bool(new_val), just_completed

    # ── workouts ───────────────────────────────────────────────────────

    async def log_workout(
        self,
        telegram_id: int,
        day_number: int,
        workout_type: str,
        location: str,
    ) -> tuple[int, bool]:
        """Log a workout. Fills slot 1 first, then slot 2. Returns (slot, just_completed)."""
        checkin = await self.get_checkin(telegram_id, day_number)
        if not checkin["workout_1_done"]:
            await self._conn.execute(
                """
                UPDATE daily_checkins
                SET workout_1_type = ?, workout_1_location = ?, workout_1_done = 1
                WHERE telegram_id = ? AND day_number = ?
                """,
                (workout_type, location, telegram_id, day_number),
            )
            slot = 1
        else:
            await self._conn.execute(
                """
                UPDATE daily_checkins
                SET workout_2_type = ?, workout_2_location = ?, workout_2_done = 1
                WHERE telegram_id = ? AND day_number = ?
                """,
                (workout_type, location, telegram_id, day_number),
            )
            slot = 2
        await self._conn.commit()
        just_completed = await self._check_completion(telegram_id, day_number)
        return slot, just_completed

    async def undo_last_workout(self, telegram_id: int, day_number: int) -> int:
        """Clear the most recent workout. Returns slot cleared (0 if nothing)."""
        checkin = await self.get_checkin(telegram_id, day_number)
        if checkin["workout_2_done"]:
            await self._conn.execute(
                """
                UPDATE daily_checkins
                SET workout_2_type = NULL, workout_2_location = NULL, workout_2_done = 0
                WHERE telegram_id = ? AND day_number = ?
                """,
                (telegram_id, day_number),
            )
            await self._conn.commit()
            return 2
        elif checkin["workout_1_done"]:
            await self._conn.execute(
                """
                UPDATE daily_checkins
                SET workout_1_type = NULL, workout_1_location = NULL, workout_1_done = 0
                WHERE telegram_id = ? AND day_number = ?
                """,
                (telegram_id, day_number),
            )
            await self._conn.commit()
            return 1
        return 0

    # ── reading ────────────────────────────────────────────────────────

    async def log_reading(
        self,
        telegram_id: int,
        day_number: int,
        book_title: str,
        takeaway: str,
    ) -> bool:
        """Log reading. Returns True if this completed all tasks for the day."""
        await self._conn.execute(
            """
            UPDATE daily_checkins
            SET reading_done = 1, book_title = ?, reading_takeaway = ?
            WHERE telegram_id = ? AND day_number = ?
            """,
            (book_title, takeaway, telegram_id, day_number),
        )
        await self._conn.commit()
        return await self._check_completion(telegram_id, day_number)

    # ── photo ──────────────────────────────────────────────────────────

    async def get_photo_file_ids(self, telegram_id: int) -> list[dict]:
        """Get all photo file_ids for a user, ordered by day."""
        async with self._conn.execute(
            "SELECT day_number, photo_file_id FROM daily_checkins "
            "WHERE telegram_id = ? AND photo_done = 1 AND photo_file_id IS NOT NULL "
            "ORDER BY day_number",
            (telegram_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def log_photo(self, telegram_id: int, day_number: int, file_id: str) -> bool:
        """Log photo. Returns True if this completed all tasks for the day."""
        await self._conn.execute(
            """
            UPDATE daily_checkins
            SET photo_done = 1, photo_file_id = ?
            WHERE telegram_id = ? AND day_number = ?
            """,
            (file_id, telegram_id, day_number),
        )
        await self._conn.commit()
        return await self._check_completion(telegram_id, day_number)

    # ── completion check ───────────────────────────────────────────────

    async def _check_completion(self, telegram_id: int, day_number: int) -> bool:
        """Set completed_at if all 6 tasks are done for this check-in.

        Returns True if this call newly set completed_at (first completion).
        """
        checkin = await self.get_checkin(telegram_id, day_number)
        if checkin is None:
            return False
        all_done = (
            checkin["workout_1_done"]
            and checkin["workout_2_done"]
            and checkin["water_cups"] >= 16
            and checkin["diet_done"]
            and checkin["reading_done"]
            and checkin["photo_done"]
        )
        if all_done and not checkin["completed_at"]:
            now = datetime.now(timezone.utc).isoformat()
            await self._conn.execute(
                "UPDATE daily_checkins SET completed_at = ? WHERE telegram_id = ? AND day_number = ?",
                (now, telegram_id, day_number),
            )
            await self._conn.commit()
            return True
        return False

    # ── books ──────────────────────────────────────────────────────────

    async def set_diet_plan(self, telegram_id: int, diet_plan: str) -> None:
        """Set the user's diet plan."""
        await self._conn.execute(
            "UPDATE users SET diet_plan = ? WHERE telegram_id = ?",
            (diet_plan, telegram_id),
        )
        await self._conn.commit()

    # ── diet log ───────────────────────────────────────────────────────

    async def log_diet_entry(
        self,
        telegram_id: int,
        day_number: int,
        entry_text: str,
        extracted_value: float | None = None,
        extracted_unit: str | None = None,
        extracted_json: str | None = None,
    ) -> int:
        """Log a food/diet entry. Returns the entry ID."""
        cursor = await self._conn.execute(
            "INSERT INTO diet_log (telegram_id, day_number, entry_text, extracted_value, extracted_unit, extracted_json) VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, day_number, entry_text, extracted_value, extracted_unit, extracted_json),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_diet_entries(self, telegram_id: int, day_number: int) -> list[dict]:
        """Get all diet entries for a user on a given day."""
        async with self._conn.execute(
            "SELECT * FROM diet_log WHERE telegram_id = ? AND day_number = ? ORDER BY timestamp",
            (telegram_id, day_number),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def delete_last_diet_entry(self, telegram_id: int, day_number: int) -> bool:
        """Delete the most recent diet entry for today. Returns True if something was deleted."""
        async with self._conn.execute(
            "SELECT id FROM diet_log WHERE telegram_id = ? AND day_number = ? ORDER BY id DESC LIMIT 1",
            (telegram_id, day_number),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await self._conn.execute("DELETE FROM diet_log WHERE id = ?", (row["id"],))
        await self._conn.commit()
        return True

    # ── books ─────────────────────────────────────────────────────────

    async def set_current_book(
        self,
        telegram_id: int,
        title: str,
        *,
        started_day: int,
        cover_url: str | None = None,
    ) -> None:
        """Set the user's current book and create a books record."""
        await self._conn.execute(
            "UPDATE users SET current_book = ? WHERE telegram_id = ?",
            (title, telegram_id),
        )
        await self._conn.execute(
            "INSERT INTO books (telegram_id, title, started_day, cover_url) VALUES (?, ?, ?, ?)",
            (telegram_id, title, started_day, cover_url),
        )
        await self._conn.commit()

    async def get_current_book_cover(self, telegram_id: int) -> str | None:
        """Return the cover URL for the user's current book, or None."""
        user = await self.get_user(telegram_id)
        if not user or not user["current_book"]:
            return None
        async with self._conn.execute(
            """
            SELECT cover_url FROM books
            WHERE telegram_id = ? AND title = ? AND finished_day IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (telegram_id, user["current_book"]),
        ) as cur:
            row = await cur.fetchone()
        return row["cover_url"] if row else None

    async def finish_book(self, telegram_id: int, *, finished_day: int) -> None:
        """Mark the user's current book as finished."""
        user = await self.get_user(telegram_id)
        if user and user["current_book"]:
            await self._conn.execute(
                """
                UPDATE books SET finished_day = ?
                WHERE telegram_id = ? AND title = ? AND finished_day IS NULL
                """,
                (finished_day, telegram_id, user["current_book"]),
            )
            await self._conn.execute(
                "UPDATE users SET current_book = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            await self._conn.commit()

    # ── daily cards ────────────────────────────────────────────────────

    async def save_card(
        self,
        day_number: int,
        date: str,
        message_id: int,
        chat_id: int,
    ) -> None:
        """Save or update the daily scoreboard card."""
        await self._conn.execute(
            """
            INSERT INTO daily_cards (day_number, date, message_id, chat_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(day_number) DO UPDATE SET
                message_id=excluded.message_id, chat_id=excluded.chat_id
            """,
            (day_number, date, message_id, chat_id),
        )
        await self._conn.commit()

    async def get_card(self, day_number: int) -> aiosqlite.Row | None:
        async with self._conn.execute(
            "SELECT * FROM daily_cards WHERE day_number = ?", (day_number,)
        ) as cur:
            return await cur.fetchone()

    async def get_card_by_message_id(self, message_id: int) -> aiosqlite.Row | None:
        async with self._conn.execute(
            "SELECT * FROM daily_cards WHERE message_id = ?", (message_id,)
        ) as cur:
            return await cur.fetchone()

    # ── feedback ───────────────────────────────────────────────────────

    async def add_feedback(
        self,
        telegram_id: int,
        fb_type: str,
        text: str,
        context: str | None = None,
    ) -> int:
        """Save user feedback. Returns the new row id."""
        async with self._conn.execute(
            """
            INSERT INTO feedback (telegram_id, type, text, context)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, fb_type, text, context),
        ) as cur:
            fb_id = cur.lastrowid
        await self._conn.commit()
        return fb_id

    async def get_feedback(
        self,
        *,
        fb_type: str | None = None,
        status: str = "new",
    ) -> list[aiosqlite.Row]:
        """Retrieve feedback, optionally filtered by type and status."""
        query = "SELECT * FROM feedback WHERE status = ?"
        params: list = [status]
        if fb_type:
            query += " AND type = ?"
            params.append(fb_type)
        query += " ORDER BY created_at DESC"
        async with self._conn.execute(query, params) as cur:
            return await cur.fetchall()

    async def resolve_feedback(self, fb_id: int, *, status: str = "resolved") -> None:
        """Mark a feedback item with the given status."""
        await self._conn.execute(
            "UPDATE feedback SET status = ? WHERE id = ?", (status, fb_id)
        )
        await self._conn.commit()

    # ── event log ─────────────────────────────────────────────────────

    async def log_event(
        self,
        user_id: int | None,
        user_name: str | None,
        event_type: str,
        event_detail: str = None,
        latency_ms: int = None,
        error: str = None,
    ) -> None:
        """Log an event. Fire-and-forget -- never raises."""
        try:
            await self._conn.execute(
                """
                INSERT INTO event_log (user_id, user_name, event_type, event_detail, latency_ms, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, user_name, event_type, event_detail, latency_ms, error),
            )
            await self._conn.commit()
        except Exception:
            pass

    async def get_event_log_health(self) -> dict:
        """Gather health metrics from the event log for /admin_health."""
        result: dict = {}

        # First event timestamp
        async with self._conn.execute(
            "SELECT MIN(timestamp) AS first_ts FROM event_log"
        ) as cur:
            row = await cur.fetchone()
            result["first_event"] = row["first_ts"] if row else None

        # Events today
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM event_log WHERE DATE(timestamp) = DATE('now')"
        ) as cur:
            row = await cur.fetchone()
            result["events_today"] = row["cnt"] if row else 0

        # AI latency averages (today)
        ai_types = ["ai_morning", "ai_chat", "ai_recap", "ai_weekly"]
        result["ai_latency"] = {}
        for atype in ai_types:
            async with self._conn.execute(
                "SELECT AVG(latency_ms) AS avg_ms FROM event_log "
                "WHERE event_type = ? AND latency_ms IS NOT NULL AND DATE(timestamp) = DATE('now')",
                (atype,),
            ) as cur:
                row = await cur.fetchone()
                result["ai_latency"][atype] = row["avg_ms"] if row else None

        # Feature usage today
        usage_types = {
            "water_tap": "Water taps",
            "workout_log": "Workouts",
            "reading_log": "Reading",
            "photo_submit": "Photos",
            "diet_toggle": "Diet toggles",
            "ai_chat": "AI chats",
        }
        result["feature_usage"] = {}
        for etype, label in usage_types.items():
            async with self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM event_log "
                "WHERE event_type = ? AND DATE(timestamp) = DATE('now')",
                (etype,),
            ) as cur:
                row = await cur.fetchone()
                result["feature_usage"][label] = row["cnt"] if row else 0

        # Errors in last 24h
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM event_log "
            "WHERE event_type = 'error' AND timestamp > DATETIME('now', '-1 day')"
        ) as cur:
            row = await cur.fetchone()
            result["errors_24h"] = row["cnt"] if row else 0

        # Active users today (distinct user_ids with events today)
        async with self._conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM event_log "
            "WHERE user_id IS NOT NULL AND DATE(timestamp) = DATE('now')"
        ) as cur:
            row = await cur.fetchone()
            result["active_users_today"] = row["cnt"] if row else 0

        return result
