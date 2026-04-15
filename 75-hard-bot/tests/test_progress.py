"""Tests for progress utility functions."""

from datetime import date

from bot.utils.progress import (
    WATER_GOAL,
    water_bar,
    get_day_number,
    is_all_complete,
    get_missing_tasks,
)


# ── water_bar ─────────────────────────────────────────────────────────

def test_water_bar_zero():
    assert water_bar(0) == "░░░░░░░░░░"


def test_water_bar_five():
    # 5/16 * 10 = 3.125 -> round to 3
    assert water_bar(5) == "▓▓▓░░░░░░░"


def test_water_bar_eight():
    # 8/16 * 10 = 5.0 -> exactly 5
    assert water_bar(8) == "▓▓▓▓▓░░░░░"


def test_water_bar_full():
    assert water_bar(16) == "▓▓▓▓▓▓▓▓▓▓"


def test_water_bar_over_max():
    """Cups above 16 should still cap at full bar."""
    assert water_bar(20) == "▓▓▓▓▓▓▓▓▓▓"


def test_water_bar_negative():
    """Negative cups should clamp to empty bar."""
    assert water_bar(-3) == "░░░░░░░░░░"


# ── get_day_number ────────────────────────────────────────────────────

def test_day_number_on_start_date():
    start = date(2026, 4, 15)
    assert get_day_number(start, start) == 1


def test_day_number_day_10():
    start = date(2026, 4, 15)
    today = date(2026, 4, 24)
    assert get_day_number(start, today) == 10


def test_day_number_before_start():
    start = date(2026, 4, 15)
    today = date(2026, 4, 14)
    assert get_day_number(start, today) == 0


def test_day_number_after_end():
    start = date(2026, 4, 15)
    today = date(2026, 6, 28)  # day 75
    assert get_day_number(start, today) == 75


# ── is_all_complete ───────────────────────────────────────────────────

def _make_checkin(**overrides) -> dict:
    """Build a checkin dict with sensible defaults (all incomplete)."""
    base = {
        "workout_1_done": 0,
        "workout_2_done": 0,
        "water_cups": 0,
        "diet_done": 0,
        "reading_done": 0,
        "photo_done": 0,
    }
    base.update(overrides)
    return base


def test_is_all_complete_true():
    checkin = _make_checkin(
        workout_1_done=1,
        workout_2_done=1,
        water_cups=16,
        diet_done=1,
        reading_done=1,
        photo_done=1,
    )
    assert is_all_complete(checkin) is True


def test_is_all_complete_false_missing_workout():
    checkin = _make_checkin(
        workout_1_done=1,
        workout_2_done=0,
        water_cups=16,
        diet_done=1,
        reading_done=1,
        photo_done=1,
    )
    assert is_all_complete(checkin) is False


def test_is_all_complete_false_water_short():
    checkin = _make_checkin(
        workout_1_done=1,
        workout_2_done=1,
        water_cups=15,
        diet_done=1,
        reading_done=1,
        photo_done=1,
    )
    assert is_all_complete(checkin) is False


def test_is_all_complete_all_zeros():
    checkin = _make_checkin()
    assert is_all_complete(checkin) is False


# ── get_missing_tasks ─────────────────────────────────────────────────

def test_get_missing_tasks_all_missing():
    checkin = _make_checkin()
    missing = get_missing_tasks(checkin)
    assert "Workout 1" in missing
    assert "Workout 2" in missing
    assert "Water (0/16)" in missing
    assert "Reading" in missing
    assert "Progress photo" in missing
    assert "Diet" in missing
    assert len(missing) == 6


def test_get_missing_tasks_none_missing():
    checkin = _make_checkin(
        workout_1_done=1,
        workout_2_done=1,
        water_cups=16,
        diet_done=1,
        reading_done=1,
        photo_done=1,
    )
    assert get_missing_tasks(checkin) == []


def test_get_missing_tasks_partial():
    checkin = _make_checkin(
        workout_1_done=1,
        workout_2_done=1,
        water_cups=10,
        diet_done=1,
        reading_done=0,
        photo_done=1,
    )
    missing = get_missing_tasks(checkin)
    assert missing == ["Water (10/16)", "Reading"]
