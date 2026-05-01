"""Tests for the strict-midnight-PT backfill cutoff (v51).

Group meeting (V3b) asked for "midnight PT next day" cutoff. The
implementation anchors to daily_checkins.checkin_date for the target
day, computing days_past = pt_today - target_anchor. Window is open
when days_past <= 1, closed when > 1.

Tests use real wall-clock time but synthesize stale checkin dates
to exercise the deny path without time-mocking dependencies.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
import pytz

from bot.database import Database
from bot.utils.luke_chat import _execute_tool

PT = pytz.timezone("US/Pacific")


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


def _pt_today():
    return datetime.now(PT).date()


async def _seed(db, *, card_day, target_day, target_date_str, user_id=111):
    """Seed user, daily_cards row for `card_day`, and a checkin row for
    `target_day` with checkin_date set to target_date_str."""
    await db.add_user(user_id, "Alice")
    await db.save_card(day_number=card_day, date=str(_pt_today()), message_id=1, chat_id=-1)
    await db.create_checkin(user_id, target_day, target_date_str)
    return user_id


@pytest.mark.asyncio
async def test_backfill_yesterday_allowed_when_anchor_is_yesterday(db):
    """Normal case: target_day == yesterday, anchor == 1 day ago PT → allow."""
    yesterday_pt = (_pt_today() - timedelta(days=1)).isoformat()
    user = await _seed(db, card_day=10, target_day=9, target_date_str=yesterday_pt)

    result = await _execute_tool(
        "backfill_task",
        {"task": "water", "day_number": 9, "detail": "16"},
        db, user_id=user,
    )
    assert "DENIED" not in result, f"backfill of legitimate yesterday must be allowed, got: {result!r}"
    assert "REFRESH_CARD:" in result


@pytest.mark.asyncio
async def test_backfill_yesterday_denied_when_anchor_is_2_days_ago(db):
    """Strict midnight cutoff: anchor 2 days ago in PT → days_past=2 → DENY.

    Synthesizes the dead-zone state: card_day hasn't rolled forward but
    pt_today has advanced past the lock window.
    """
    two_days_ago_pt = (_pt_today() - timedelta(days=2)).isoformat()
    user = await _seed(db, card_day=10, target_day=9, target_date_str=two_days_ago_pt)

    result = await _execute_tool(
        "backfill_task",
        {"task": "water", "day_number": 9, "detail": "16"},
        db, user_id=user,
    )
    assert "DENIED" in result and "midnight PT" in result, (
        f"backfill must DENY past midnight PT, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_day_1_grace_overrides_strict_cutoff(db):
    """Day 1 backfill always allowed regardless of how stale anchor is."""
    long_ago = (_pt_today() - timedelta(days=50)).isoformat()
    # card_day=51 means yesterday=50; target_day=1 is far older. Grace overrides.
    user = await _seed(db, card_day=51, target_day=1, target_date_str=long_ago)

    result = await _execute_tool(
        "backfill_task",
        {"task": "water", "day_number": 1, "detail": "16"},
        db, user_id=user,
    )
    assert "DENIED" not in result, f"Day 1 grace must override strict cutoff, got: {result!r}"


@pytest.mark.asyncio
async def test_backfill_too_old_denied_before_strict_check(db):
    """Existing target_day < yesterday DENY runs first; new strict check doesn't fire here."""
    yesterday_pt = (_pt_today() - timedelta(days=1)).isoformat()
    user = await _seed(db, card_day=10, target_day=5, target_date_str=yesterday_pt)

    # target_day=5, card_day=10, yesterday=9. 5 < 9 → DENY immediately.
    result = await _execute_tool(
        "backfill_task",
        {"task": "water", "day_number": 5, "detail": "16"},
        db, user_id=user,
    )
    assert "more than 1 day old" in result, (
        f"older-than-yesterday DENY message expected, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_request_backfill_photo_strict_midnight_deny(db):
    """Photo backfill enforces same strict midnight cutoff."""
    two_days_ago_pt = (_pt_today() - timedelta(days=2)).isoformat()
    user = await _seed(db, card_day=10, target_day=9, target_date_str=two_days_ago_pt)

    result = await _execute_tool(
        "request_backfill_photo",
        {"day_number": 9},
        db, user_id=user,
    )
    assert "DENIED" in result and "midnight PT" in result, (
        f"photo backfill must DENY past midnight PT, got: {result!r}"
    )


@pytest.mark.asyncio
async def test_request_backfill_photo_allowed_within_window(db):
    """Photo backfill within window returns BACKFILL_PHOTO signal."""
    yesterday_pt = (_pt_today() - timedelta(days=1)).isoformat()
    user = await _seed(db, card_day=10, target_day=9, target_date_str=yesterday_pt)

    result = await _execute_tool(
        "request_backfill_photo",
        {"day_number": 9},
        db, user_id=user,
    )
    assert result.startswith("BACKFILL_PHOTO:9"), (
        f"in-window photo backfill must signal BACKFILL_PHOTO, got: {result!r}"
    )
