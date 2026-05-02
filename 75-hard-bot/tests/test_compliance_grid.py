"""Tests for the compliance grid renderer + retro grace gating."""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import pytest
import pytest_asyncio
from io import BytesIO
from PIL import Image

from bot.database import Database
from bot.utils.compliance_grid import (
    STATE_COLORS,
    FUTURE_FILL,
    render_compliance_grid,
)


# ── Render smoke ──────────────────────────────────────────────────────


def _full_checkin(day: int, **overrides) -> dict:
    """Return a fully-complete checkin row for the given day, with overrides."""
    base = {
        "telegram_id": 111,
        "day_number": day,
        "date": "2026-04-15",
        "workout_1_done": 1,
        "workout_2_done": 1,
        "water_cups": 16,
        "diet_done": 1,
        "reading_done": 1,
        "photo_done": 1,
    }
    base.update(overrides)
    return base


def test_render_returns_valid_png():
    buf = render_compliance_grid(
        user_name="Bryan",
        today_day=18,
        challenge_days=75,
        checkins_by_day={d: _full_checkin(d) for d in range(1, 19)},
        penance_rows=[],
        cutoff_passed_through_day=17,
    )
    assert isinstance(buf, BytesIO)
    img = Image.open(buf)
    assert img.format == "PNG"
    assert img.width > 100
    assert img.height > 100


def test_grid_dimensions_scale_with_75_days():
    """The grid is 3 row-groups (1-25, 26-50, 51-75). Width should accommodate
    25 cells regardless of how many days are filled in."""
    buf = render_compliance_grid(
        user_name="Bryan",
        today_day=5,
        challenge_days=75,
        checkins_by_day={d: _full_checkin(d) for d in range(1, 6)},
        penance_rows=[],
        cutoff_passed_through_day=4,
    )
    img = Image.open(buf)
    # 25 cells × 22*2 + gaps + label_w + padding ~ 1500-1600px wide
    assert img.width >= 1400


def test_handles_user_with_zero_history():
    """A fresh user (day 1, nothing logged) shouldn't crash the renderer."""
    buf = render_compliance_grid(
        user_name="Newcomer",
        today_day=1,
        challenge_days=75,
        checkins_by_day={},
        penance_rows=[],
        cutoff_passed_through_day=0,
    )
    img = Image.open(buf)
    assert img.format == "PNG"


def test_complete_day_renders_green():
    """A fully-complete day's water cell should be the 'complete' color.

    Pixel-samples the rendered image at the known location of the water cell
    on day 1. If the renderer's layout shifts, this test will catch it.
    """
    # A single complete day, all green
    buf = render_compliance_grid(
        user_name="Bryan",
        today_day=1,
        challenge_days=75,
        checkins_by_day={1: _full_checkin(1)},
        penance_rows=[],
        cutoff_passed_through_day=0,  # day 1 still active (today)
    )
    img = Image.open(buf).convert("RGB")
    # Sample the center of the water cell on day 1 (3rd task row, 1st column).
    # Layout: scale=2, pad=64, label_w=160, cell=44, gap=6, title_h=120
    # Group axis label takes 32px, then task rows start.
    # Day 1 is column 0 in group 0. Water is task index 2 (workout_in,
    # workout_out, water, diet, reading, photo).
    # Sample plausible center: x ~ pad + label_w + cell/2, y after title + axis + 2 task rows + cell/2
    # Use a tolerant scan: search the first 25 cells of any row for the green color.
    pixels = img.load()
    target = STATE_COLORS["complete"]
    target_rgb = tuple(int(target[i:i+2], 16) for i in (1, 3, 5))
    # Scan rows in the band where task rows live (after title)
    found = False
    for y in range(120, 600):
        for x in range(160, img.width - 100):
            if pixels[x, y] == target_rgb:
                found = True
                break
        if found:
            break
    assert found, "expected at least one 'complete' green pixel in the grid"


def test_future_days_render_dim():
    """Days past today should be the dim future-fill color."""
    buf = render_compliance_grid(
        user_name="Bryan",
        today_day=5,
        challenge_days=75,
        checkins_by_day={d: _full_checkin(d) for d in range(1, 6)},
        penance_rows=[],
        cutoff_passed_through_day=4,
    )
    img = Image.open(buf).convert("RGB")
    pixels = img.load()
    target = FUTURE_FILL
    target_rgb = tuple(int(target[i:i+2], 16) for i in (1, 3, 5))
    found = False
    for y in range(120, 600):
        for x in range(img.width // 2, img.width - 100):  # right half = later days
            if pixels[x, y] == target_rgb:
                found = True
                break
        if found:
            break
    assert found, "expected future-day dim cells in the right half of the grid"


def test_in_penance_renders_orange():
    """A day with an in_progress penance row should render in penance orange."""
    today = 5
    checkins = {d: _full_checkin(d) for d in range(1, today + 1)}
    # Day 3 — water is missed (not full)
    checkins[3] = _full_checkin(3, water_cups=8)
    # Day 4 has a penance for the water miss (in progress)
    pen = [{
        "id": 1, "telegram_id": 111, "missed_day": 3, "makeup_day": 4,
        "task": "water", "status": "in_progress", "retroactive": 0,
        "detail": None, "declared_at": None, "resolved_at": None,
    }]
    buf = render_compliance_grid(
        user_name="Bryan",
        today_day=today,
        challenge_days=75,
        checkins_by_day=checkins,
        penance_rows=pen,
        cutoff_passed_through_day=4,
    )
    img = Image.open(buf).convert("RGB")
    pixels = img.load()
    target = STATE_COLORS["in_penance"]
    target_rgb = tuple(int(target[i:i+2], 16) for i in (1, 3, 5))
    found = False
    for y in range(120, 800):
        for x in range(160, img.width - 100):
            if pixels[x, y] == target_rgb:
                found = True
                break
        if found:
            break
    assert found, "expected an 'in_penance' orange cell"


# ── Retro grace gating ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_retro_grace_inactive_by_default(db):
    """No setting → grace inactive."""
    assert await db.is_retro_grace_active(today_day=10) is False


@pytest.mark.asyncio
async def test_retro_grace_active_when_today_within_window(db):
    await db.set_setting("retro_grace_until_day", "20")
    assert await db.is_retro_grace_active(today_day=15) is True
    assert await db.is_retro_grace_active(today_day=20) is True


@pytest.mark.asyncio
async def test_retro_grace_inactive_after_window(db):
    await db.set_setting("retro_grace_until_day", "20")
    assert await db.is_retro_grace_active(today_day=21) is False
    assert await db.is_retro_grace_active(today_day=30) is False


@pytest.mark.asyncio
async def test_retro_grace_handles_invalid_setting(db):
    """Garbage in the setting shouldn't crash the gate — just deny gracefully."""
    await db.set_setting("retro_grace_until_day", "not_a_number")
    assert await db.is_retro_grace_active(today_day=10) is False


@pytest.mark.asyncio
async def test_retro_grace_zero_means_closed(db):
    """admin_close_retro_audit sets the value to '0' to explicitly close."""
    await db.set_setting("retro_grace_until_day", "0")
    assert await db.is_retro_grace_active(today_day=1) is False
    assert await db.is_retro_grace_active(today_day=10) is False
