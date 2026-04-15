"""Tests for the daily card renderer."""

from bot.utils.card_renderer import render_card


def _make_checkin_row(
    name: str,
    workout_1_done: int = 0,
    workout_2_done: int = 0,
    water_cups: int = 0,
    photo_done: int = 0,
    reading_done: int = 0,
    diet_done: int = 0,
) -> dict:
    return {
        "name": name,
        "workout_1_done": workout_1_done,
        "workout_2_done": workout_2_done,
        "water_cups": water_cups,
        "photo_done": photo_done,
        "reading_done": reading_done,
        "diet_done": diet_done,
    }


# ── empty card ────────────────────────────────────────────────────────

def test_render_card_empty():
    """No participants should still produce a valid card with header + footer."""
    text = render_card(day_number=1, active_count=0, prize_pool=0, checkins=[])
    assert "DAY 1 / 75" in text
    assert "0/0 STANDING" in text


# ── partial completion ────────────────────────────────────────────────

def test_render_card_partial():
    checkins = [
        _make_checkin_row(
            "Bryan",
            workout_1_done=1,
            water_cups=6,
        ),
    ]
    text = render_card(day_number=3, active_count=5, prize_pool=375, checkins=checkins)
    assert "DAY 3 / 75" in text
    assert "5 STANDING" in text
    assert "$375" in text
    assert "Bryan" in text
    # Workout 1 done => check, workout 2 not done => dots
    assert "\u2705" in text  # ✅
    assert ".." in text
    # Should NOT have a star
    assert "\u2b50" not in text


# ── all complete with star ────────────────────────────────────────────

def test_render_card_all_complete_star():
    checkins = [
        _make_checkin_row(
            "Kat",
            workout_1_done=1,
            workout_2_done=1,
            water_cups=16,
            photo_done=1,
            reading_done=1,
            diet_done=1,
        ),
    ]
    text = render_card(day_number=10, active_count=3, prize_pool=225, checkins=checkins)
    assert "Kat" in text
    assert "\u2b50" in text  # ⭐


# ── STILL STANDING ────────────────────────────────────────────────────

def test_render_card_still_standing():
    """When ALL participants complete all tasks, header says STILL STANDING."""
    checkins = [
        _make_checkin_row(
            "Bryan",
            workout_1_done=1,
            workout_2_done=1,
            water_cups=16,
            photo_done=1,
            reading_done=1,
            diet_done=1,
        ),
        _make_checkin_row(
            "Kat",
            workout_1_done=1,
            workout_2_done=1,
            water_cups=16,
            photo_done=1,
            reading_done=1,
            diet_done=1,
        ),
    ]
    text = render_card(day_number=5, active_count=2, prize_pool=150, checkins=checkins)
    assert "STILL STANDING" in text


def test_render_card_not_still_standing_when_partial():
    """When not all participants are complete, should NOT say STILL STANDING."""
    checkins = [
        _make_checkin_row(
            "Bryan",
            workout_1_done=1,
            workout_2_done=1,
            water_cups=16,
            photo_done=1,
            reading_done=1,
            diet_done=1,
        ),
        _make_checkin_row(
            "Kat",
            workout_1_done=1,
            workout_2_done=0,  # missing
            water_cups=16,
            photo_done=1,
            reading_done=1,
            diet_done=1,
        ),
    ]
    text = render_card(day_number=5, active_count=2, prize_pool=150, checkins=checkins)
    assert "STILL STANDING" not in text


# ── footer legend ─────────────────────────────────────────────────────

def test_render_card_has_footer_legend():
    text = render_card(day_number=1, active_count=1, prize_pool=75, checkins=[
        _make_checkin_row("Dev"),
    ])
    assert "W1" in text
    assert "W2" in text
    assert "WATER" in text
    assert "PIC" in text
    assert "READ" in text
    assert "DIET" in text
