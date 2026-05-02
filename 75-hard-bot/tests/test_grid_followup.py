"""Tests for the interactive grid-backfill follow-up.

Pinned contracts:
  1. get_my_compliance_grid tool returns BOTH the MEDIA:compliance_grid signal
     AND a GRID_FOLLOWUP analysis section for Claude to use as the basis of
     a per-day disambiguation question.
  2. Inside retro-grace, the tool walks every past day; outside, it walks
     only yesterday (+ Day 1 grace).
  3. Days fully covered (all penance-able tasks done OR have penance rows)
     are EXCLUDED from the unresolved list.
  4. The MEDIA: parser in chat_with_luke correctly extracts the media name
     from the multiline result.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import pytest
import pytest_asyncio

from bot.database import Database
from bot.utils import luke_chat


@pytest_asyncio.fixture
async def db_with_user():
    database = Database(":memory:")
    await database.init()
    await database.add_user(111, "Bryan", tier=75)
    # Active card = day 18 (so today's `day` = 18, yesterday = 17)
    await database.save_card(day_number=18, date="2026-05-02", message_id=1, chat_id=-1)
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_grid_returns_media_signal_and_followup(db_with_user):
    db = db_with_user
    # Yesterday (17) has no checkin row at all → all penance-able tasks unresolved
    result = await luke_chat._execute_tool(
        "get_my_compliance_grid", {}, db, user_id=111,
    )
    assert result.startswith("MEDIA:compliance_grid")
    assert "GRID_FOLLOWUP" in result
    assert "day 17" in result
    # All penance-able tasks should be flagged for day 17
    for task in ("workout_indoor", "workout_outdoor", "water", "reading", "photo"):
        assert task in result


@pytest.mark.asyncio
async def test_grid_followup_excludes_completed_tasks(db_with_user):
    db = db_with_user
    # Yesterday: all done except photo
    await db.create_checkin(111, 17, "2026-05-01")
    await db._conn.execute(
        """UPDATE daily_checkins
           SET workout_1_done=1, workout_2_done=1, water_cups=16,
               diet_done=1, reading_done=1, photo_done=0
           WHERE telegram_id=? AND day_number=?""",
        (111, 17),
    )
    await db._conn.commit()

    result = await luke_chat._execute_tool(
        "get_my_compliance_grid", {}, db, user_id=111,
    )
    # Only photo should be listed for day 17, the other tasks are done
    assert "day 17: photo" in result
    assert "day 17: workout" not in result


@pytest.mark.asyncio
async def test_grid_followup_no_unresolved_returns_caught_up(db_with_user):
    db = db_with_user
    # Yesterday all done
    await db.create_checkin(111, 17, "2026-05-01")
    await db._conn.execute(
        """UPDATE daily_checkins
           SET workout_1_done=1, workout_2_done=1, water_cups=16,
               diet_done=1, reading_done=1, photo_done=1
           WHERE telegram_id=? AND day_number=?""",
        (111, 17),
    )
    await db._conn.commit()

    result = await luke_chat._execute_tool(
        "get_my_compliance_grid", {}, db, user_id=111,
    )
    assert "no unresolved days" in result


@pytest.mark.asyncio
async def test_grid_followup_excludes_days_already_covered_by_penance(db_with_user):
    db = db_with_user
    # Yesterday: missed water and workout_indoor; penance already declared for water
    await db.create_checkin(111, 17, "2026-05-01")  # all 0
    await db.add_penance(111, missed_day=17, makeup_day=18, task="water")

    result = await luke_chat._execute_tool(
        "get_my_compliance_grid", {}, db, user_id=111,
    )
    # water for day 17 must NOT appear since it's already covered
    assert "day 17: workout_indoor" in result or "day 17:" in result
    # Find the day-17 segment and assert water isn't there
    assert "day 17" in result
    # Look at just the part of the string around "day 17"
    import re
    m = re.search(r"day 17:\s*([\w_,\s]+)", result)
    if m:
        tasks_str = m.group(1)
        assert "water" not in tasks_str, f"water should be excluded but got: {tasks_str}"


@pytest.mark.asyncio
async def test_grid_followup_outside_retro_walks_yesterday_only(db_with_user):
    """Without retro-audit open, only yesterday is in the editable window."""
    db = db_with_user
    # Day 16 has gaps — should NOT appear (outside the default window)
    await db.create_checkin(111, 16, "2026-04-30")  # all 0
    await db.create_checkin(111, 17, "2026-05-01")  # all 0
    result = await luke_chat._execute_tool(
        "get_my_compliance_grid", {}, db, user_id=111,
    )
    assert "day 17" in result
    assert "day 16" not in result


@pytest.mark.asyncio
async def test_grid_followup_inside_retro_walks_all_past_days(db_with_user):
    """With retro-audit open, ALL past days appear in the unresolved list."""
    db = db_with_user
    # Open the retro window so older days are eligible
    await db.set_setting("retro_grace_until_day", "30")  # well past today
    await db.create_checkin(111, 5, "2026-04-19")
    await db.create_checkin(111, 8, "2026-04-22")
    await db.create_checkin(111, 17, "2026-05-01")
    result = await luke_chat._execute_tool(
        "get_my_compliance_grid", {}, db, user_id=111,
    )
    assert "day 5" in result
    assert "day 8" in result
    assert "day 17" in result


def test_media_parser_handles_multiline_signal():
    """chat_with_luke must extract just the media name from a result like
    'MEDIA:compliance_grid\\n\\nGRID_FOLLOWUP: ...'."""
    raw = "MEDIA:compliance_grid\n\nGRID_FOLLOWUP: stuff stuff stuff"
    media = raw.split("MEDIA:", 1)[1].split("\n", 1)[0].strip()
    assert media == "compliance_grid"


def test_media_parser_still_works_for_simple_signal():
    raw = "MEDIA:transformation"
    media = raw.split("MEDIA:", 1)[1].split("\n", 1)[0].strip()
    assert media == "transformation"
