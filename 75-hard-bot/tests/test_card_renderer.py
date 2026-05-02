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
    telegram_id: int = 0,
) -> dict:
    return {
        "telegram_id": telegram_id or hash(name) % 100000,
        "name": name,
        "workout_1_done": workout_1_done,
        "workout_2_done": workout_2_done,
        "water_cups": water_cups,
        "photo_done": photo_done,
        "reading_done": reading_done,
        "diet_done": diet_done,
    }


def _penance(task: str, status: str = "in_progress") -> dict:
    return {"task": task, "status": status}


def test_render_card_empty():
    """No participants should still produce a valid card with header."""
    text = render_card(day_number=1, active_count=0, prize_pool=0, checkins=[])
    assert "DAY 1 / 75" in text
    assert "<pre>" in text


def test_render_card_partial():
    checkins = [
        _make_checkin_row("Alice", workout_1_done=1, water_cups=6),
    ]
    text = render_card(day_number=3, active_count=5, prize_pool=375, checkins=checkins)
    assert "DAY 3 / 75" in text
    assert "Alice" in text
    # Workout 1 done => +, workout 2 not done => dot
    assert "+" in text
    # Should NOT have a star
    assert "*" not in text or text.count("*") == 0


def test_render_card_all_complete_star():
    checkins = [
        _make_checkin_row("Bob", 1, 1, 16, 1, 1, 1),
    ]
    text = render_card(day_number=10, active_count=3, prize_pool=225, checkins=checkins)
    assert "Bob" in text
    assert "*" in text  # star marker for complete


def test_render_card_not_still_standing_when_partial():
    """When not all participants are complete, no stars for incomplete users."""
    checkins = [
        _make_checkin_row("Alice", 1, 1, 16, 1, 1, 1),
        _make_checkin_row("Bob", 1, 0, 16, 1, 1, 1),
    ]
    text = render_card(day_number=5, active_count=2, prize_pool=150, checkins=checkins)
    # Alice should have star, Bob should not
    lines = text.split("\n")
    for line in lines:
        if "Bob" in line:
            assert "*" not in line


def test_render_card_has_column_header():
    text = render_card(day_number=1, active_count=1, prize_pool=75, checkins=[
        _make_checkin_row("Eve"),
    ])
    assert "WORK" in text
    assert "WATER" in text
    assert "P R D" in text


def test_render_card_has_weekday():
    text = render_card(day_number=1, active_count=1, prize_pool=75, checkins=[
        _make_checkin_row("Eve"),
    ])
    # Day 1 = April 15, 2026 = Wednesday
    assert "Wednesday" in text


# ── Penance display ───────────────────────────────────────────────────


def test_water_penance_doubles_divisor():
    """Water cell shows /32 when user is in penance for water today."""
    bryan = _make_checkin_row("Bryan", water_cups=12, telegram_id=111)
    text = render_card(
        day_number=18, active_count=1, prize_pool=75,
        checkins=[bryan],
        penances_by_user={111: [_penance("water")]},
    )
    assert "12/32" in text
    assert "12/16" not in text


def test_no_penance_keeps_normal_divisor():
    bryan = _make_checkin_row("Bryan", water_cups=12, telegram_id=111)
    text = render_card(
        day_number=18, active_count=1, prize_pool=75,
        checkins=[bryan],
    )
    assert "12/16" in text


def test_workout_penance_renders_p_marker_for_pending():
    """Workout cell shows 'p' (lowercase) instead of '·' when penance is
    pending for that workout type."""
    bryan = _make_checkin_row("Bryan", telegram_id=111)
    text = render_card(
        day_number=18, active_count=1, prize_pool=75,
        checkins=[bryan],
        penances_by_user={111: [_penance("workout_indoor")]},
    )
    # Bryan's row should have 'p' for workout_1 (indoor)
    bryan_line = next(l for l in text.split("\n") if "Bryan" in l)
    # Format: "Bryan   X  Y  ..." — first cell after name is workout_1 (indoor)
    assert " p  " in bryan_line


def test_workout_done_overrides_penance_marker():
    """Once user completes a penance-task today, cell shows '+' (recovered)
    not 'p' (still pending)."""
    bryan = _make_checkin_row("Bryan", workout_1_done=1, telegram_id=111)
    text = render_card(
        day_number=18, active_count=1, prize_pool=75,
        checkins=[bryan],
        penances_by_user={111: [_penance("workout_indoor")]},
    )
    bryan_line = next(l for l in text.split("\n") if "Bryan" in l)
    # First cell after name should be '+'
    assert "Bryan" in bryan_line
    # The two-space gap then the workout_1 cell
    parts = bryan_line.split()
    # parts[0] = name, parts[1] = w1, parts[2] = w2
    assert parts[1] == "+"


def test_penance_footer_lists_active_users():
    """Footer line names who's in penance for what."""
    bryan = _make_checkin_row("Bryan", telegram_id=111)
    kat = _make_checkin_row("Kat", telegram_id=222)
    text = render_card(
        day_number=18, active_count=2, prize_pool=150,
        checkins=[bryan, kat],
        penances_by_user={
            111: [_penance("water")],
            222: [_penance("workout_indoor"), _penance("reading")],
        },
    )
    assert "penance today:" in text
    assert "bryan 2× water" in text
    assert "kat 2× indoor + reading" in text


def test_no_footer_when_no_penance():
    """Card stays clean for the common case of no active penances."""
    text = render_card(
        day_number=5, active_count=1, prize_pool=75,
        checkins=[_make_checkin_row("Alice", telegram_id=111)],
        penances_by_user={},
    )
    assert "penance today:" not in text


def test_recovered_penance_does_not_appear_in_footer():
    """Only in_progress penances render — recovered ones are silent."""
    bryan = _make_checkin_row("Bryan", water_cups=32, telegram_id=111)
    text = render_card(
        day_number=18, active_count=1, prize_pool=75,
        checkins=[bryan],
        penances_by_user={111: [_penance("water", status="recovered")]},
    )
    assert "penance today:" not in text


def test_diet_never_gets_penance_marker():
    """Diet is binary — no penance possible. Cell stays '+' or '·'."""
    bryan = _make_checkin_row("Bryan", telegram_id=111)
    # Even if somehow a diet penance row leaks in, the renderer must ignore it.
    text = render_card(
        day_number=18, active_count=1, prize_pool=75,
        checkins=[bryan],
        penances_by_user={111: [_penance("diet")]},
    )
    bryan_line = next(l for l in text.split("\n") if "Bryan" in l)
    # No 'p' marker should appear anywhere in the row — diet is binary.
    # (The penance task→cell map intentionally omits 'diet'.)
    assert " p " not in bryan_line and "p\n" not in bryan_line and not bryan_line.endswith("p")
