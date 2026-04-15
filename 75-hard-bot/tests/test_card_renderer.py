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


def test_render_card_empty():
    """No participants should still produce a valid card with header."""
    text = render_card(day_number=1, active_count=0, prize_pool=0, checkins=[])
    assert "DAY 1 / 75" in text
    assert "<pre>" in text


def test_render_card_partial():
    checkins = [
        _make_checkin_row("Bryan", workout_1_done=1, water_cups=6),
    ]
    text = render_card(day_number=3, active_count=5, prize_pool=375, checkins=checkins)
    assert "DAY 3 / 75" in text
    assert "Bryan" in text
    # Workout 1 done => +, workout 2 not done => dot
    assert "+" in text
    # Should NOT have a star
    assert "*" not in text or text.count("*") == 0


def test_render_card_all_complete_star():
    checkins = [
        _make_checkin_row("Kat", 1, 1, 16, 1, 1, 1),
    ]
    text = render_card(day_number=10, active_count=3, prize_pool=225, checkins=checkins)
    assert "Kat" in text
    assert "*" in text  # star marker for complete


def test_render_card_not_still_standing_when_partial():
    """When not all participants are complete, no stars for incomplete users."""
    checkins = [
        _make_checkin_row("Bryan", 1, 1, 16, 1, 1, 1),
        _make_checkin_row("Kat", 1, 0, 16, 1, 1, 1),
    ]
    text = render_card(day_number=5, active_count=2, prize_pool=150, checkins=checkins)
    # Bryan should have star, Kat should not
    lines = text.split("\n")
    for line in lines:
        if "Kat" in line:
            assert "*" not in line


def test_render_card_has_column_header():
    text = render_card(day_number=1, active_count=1, prize_pool=75, checkins=[
        _make_checkin_row("Dev"),
    ])
    assert "WORK" in text
    assert "WATER" in text
    assert "P R D" in text


def test_render_card_has_weekday():
    text = render_card(day_number=1, active_count=1, prize_pool=75, checkins=[
        _make_checkin_row("Dev"),
    ])
    # Day 1 = April 15, 2026 = Wednesday
    assert "Wednesday" in text
