"""Regression tests for the morning-nudge disambiguation flow (step 2).

Pins three contracts:
  1. The declare_penance tool definition exists and uses the right enum.
  2. critical_rule #8 (penance disambiguation) is in LUKE_CHAT_SYSTEM.
  3. The morning_after_reminder_job is wired to log its outbound DM into
     conversation_log so Luke's history hydrate sees it.

These don't test the live LLM behavior (impossible without mocking Anthropic),
but they pin the structural pieces that make the disambiguation possible.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import inspect

from bot.utils import luke_chat
from bot.jobs import scheduler
from bot.penance import PENANCE_ABLE_TASKS


# ── tool definition ────────────────────────────────────────────────────

def _find_tool(name: str) -> dict | None:
    for t in luke_chat.TOOLS:
        if t["name"] == name:
            return t
    return None


def test_declare_penance_tool_registered():
    tool = _find_tool("declare_penance")
    assert tool is not None, "declare_penance tool must be registered in luke_chat.TOOLS"


def test_declare_penance_enum_matches_penance_able_set():
    tool = _find_tool("declare_penance")
    enum = tool["input_schema"]["properties"]["task"]["enum"]
    assert set(enum) == PENANCE_ABLE_TASKS


def test_declare_penance_excludes_diet():
    tool = _find_tool("declare_penance")
    enum = tool["input_schema"]["properties"]["task"]["enum"]
    assert "diet" not in enum, "diet must not be penance-able — binary task only"


def test_declare_penance_requires_task():
    tool = _find_tool("declare_penance")
    assert tool["input_schema"]["required"] == ["task"]


def test_declare_penance_description_warns_against_vague_calls():
    """The tool description must explicitly tell the LLM not to call this
    without a specific task — that's the structural anti-phantom guard."""
    tool = _find_tool("declare_penance")
    desc = tool["description"].lower()
    assert "never call this without a specific task" in desc
    assert "ask" in desc  # tells the LLM to ask first when ambiguous


# ── system prompt critical rule 8 ──────────────────────────────────────

def test_critical_rule_8_present():
    """Rule 8 (penance disambiguation) must be in LUKE_CHAT_SYSTEM."""
    assert "8. PENANCE DISAMBIGUATION" in luke_chat.LUKE_CHAT_SYSTEM


def test_critical_rule_8_says_never_call_without_task():
    sys = luke_chat.LUKE_CHAT_SYSTEM
    assert "NEVER call declare_penance without a specific task" in sys


def test_critical_rule_8_routes_diet_violations_away_from_penance():
    sys = luke_chat.LUKE_CHAT_SYSTEM
    # The rule must point users with diet violations to log_violation, not declare_penance
    assert "log_violation" in sys
    assert "diet is binary, no penance possible" in sys


def test_critical_rule_8_blocks_phantom_penance_claims():
    sys = luke_chat.LUKE_CHAT_SYSTEM
    # State-claim-no-tool guard generalized to penance state
    assert (
        'NEVER claim "your penance is set"' in sys
        or "NEVER claim" in sys and "penance is set" in sys
    )


# ── morning nudge wiring ───────────────────────────────────────────────

def test_morning_after_reminder_job_logs_outbound_to_conversation_log():
    """The nudge must persist itself to conversation_log so Luke's history
    hydrate picks it up. Without this, the user's reply lands in Luke's chat
    in a vacuum — no prior context — and disambiguation fails."""
    src = inspect.getsource(scheduler.morning_after_reminder_job)
    assert "add_conversation_log" in src, (
        "morning_after_reminder_job must call db.add_conversation_log so "
        "Luke's history hydrate sees the outbound nudge"
    )
    assert "[bot_nudge:morning_after]" in src, (
        "the conversation_log row should be tagged so audits can identify "
        "bot-originated nudges vs real user turns"
    )


def test_morning_after_reminder_uses_disambiguation_phrasing():
    """The nudge text must invite the per-task backfill-or-penance disambiguation
    Luke is set up to handle. Pin the contract — copy can drift, structure can't."""
    src = inspect.getsource(scheduler.morning_after_reminder_job)
    text_block = src.lower()
    assert "did you do it and forget" in text_block or "forget to log" in text_block
    assert "penance" in text_block
    assert "midnight pacific" in text_block


# ── behavioral tests with stub bot + real DB ───────────────────────────

import pytest
import pytest_asyncio
from types import SimpleNamespace

from bot.database import Database


class _StubBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, *, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


def _ctx(db, bot):
    return SimpleNamespace(bot_data={"db": db, "group_chat_id": -1}, bot=bot)


@pytest_asyncio.fixture
async def db_with_three_users():
    """Bryan + Kat + Cam, all dm_registered. Day-18 card is active so
    yesterday=17."""
    database = Database(":memory:")
    await database.init()
    for tid, name in [(111, "Bryan"), (222, "Kat"), (333, "Cam")]:
        await database.add_user(tid, name, tier=75)
        await database._conn.execute(
            "UPDATE users SET dm_registered = 1 WHERE telegram_id = ?", (tid,)
        )
    await database._conn.commit()
    await database.save_card(day_number=18, date="2026-05-02", message_id=1, chat_id=-1)
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_nudge_drives_from_active_users_not_just_checkins(db_with_three_users):
    """A user with NO checkin row for yesterday must still get nudged.
    Previously the nudge iterated checkins and silently skipped these users."""
    db = db_with_three_users
    # Only Bryan has a checkin row; Kat + Cam have nothing
    await db.create_checkin(111, 17, "2026-05-01")  # all 0

    from bot.jobs.scheduler import morning_after_reminder_job
    bot = _StubBot()
    await morning_after_reminder_job(_ctx(db, bot))

    chat_ids = {m["chat_id"] for m in bot.sent}
    assert 111 in chat_ids
    assert 222 in chat_ids, "Kat had no checkin row but missed everything — must be nudged"
    assert 333 in chat_ids, "Cam same"


@pytest.mark.asyncio
async def test_nudge_filters_out_already_declared_penance(db_with_three_users):
    """If the user declared penance for water last night, the morning nudge
    must NOT list water again."""
    db = db_with_three_users
    await db.create_checkin(111, 17, "2026-05-01")  # all 0
    # Bryan declared water penance last night
    await db.add_penance(111, missed_day=17, makeup_day=18, task="water")

    from bot.jobs.scheduler import morning_after_reminder_job
    bot = _StubBot()
    await morning_after_reminder_job(_ctx(db, bot))

    bryan_msgs = [m for m in bot.sent if m["chat_id"] == 111]
    assert bryan_msgs, "Bryan still has other tasks missing — should still be nudged"
    text = bryan_msgs[0]["text"]
    # Water must be excluded; other tasks still listed
    assert "water" not in text.lower()
    assert "indoor workout" in text.lower() or "outdoor workout" in text.lower()


@pytest.mark.asyncio
async def test_nudge_skips_user_when_all_misses_are_already_handled(db_with_three_users):
    """If every missing task already has a penance row, send NO nudge — no
    point asking 'did you do it?' for things already disambiguated."""
    db = db_with_three_users
    await db.create_checkin(111, 17, "2026-05-01")  # all 0
    # Manually declare penance for every penance-able task; diet is binary
    # so the bot can't penance it but it's also not what the nudge focuses on
    for task in ("workout_indoor", "workout_outdoor", "water", "reading", "photo"):
        await db.add_penance(111, missed_day=17, makeup_day=18, task=task)
    # Diet still missing but it's binary — nudge should still fire about it
    # since we can't auto-cover diet via penance.

    from bot.jobs.scheduler import morning_after_reminder_job
    bot = _StubBot()
    await morning_after_reminder_job(_ctx(db, bot))

    bryan_msgs = [m for m in bot.sent if m["chat_id"] == 111]
    assert len(bryan_msgs) == 1, "Bryan still has diet uncovered → nudge fires"
    assert "diet" in bryan_msgs[0]["text"].lower()
    # Nothing else should be in the nudge
    text = bryan_msgs[0]["text"].lower()
    assert "workout" not in text
    assert "water" not in text
    assert "reading" not in text


@pytest.mark.asyncio
async def test_nudge_skips_fully_complete_user(db_with_three_users):
    """User who completed everything yesterday gets NO nudge."""
    db = db_with_three_users
    await db.create_checkin(111, 17, "2026-05-01")
    await db._conn.execute(
        """UPDATE daily_checkins
           SET workout_1_done=1, workout_2_done=1, water_cups=16,
               diet_done=1, reading_done=1, photo_done=1
           WHERE telegram_id=? AND day_number=?""",
        (111, 17),
    )
    await db._conn.commit()

    from bot.jobs.scheduler import morning_after_reminder_job
    bot = _StubBot()
    await morning_after_reminder_job(_ctx(db, bot))

    assert not any(m["chat_id"] == 111 for m in bot.sent)


@pytest.mark.asyncio
async def test_nudge_lists_water_with_friendly_label(db_with_three_users):
    """Regression — old code emitted 'Water (8/16)' style labels. New code
    uses canonical task names with friendly labels for the body."""
    db = db_with_three_users
    await db.create_checkin(111, 17, "2026-05-01")
    await db._conn.execute(
        "UPDATE daily_checkins SET water_cups=8 WHERE telegram_id=? AND day_number=?",
        (111, 17),
    )
    await db._conn.commit()

    from bot.jobs.scheduler import morning_after_reminder_job
    bot = _StubBot()
    await morning_after_reminder_job(_ctx(db, bot))

    bryan_msgs = [m for m in bot.sent if m["chat_id"] == 111]
    assert bryan_msgs
    text = bryan_msgs[0]["text"].lower()
    assert "water" in text
    # Old phrasing must be gone
    assert "8/16" not in text and "(8" not in text
