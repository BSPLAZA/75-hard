"""Coverage tests for conversation_log — verifies all source types make it in.

Pre-fix the audit pipeline only saw `source='dm'` rows. Group-chat and
scheduled-job emissions never reached the table, so the audit was structurally
incomplete. These tests pin the contract: every source value the system
produces must be writable via the documented helpers, and SELECT DISTINCT
source must return all of them.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import pytest
import pytest_asyncio

from bot.database import Database


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_log_scheduled_emission_writes_row(db):
    """Scheduled emissions land in conversation_log with the right shape."""
    await db.log_scheduled_emission("morning", "day 18 lets go")
    rows = await db.get_recent_conversations(limit=10)
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["source"] == "scheduled"
    assert r["telegram_id"] == 0  # sentinel for "no user"
    assert r["user_name"] is None
    assert r["user_message"] == "[schedule: morning]"
    assert r["luke_response"] == "day 18 lets go"


@pytest.mark.asyncio
async def test_log_scheduled_emission_handles_none_response(db):
    """Anthropic call returning None (e.g. spicy 'NONE' rejection) still logs."""
    await db.log_scheduled_emission("spicy", None)
    rows = await db.get_recent_conversations(limit=10)
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["luke_response"] == "[empty]"


@pytest.mark.asyncio
async def test_log_scheduled_emission_with_error(db):
    """Failed Anthropic calls log the exception so the audit sees the gap."""
    await db.log_scheduled_emission("weekly", None, error="rate_limit_error")
    rows = await db.get_recent_conversations(limit=10)
    r = dict(rows[0])
    assert r["luke_response"].startswith("[ERROR]")
    assert "rate_limit_error" in r["luke_response"]


@pytest.mark.asyncio
async def test_admin_test_distinguishable_from_real_schedule(db):
    """admin_test triggers tagged separately so the audit doesn't conflate
    operator previews with real production output."""
    await db.log_scheduled_emission("morning", "real one")
    await db.log_scheduled_emission("morning", "admin preview", triggered_by="admin_test")
    rows = await db.get_recent_conversations(limit=10)
    messages = sorted(dict(r)["user_message"] for r in rows)
    assert messages == ["[admin_test: morning]", "[schedule: morning]"]


@pytest.mark.asyncio
async def test_all_three_source_types_recorded(db):
    """The load-bearing assertion: dm + group + scheduled all writable, all
    visible in SELECT DISTINCT source. Pre-fix only 'dm' would have shown up.
    """
    # source='dm' — the existing chat_with_luke path
    await db.add_conversation_log(
        telegram_id=12345, user_name="Alice", source="dm",
        user_message="logged water", luke_response="got it",
    )
    # source='group' — reserved for future group-chat replies. Helper accepts
    # any source; the only barrier was no caller ever wrote one.
    await db.add_conversation_log(
        telegram_id=-5110469836, user_name="Alice", source="group",
        user_message="@luke what day", luke_response="day 18",
    )
    # source='scheduled' — the new helper for job + admin emissions
    await db.log_scheduled_emission("morning", "good morning crew")

    rows = await db.get_recent_conversations(limit=10)
    sources = {dict(r)["source"] for r in rows}
    assert sources == {"dm", "group", "scheduled"}


@pytest.mark.asyncio
async def test_telegram_id_zero_is_distinguishable_in_sql(db):
    """The sentinel 0 makes scheduled rows partitionable from user rows."""
    await db.add_conversation_log(
        telegram_id=12345, user_name="Alice", source="dm",
        user_message="x", luke_response="y",
    )
    await db.log_scheduled_emission("morning", "z")

    # Direct SQL — what the audit script does in production
    async with db._conn.execute(
        "SELECT COUNT(*) AS n FROM conversation_log WHERE telegram_id > 0"
    ) as cur:
        user_count = (await cur.fetchone())["n"]
    async with db._conn.execute(
        "SELECT COUNT(*) AS n FROM conversation_log WHERE telegram_id = 0"
    ) as cur:
        scheduled_count = (await cur.fetchone())["n"]

    assert user_count == 1
    assert scheduled_count == 1
