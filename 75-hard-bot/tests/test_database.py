"""Comprehensive tests for the 75 Hard bot database module."""

import pytest
import pytest_asyncio
from bot.database import Database


@pytest_asyncio.fixture
async def db():
    """Provide an in-memory database for each test."""
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


# ── User CRUD ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_user(db):
    await db.add_user(111, "Alice", tier=75)
    user = await db.get_user(111)
    assert user is not None
    assert user["name"] == "Alice"
    assert user["tier"] == 75
    assert user["active"] == 1
    assert user["dm_registered"] == 0


@pytest.mark.asyncio
async def test_add_user_duplicate_updates(db):
    await db.add_user(111, "Alice", tier=75)
    await db.add_user(111, "Alice Updated", tier=30)
    user = await db.get_user(111)
    # Should update on conflict
    assert user["name"] == "Alice Updated"
    assert user["tier"] == 30


@pytest.mark.asyncio
async def test_get_user_missing(db):
    user = await db.get_user(999)
    assert user is None


@pytest.mark.asyncio
async def test_get_active_users(db):
    await db.add_user(1, "Alice")
    await db.add_user(2, "Bob")
    await db.add_user(3, "Charlie")
    await db.eliminate_user(2, failed_day=5)
    users = await db.get_active_users()
    assert len(users) == 2
    names = {u["name"] for u in users}
    assert names == {"Alice", "Charlie"}


@pytest.mark.asyncio
async def test_register_dm(db):
    await db.add_user(111, "Alice")
    await db.register_dm(111)
    user = await db.get_user(111)
    assert user["dm_registered"] == 1


@pytest.mark.asyncio
async def test_eliminate_user(db):
    await db.add_user(111, "Alice")
    await db.eliminate_user(111, failed_day=10)
    user = await db.get_user(111)
    assert user["active"] == 0
    assert user["failed_day"] == 10


@pytest.mark.asyncio
async def test_get_unregistered_names(db):
    await db.add_user(1, "Alice")
    await db.add_user(2, "Bob")
    await db.register_dm(1)
    unregistered = await db.get_unregistered_names()
    assert unregistered == ["Bob"]


# ── Daily Check-in CRUD ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_checkin(db):
    await db.add_user(111, "Alice")
    checkin = await db.create_checkin(111, day_number=1, date="2026-04-15")
    assert checkin is not None
    assert checkin["day_number"] == 1
    assert checkin["water_cups"] == 0

    fetched = await db.get_checkin(111, day_number=1)
    assert fetched is not None
    assert fetched["telegram_id"] == 111


@pytest.mark.asyncio
async def test_create_checkin_idempotent(db):
    """Creating a checkin for the same user+day returns the existing one."""
    await db.add_user(111, "Alice")
    c1 = await db.create_checkin(111, 1, "2026-04-15")
    c2 = await db.create_checkin(111, 1, "2026-04-15")
    assert c1["id"] == c2["id"]


@pytest.mark.asyncio
async def test_get_all_checkins_for_day(db):
    await db.add_user(1, "Alice")
    await db.add_user(2, "Bob")
    await db.add_user(3, "Charlie")
    await db.eliminate_user(3, failed_day=1)  # inactive

    await db.create_checkin(1, 1, "2026-04-15")
    await db.create_checkin(2, 1, "2026-04-15")
    await db.create_checkin(3, 1, "2026-04-15")

    rows = await db.get_all_checkins_for_day(1)
    # Should only include active users
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert "Charlie" not in names


# ── Water ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_increment_water(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    count, _ = await db.increment_water(111, 1)
    assert count == 1
    count, _ = await db.increment_water(111, 1)
    assert count == 2


@pytest.mark.asyncio
async def test_increment_water_cap_at_16(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    for _ in range(20):
        count, _ = await db.increment_water(111, 1)
    assert count == 16


@pytest.mark.asyncio
async def test_set_water(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.set_water(111, 1, 10)
    checkin = await db.get_checkin(111, 1)
    assert checkin["water_cups"] == 10


# ── Diet ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_toggle_diet(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    result, _ = await db.toggle_diet(111, 1)
    assert result is True  # was 0, now 1
    checkin = await db.get_checkin(111, 1)
    assert checkin["diet_done"] == 1


@pytest.mark.asyncio
async def test_toggle_diet_off(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.toggle_diet(111, 1)   # 0 -> 1
    result, _ = await db.toggle_diet(111, 1)  # 1 -> 0
    assert result is False
    checkin = await db.get_checkin(111, 1)
    assert checkin["diet_done"] == 0


# ── Workouts ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_first_workout(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    slot, _ = await db.log_workout(111, 1, workout_type="run", location="outdoor")
    assert slot == 1
    checkin = await db.get_checkin(111, 1)
    assert checkin["workout_1_type"] == "run"
    assert checkin["workout_1_location"] == "outdoor"
    assert checkin["workout_1_done"] == 1


@pytest.mark.asyncio
async def test_log_second_workout(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.log_workout(111, 1, workout_type="run", location="outdoor")
    slot, _ = await db.log_workout(111, 1, workout_type="lift", location="indoor")
    assert slot == 2
    checkin = await db.get_checkin(111, 1)
    assert checkin["workout_2_type"] == "lift"
    assert checkin["workout_2_location"] == "indoor"
    assert checkin["workout_2_done"] == 1


@pytest.mark.asyncio
async def test_undo_last_workout_clears_workout_2_first(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.log_workout(111, 1, "run", "outdoor")
    await db.log_workout(111, 1, "lift", "indoor")

    undone = await db.undo_last_workout(111, 1)
    assert undone == 2
    checkin = await db.get_checkin(111, 1)
    assert checkin["workout_2_done"] == 0
    assert checkin["workout_2_type"] is None
    # Workout 1 should be untouched
    assert checkin["workout_1_done"] == 1


@pytest.mark.asyncio
async def test_undo_last_workout_clears_workout_1(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.log_workout(111, 1, "run", "outdoor")

    undone = await db.undo_last_workout(111, 1)
    assert undone == 1
    checkin = await db.get_checkin(111, 1)
    assert checkin["workout_1_done"] == 0
    assert checkin["workout_1_type"] is None


@pytest.mark.asyncio
async def test_undo_last_workout_nothing_to_undo(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    undone = await db.undo_last_workout(111, 1)
    assert undone == 0


# ── Reading ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_reading(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.log_reading(111, 1, book_title="Atomic Habits", takeaway="Small changes matter")
    checkin = await db.get_checkin(111, 1)
    assert checkin["reading_done"] == 1
    assert checkin["book_title"] == "Atomic Habits"
    assert checkin["reading_takeaway"] == "Small changes matter"


# ── Photo ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_photo(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.log_photo(111, 1, file_id="AgACAgIAA_photo_123")
    checkin = await db.get_checkin(111, 1)
    assert checkin["photo_done"] == 1
    assert checkin["photo_file_id"] == "AgACAgIAA_photo_123"


@pytest.mark.asyncio
async def test_get_photo_file_ids(db):
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.create_checkin(111, 7, "2026-04-21")
    await db.create_checkin(111, 14, "2026-04-28")

    await db.log_photo(111, 1, "photo_day1")
    await db.log_photo(111, 7, "photo_day7")
    await db.log_photo(111, 14, "photo_day14")

    photos = await db.get_photo_file_ids(111)
    assert len(photos) == 3
    assert photos[0]["day_number"] == 1
    assert photos[0]["photo_file_id"] == "photo_day1"
    assert photos[1]["day_number"] == 7
    assert photos[2]["day_number"] == 14
    assert photos[2]["photo_file_id"] == "photo_day14"


@pytest.mark.asyncio
async def test_get_photo_file_ids_empty(db):
    await db.add_user(111, "Alice")
    photos = await db.get_photo_file_ids(111)
    assert photos == []


@pytest.mark.asyncio
async def test_get_photo_file_ids_excludes_no_photo(db):
    """Checkins without photos should not appear."""
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.create_checkin(111, 2, "2026-04-16")
    # Only log photo for day 1
    await db.log_photo(111, 1, "photo_day1")

    photos = await db.get_photo_file_ids(111)
    assert len(photos) == 1
    assert photos[0]["day_number"] == 1


# ── Completion auto-check ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_completion(db):
    """When all 6 tasks are done, completed_at should be set."""
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")

    # Do all tasks
    await db.log_workout(111, 1, "run", "outdoor")
    await db.log_workout(111, 1, "lift", "indoor")
    # Fill water to goal
    await db.set_water(111, 1, 16)
    await db.toggle_diet(111, 1)
    await db.log_reading(111, 1, "Atomic Habits", "key takeaway")
    await db.log_photo(111, 1, "photo_123")

    checkin = await db.get_checkin(111, 1)
    assert checkin["completed_at"] is not None


@pytest.mark.asyncio
async def test_check_completion_not_complete(db):
    """If any task missing, completed_at should remain None."""
    await db.add_user(111, "Alice")
    await db.create_checkin(111, 1, "2026-04-15")
    await db.log_workout(111, 1, "run", "outdoor")
    # Missing: second workout, water, diet, reading, photo

    checkin = await db.get_checkin(111, 1)
    assert checkin["completed_at"] is None


# ── Books ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_current_book(db):
    await db.add_user(111, "Alice")
    await db.set_current_book(111, "Atomic Habits", started_day=1)
    user = await db.get_user(111)
    assert user["current_book"] == "Atomic Habits"


@pytest.mark.asyncio
async def test_finish_book(db):
    await db.add_user(111, "Alice")
    await db.set_current_book(111, "Atomic Habits", started_day=1)
    await db.finish_book(111, finished_day=10)

    user = await db.get_user(111)
    assert user["current_book"] is None

    # Book record should have finished_day set
    async with db._conn.execute(
        "SELECT * FROM books WHERE telegram_id = ? AND title = ?",
        (111, "Atomic Habits"),
    ) as cursor:
        book = await cursor.fetchone()
    assert book["finished_day"] == 10


# ── Daily Cards ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get_card(db):
    await db.save_card(day_number=1, date="2026-04-15", message_id=42, chat_id=100)
    card = await db.get_card(1)
    assert card is not None
    assert card["message_id"] == 42
    assert card["chat_id"] == 100


@pytest.mark.asyncio
async def test_get_card_missing(db):
    card = await db.get_card(99)
    assert card is None


@pytest.mark.asyncio
async def test_get_card_by_message_id(db):
    await db.save_card(1, "2026-04-15", 42, 100)
    card = await db.get_card_by_message_id(42)
    assert card is not None
    assert card["day_number"] == 1


# ── Feedback ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_and_get_feedback(db):
    fb_id = await db.add_feedback(
        telegram_id=111,
        fb_type="bug",
        text="Button doesn't work",
        context="checkin flow",
    )
    assert fb_id is not None

    items = await db.get_feedback()
    assert len(items) == 1
    assert items[0]["type"] == "bug"
    assert items[0]["status"] == "new"


@pytest.mark.asyncio
async def test_get_feedback_filter_by_type(db):
    await db.add_feedback(111, "bug", "Bug report")
    await db.add_feedback(111, "feature", "Feature request")
    bugs = await db.get_feedback(fb_type="bug")
    assert len(bugs) == 1
    assert bugs[0]["type"] == "bug"


@pytest.mark.asyncio
async def test_resolve_feedback(db):
    fb_id = await db.add_feedback(111, "bug", "Fix this")
    await db.resolve_feedback(fb_id)
    items = await db.get_feedback()  # default status='new'
    assert len(items) == 0

    resolved = await db.get_feedback(status="resolved")
    assert len(resolved) == 1


# ── Event Log ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_event_basic(db):
    await db.log_event(111, "Alice", "water_tap", "cups=5")
    async with db._conn.execute("SELECT * FROM event_log") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "water_tap"
    assert rows[0]["user_id"] == 111
    assert rows[0]["user_name"] == "Alice"
    assert rows[0]["event_detail"] == "cups=5"


@pytest.mark.asyncio
async def test_log_event_with_latency(db):
    await db.log_event(None, None, "ai_morning", "day=5", latency_ms=1200)
    async with db._conn.execute("SELECT * FROM event_log") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["latency_ms"] == 1200
    assert rows[0]["user_id"] is None


@pytest.mark.asyncio
async def test_log_event_with_error(db):
    await db.log_event(111, "Alice", "error", error="Something broke")
    async with db._conn.execute("SELECT * FROM event_log") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["error"] == "Something broke"


@pytest.mark.asyncio
async def test_log_event_never_raises(db):
    """log_event should silently fail, never crash the bot."""
    # Close the connection to force an error
    await db.close()
    # This should NOT raise
    await db.log_event(111, "Alice", "water_tap")


@pytest.mark.asyncio
async def test_get_event_log_health_empty(db):
    health = await db.get_event_log_health()
    assert health["events_today"] == 0
    assert health["errors_24h"] == 0
    assert health["active_users_today"] == 0


@pytest.mark.asyncio
async def test_get_event_log_health_with_data(db):
    await db.log_event(111, "Alice", "water_tap", "cups=5")
    await db.log_event(222, "Bob", "diet_toggle", "on=True")
    await db.log_event(111, "Alice", "ai_chat", "msg_len=20", latency_ms=800)
    await db.log_event(111, "Alice", "ai_chat", "msg_len=15", latency_ms=1200)
    await db.log_event(None, None, "error", error="test error")

    health = await db.get_event_log_health()
    assert health["events_today"] == 5
    assert health["errors_24h"] == 1
    assert health["active_users_today"] == 2
    assert health["feature_usage"]["Water taps"] == 1
    assert health["feature_usage"]["Diet toggles"] == 1
    assert health["feature_usage"]["AI chats"] == 2
    # AI chat latency average should be (800 + 1200) / 2 = 1000
    assert health["ai_latency"]["ai_chat"] == 1000.0
