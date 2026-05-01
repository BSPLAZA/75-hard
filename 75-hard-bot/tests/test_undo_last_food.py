"""Regression tests for undo_last_food's auto-reflip and card-refresh behavior.

Bug: undo_last_food only deleted the diet_log row. It did NOT recompute
whether the user was still over their numeric diet goal. So a user who
crossed 170g protein (auto-confirming diet) and then undid the crossing
entry stayed "diet_done = 1" forever, even though the log was below goal.

Group meeting (V3b) flagged this under "DM-to-group-card incongruence":
DM state and card state diverge silently. This test pins the fix.
"""

import os

# Module load needs these env vars set
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import pytest
import pytest_asyncio

from bot.database import Database
from bot.utils.luke_chat import _execute_tool


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


async def _seed_user_with_card_for_today(db, user_id=111, name="Alice"):
    """Seed a user, a checkin row for today's challenge day, and a daily_cards row.

    `_execute_tool` reads the current challenge day from `daily_cards`, so this
    keeps tests deterministic regardless of wall-clock time.
    """
    await db.add_user(user_id, name)
    # daily_cards drives get_current_challenge_day; insert a row for day=1.
    await db.save_card(day_number=1, date="2026-04-15", message_id=1, chat_id=-1)
    await db.create_checkin(user_id, 1, "2026-04-15")
    return user_id


@pytest.mark.asyncio
async def test_undo_last_food_unflips_diet_when_below_goal(db):
    """User logs over goal, diet auto-confirms, undo last entry → diet un-flips."""
    user_id = await _seed_user_with_card_for_today(db)
    await db.set_diet_plan(user_id, "170g protein")

    # 100g (still below goal)
    await db.log_diet_entry(user_id, 1, "chicken breast",
                             extracted_value=100, extracted_unit="protein_g")
    # 80g (now 180g — goal crossed). Mimic log_food's auto-flip explicitly.
    await db.log_diet_entry(user_id, 1, "protein shake",
                             extracted_value=80, extracted_unit="protein_g")
    await db.toggle_diet(user_id, 1)
    checkin = await db.get_checkin(user_id, 1)
    assert checkin["diet_done"] == 1, "fixture: diet should be confirmed pre-undo"

    result = await _execute_tool("undo_last_food", {}, db, user_id=user_id)

    checkin = await db.get_checkin(user_id, 1)
    assert checkin["diet_done"] == 0, "diet_done must un-flip when undo drops below goal"
    assert result.startswith("REFRESH_CARD:"), (
        f"un-flip path must signal a card refresh, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_undo_last_food_keeps_diet_when_still_above_goal(db):
    """If user is still over goal after undo, diet_done stays confirmed."""
    user_id = await _seed_user_with_card_for_today(db)
    await db.set_diet_plan(user_id, "170g protein")

    # 200g (over goal alone)
    await db.log_diet_entry(user_id, 1, "huge meal",
                             extracted_value=200, extracted_unit="protein_g")
    # 30g (total 230g, still over even if 30g is removed — total would be 200g)
    await db.log_diet_entry(user_id, 1, "extra shake",
                             extracted_value=30, extracted_unit="protein_g")
    await db.toggle_diet(user_id, 1)

    result = await _execute_tool("undo_last_food", {}, db, user_id=user_id)

    checkin = await db.get_checkin(user_id, 1)
    assert checkin["diet_done"] == 1, "diet stays confirmed when still over goal"
    # No card-state change → no REFRESH_CARD prefix needed
    assert not result.startswith("REFRESH_CARD:"), (
        f"no state change should NOT signal refresh, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_undo_last_food_qualitative_diet_no_unflip(db):
    """Qualitative diets (no parseable goal) don't auto-unflip — same as log_food path."""
    user_id = await _seed_user_with_card_for_today(db)
    await db.set_diet_plan(user_id, "clean eating, no processed snacks")

    await db.log_diet_entry(user_id, 1, "salad", extracted_value=None, extracted_unit="clean")
    await db.toggle_diet(user_id, 1)  # user manually confirmed via confirm_diet_dm

    result = await _execute_tool("undo_last_food", {}, db, user_id=user_id)

    checkin = await db.get_checkin(user_id, 1)
    # Qualitative diet: undo_last_food must not touch diet_done — that's confirm_diet_dm's job.
    assert checkin["diet_done"] == 1
    assert not result.startswith("REFRESH_CARD:")


@pytest.mark.asyncio
async def test_undo_last_food_no_entries_returns_message(db):
    """Empty diet log → friendly nothing-to-undo message, no DB writes."""
    user_id = await _seed_user_with_card_for_today(db)

    result = await _execute_tool("undo_last_food", {}, db, user_id=user_id)
    assert "Nothing to undo" in result
