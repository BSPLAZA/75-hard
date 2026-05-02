"""Tests for the deploy-time release announcement + debounce.

Pinned contracts:
  1. _is_within_debounce returns True iff < DEBOUNCE_MINUTES since last announce.
  2. maybe_announce_release sends only when (a) there's pending content AND
     (b) debounce window has elapsed AND (c) group_chat_id is set.
  3. On successful send, both last_announced_release_version AND
     last_release_announce_at are updated atomically.
  4. A failed send must NOT advance either marker — retry happens next deploy.
  5. The morning card path also stamps last_release_announce_at so deploy-time
     correctly debounces against morning posts.
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import inspect

import pytest
import pytest_asyncio

from bot.database import Database
from bot import release_notes
from bot.release_notes import (
    CURRENT_VERSION,
    RELEASE_ANNOUNCE_DEBOUNCE_MINUTES,
    _is_within_debounce,
    maybe_announce_release,
)


# ── Pure debounce helper ────────────────────────────────────────────────


def test_debounce_no_prior_announce_returns_false():
    """First-ever announcement is never debounced."""
    assert _is_within_debounce(None) is False
    assert _is_within_debounce("") is False


def test_debounce_recent_announce_returns_true():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    last = (now - timedelta(minutes=10)).isoformat()
    assert _is_within_debounce(last, now=now) is True


def test_debounce_old_announce_returns_false():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    last = (now - timedelta(minutes=RELEASE_ANNOUNCE_DEBOUNCE_MINUTES + 1)).isoformat()
    assert _is_within_debounce(last, now=now) is False


def test_debounce_naive_timestamp_treated_as_utc():
    """Older entries written without timezone info shouldn't crash."""
    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    last = (now - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
    assert _is_within_debounce(last, now=now) is True


def test_debounce_garbage_timestamp_returns_false():
    """Bad data shouldn't block announcements forever — fail-open."""
    assert _is_within_debounce("not-an-iso-timestamp") is False


def test_debounce_window_is_60_minutes():
    """Bryan's spec: don't spam, but don't go all-day silent. 60 min."""
    assert RELEASE_ANNOUNCE_DEBOUNCE_MINUTES == 60


# ── maybe_announce_release end-to-end ───────────────────────────────────


class _StubBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.fail_next = False

    async def send_message(self, *, chat_id, text, **kwargs):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated send failure")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=len(self.sent))


def _make_app(db, chat_id=-1003832139369, bot=None):
    """Mimic enough of telegram.ext.Application for maybe_announce_release."""
    if bot is None:
        bot = _StubBot()
    return SimpleNamespace(
        bot=bot,
        bot_data={"db": db, "group_chat_id": chat_id},
    )


@pytest_asyncio.fixture
async def fresh_db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_announce_when_pending_and_no_prior(fresh_db):
    """First-ever announce: pending content, no last_at → SEND."""
    db = fresh_db
    app = _make_app(db)
    sent = await maybe_announce_release(app)
    assert sent is True
    assert len(app.bot.sent) == 1
    # Marker advanced
    assert (await db.get_setting("last_announced_release_version")) == str(CURRENT_VERSION)
    assert (await db.get_setting("last_release_announce_at")) is not None


@pytest.mark.asyncio
async def test_no_announce_when_caught_up(fresh_db):
    """Marker already at CURRENT_VERSION → no pending content → NO send."""
    db = fresh_db
    await db.set_setting("last_announced_release_version", str(CURRENT_VERSION))
    app = _make_app(db)
    sent = await maybe_announce_release(app)
    assert sent is False
    assert app.bot.sent == []


@pytest.mark.asyncio
async def test_no_announce_when_debounce_active(fresh_db):
    """Pending content but recent announce → debounced."""
    db = fresh_db
    # Pending: marker behind CURRENT_VERSION
    await db.set_setting("last_announced_release_version", "0")
    # Recent announce: 10 min ago
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    await db.set_setting("last_release_announce_at", recent)

    app = _make_app(db)
    sent = await maybe_announce_release(app)
    assert sent is False
    assert app.bot.sent == []
    # Marker UNCHANGED
    assert (await db.get_setting("last_announced_release_version")) == "0"


@pytest.mark.asyncio
async def test_announce_after_debounce_expires(fresh_db):
    """Old announce timestamp (> window) lets the next deploy through."""
    db = fresh_db
    await db.set_setting("last_announced_release_version", "0")
    old = (datetime.now(timezone.utc) - timedelta(minutes=RELEASE_ANNOUNCE_DEBOUNCE_MINUTES + 5)).isoformat()
    await db.set_setting("last_release_announce_at", old)

    app = _make_app(db)
    sent = await maybe_announce_release(app)
    assert sent is True
    assert len(app.bot.sent) == 1


@pytest.mark.asyncio
async def test_no_announce_when_no_group_chat(fresh_db):
    """Without group_chat_id, can't send anywhere — bail."""
    db = fresh_db
    app = _make_app(db, chat_id=None)
    sent = await maybe_announce_release(app)
    assert sent is False
    # And no marker advance
    assert (await db.get_setting("last_announced_release_version")) is None


@pytest.mark.asyncio
async def test_failed_send_does_not_advance_marker(fresh_db):
    """If the send raises, both markers stay so retry works on next deploy."""
    db = fresh_db
    bot = _StubBot()
    bot.fail_next = True
    app = _make_app(db, bot=bot)
    sent = await maybe_announce_release(app)
    assert sent is False
    # Markers UNCHANGED
    assert (await db.get_setting("last_announced_release_version")) is None
    assert (await db.get_setting("last_release_announce_at")) is None


@pytest.mark.asyncio
async def test_morning_card_path_also_stamps_timestamp():
    """Source-level: morning_card_job's announce branch must update
    last_release_announce_at, otherwise deploy-time path can't debounce
    against the morning post."""
    from bot.jobs import scheduler as sched
    src = inspect.getsource(sched.morning_card_job)
    # The release-notes block in morning_card_job should set both keys
    assert "last_announced_release_version" in src
    assert "last_release_announce_at" in src


@pytest.mark.asyncio
async def test_two_consecutive_deploys_debounce_correctly(fresh_db):
    """Realistic scenario: deploy 1 sends, deploy 2 (30 min later) is held."""
    db = fresh_db
    app = _make_app(db)

    # Deploy 1
    s1 = await maybe_announce_release(app)
    assert s1 is True

    # Simulate 30-min later: marker NOT moved further (no new releases happened)
    # but build_announcement against current marker would return None.
    # To simulate a NEW release between deploys, we rewind the marker:
    await db.set_setting("last_announced_release_version", "0")

    s2 = await maybe_announce_release(app)
    assert s2 is False, "second deploy within window must be debounced"
    assert len(app.bot.sent) == 1, "still only one outbound message"
