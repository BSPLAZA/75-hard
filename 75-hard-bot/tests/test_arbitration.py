"""Tests for step 6 — group arbitration via Telegram poll.

Pinned contracts:
  1. log_violation tool exists, gates on binary tasks, requires detail.
  2. log_violation dispatch creates an arbitration_pending penance row, sends
     a poll to the group, and stores poll_id + poll_message_id.
  3. Phantom defenses: violation state-claims without a log_violation tool
     call are flagged.
  4. record_arbitration_vote persists choice; retraction deletes; tally aggregates.
  5. /admin_arbitrations listing + /admin_arbitrate verdicts (pass/penance/fail).
  6. PollAnswerHandler is registered.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import inspect
from types import SimpleNamespace

import pytest
import pytest_asyncio

from bot.database import Database
from bot.handlers import admin as admin_module
from bot.utils import luke_chat


# ── Tool registration + schema ──────────────────────────────────────────


def _find_tool(name: str) -> dict | None:
    for t in luke_chat.TOOLS:
        if t["name"] == name:
            return t
    return None


def test_log_violation_tool_registered():
    assert _find_tool("log_violation") is not None


def test_log_violation_requires_detail():
    schema = _find_tool("log_violation")["input_schema"]
    assert "task" in schema["required"]
    assert "detail" in schema["required"]


def test_log_violation_task_enum_is_binary_only():
    """Only binary tasks (currently 'diet') belong here. Action tasks go through
    declare_penance."""
    schema = _find_tool("log_violation")["input_schema"]
    assert schema["properties"]["task"]["enum"] == ["diet"]


def test_log_violation_description_routes_action_misses_away():
    """Description must steer 'workout missed' / 'water missed' away from
    log_violation toward declare_penance."""
    desc = _find_tool("log_violation")["description"].lower()
    assert "declare_penance" in desc
    assert "binary" in desc or "diet" in desc


# ── Phantom defense ─────────────────────────────────────────────────────


def test_phantom_violation_claim_without_tool_is_flagged():
    """Luke saying 'case filed' without a log_violation tool call this turn
    must trip the phantom check."""
    result = luke_chat._check_state_claims(
        "got it. case filed for the wine. squad will vote.", tools_called=[]
    )
    assert result is not None
    assert result[0] == "violation"


def test_violation_claim_with_log_violation_call_is_ok():
    result = luke_chat._check_state_claims(
        "case filed. squad voting now.", tools_called=["log_violation"]
    )
    assert result is None


def test_violation_claim_with_unrelated_tool_is_flagged():
    """log_water_dm doesn't legitimize a violation-class claim."""
    result = luke_chat._check_state_claims(
        "posted to the group for the wine.", tools_called=["log_water_dm"]
    )
    assert result is not None


# ── Dispatch — DB fixture ───────────────────────────────────────────────


class _StubBot:
    """Captures send_message + send_poll calls."""

    def __init__(self, poll_id: str = "tg_poll_42", message_id: int = 555):
        self.sent: list[dict] = []
        self.polls: list[dict] = []
        self._poll_id = poll_id
        self._message_id = message_id

    async def send_message(self, *, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=self._message_id)

    async def send_poll(self, *, chat_id, question, options, **kwargs):
        self.polls.append(
            {"chat_id": chat_id, "question": question, "options": options, **kwargs}
        )
        # Telegram returns a Message with .poll set
        poll_obj = SimpleNamespace(id=self._poll_id)
        return SimpleNamespace(message_id=self._message_id, poll=poll_obj)


@pytest_asyncio.fixture
async def db_with_bryan():
    database = Database(":memory:")
    await database.init()
    await database.add_user(111, "Bryan", tier=75)
    await database.save_card(day_number=18, date="2026-05-02", message_id=1, chat_id=-1)
    yield database
    await database.close()


def _make_context(db, bot):
    return SimpleNamespace(bot_data={"db": db, "group_chat_id": -1}, bot=bot)


@pytest.mark.asyncio
async def test_log_violation_creates_arbitration_pending_row(db_with_bryan):
    db = db_with_bryan
    bot = _StubBot()
    result = await luke_chat._execute_tool(
        "log_violation",
        {"task": "diet", "detail": "one glass of wine at dinner"},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    assert "REFRESH_CARD" in result or "case" in result.lower()
    rows = await db.get_active_penances(111)
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["status"] == "arbitration_pending"
    assert r["task"] == "diet"
    assert "wine" in (r["detail"] or "")


@pytest.mark.asyncio
async def test_log_violation_sends_group_poll(db_with_bryan):
    db = db_with_bryan
    bot = _StubBot()
    await luke_chat._execute_tool(
        "log_violation",
        {"task": "diet", "detail": "pizza slice"},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    assert len(bot.polls) == 1
    poll = bot.polls[0]
    assert poll["chat_id"] == -1
    assert poll["options"] == ["pass", "penance", "fail"]
    assert "Bryan" in poll["question"]
    assert "diet" in poll["question"]
    assert "pizza" in poll["question"]


@pytest.mark.asyncio
async def test_log_violation_attaches_poll_metadata(db_with_bryan):
    db = db_with_bryan
    bot = _StubBot(poll_id="abc123", message_id=999)
    await luke_chat._execute_tool(
        "log_violation",
        {"task": "diet", "detail": "wine"},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    rows = await db.get_active_penances(111)
    r = dict(rows[0])
    assert r["poll_id"] == "abc123"
    assert r["poll_message_id"] == 999

    # And we can look up by poll_id
    found = await db.get_penance_by_poll_id("abc123")
    assert found is not None
    assert dict(found)["id"] == r["id"]


@pytest.mark.asyncio
async def test_log_violation_rejects_action_tasks(db_with_bryan):
    db = db_with_bryan
    bot = _StubBot()
    result = await luke_chat._execute_tool(
        "log_violation",
        {"task": "workout_indoor", "detail": "skipped"},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    assert result.startswith("DENIED")
    assert "declare_penance" in result.lower()


@pytest.mark.asyncio
async def test_log_violation_rejects_empty_detail(db_with_bryan):
    db = db_with_bryan
    bot = _StubBot()
    result = await luke_chat._execute_tool(
        "log_violation",
        {"task": "diet", "detail": "  "},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    assert result.startswith("DENIED")
    assert "detail" in result.lower()


@pytest.mark.asyncio
async def test_log_violation_dedupes_open_cases(db_with_bryan):
    db = db_with_bryan
    bot = _StubBot()
    await luke_chat._execute_tool(
        "log_violation",
        {"task": "diet", "detail": "wine"},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    result2 = await luke_chat._execute_tool(
        "log_violation",
        {"task": "diet", "detail": "another"},
        db,
        user_id=111,
        context=_make_context(db, bot),
    )
    assert result2.startswith("DENIED")
    assert "already" in result2.lower()


# ── Arbitration vote DB layer ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_and_tally_votes(db_with_bryan):
    db = db_with_bryan
    pid = await db.add_penance(
        telegram_id=111, missed_day=17, makeup_day=17, task="diet",
        status="arbitration_pending", detail="wine",
    )
    await db.record_arbitration_vote(pid, voter_id=222, choice="pass")
    await db.record_arbitration_vote(pid, voter_id=333, choice="penance")
    await db.record_arbitration_vote(pid, voter_id=444, choice="penance")

    tally = await db.get_arbitration_tally(pid)
    assert tally == {"pass": 1, "penance": 2}


@pytest.mark.asyncio
async def test_vote_update_replaces_previous(db_with_bryan):
    db = db_with_bryan
    pid = await db.add_penance(
        telegram_id=111, missed_day=17, makeup_day=17, task="diet",
        status="arbitration_pending",
    )
    await db.record_arbitration_vote(pid, voter_id=222, choice="pass")
    await db.record_arbitration_vote(pid, voter_id=222, choice="fail")  # changed mind
    tally = await db.get_arbitration_tally(pid)
    assert tally == {"fail": 1}


@pytest.mark.asyncio
async def test_vote_retraction_deletes(db_with_bryan):
    db = db_with_bryan
    pid = await db.add_penance(
        telegram_id=111, missed_day=17, makeup_day=17, task="diet",
        status="arbitration_pending",
    )
    await db.record_arbitration_vote(pid, voter_id=222, choice="pass")
    await db.record_arbitration_vote(pid, voter_id=222, choice="")  # retracted
    tally = await db.get_arbitration_tally(pid)
    assert tally == {}


@pytest.mark.asyncio
async def test_pending_arbitrations_list_excludes_resolved(db_with_bryan):
    db = db_with_bryan
    p_open = await db.add_penance(
        telegram_id=111, missed_day=17, makeup_day=17, task="diet",
        status="arbitration_pending",
    )
    p_done = await db.add_penance(
        telegram_id=111, missed_day=15, makeup_day=15, task="diet",
        status="arbitration_pending",
    )
    await db.resolve_penance(p_done, "failed")

    pending = await db.get_pending_arbitrations()
    pending_ids = [dict(r)["id"] for r in pending]
    assert p_open in pending_ids
    assert p_done not in pending_ids


# ── Admin commands wiring ───────────────────────────────────────────────


def test_admin_arbitrations_handler_registered():
    handlers = admin_module.get_admin_handlers()
    names = []
    for h in handlers:
        for c in (h.commands if hasattr(h, "commands") else []):
            names.append(c)
    assert "admin_arbitrations" in names
    assert "admin_arbitrate" in names


def test_admin_arbitrate_supports_three_verdicts():
    src = inspect.getsource(admin_module._admin_arbitrate_command)
    # Verdict literals
    assert "'pass'" in src or '"pass"' in src
    assert "'penance'" in src or '"penance"' in src
    assert "'fail'" in src or '"fail"' in src
    # Penance verdict requires a task arg
    assert "PENANCE_ABLE_TASKS" in src


def test_arbitration_poll_handler_module_exists():
    """The PollAnswerHandler is wired separately."""
    from bot.handlers import arbitration
    handler = arbitration.get_arbitration_poll_handler()
    assert handler is not None


# ── Poll-answer integration (no real Telegram client) ───────────────────


@pytest.mark.asyncio
async def test_poll_answer_records_vote(db_with_bryan):
    """Simulate a Telegram PollAnswer → record_arbitration_vote called."""
    from bot.handlers import arbitration as arb

    db = db_with_bryan
    pid = await db.add_penance(
        telegram_id=111, missed_day=17, makeup_day=17, task="diet",
        status="arbitration_pending",
    )
    await db.attach_arbitration_poll(pid, poll_id="poll_xyz", poll_message_id=42)

    poll_answer = SimpleNamespace(
        poll_id="poll_xyz",
        user=SimpleNamespace(id=222),
        option_ids=[1],  # index 1 → "penance"
    )
    update = SimpleNamespace(poll_answer=poll_answer)
    context = SimpleNamespace(bot_data={"db": db})

    await arb.handle_poll_answer(update, context)

    tally = await db.get_arbitration_tally(pid)
    assert tally == {"penance": 1}


@pytest.mark.asyncio
async def test_poll_answer_retraction_deletes_vote(db_with_bryan):
    from bot.handlers import arbitration as arb

    db = db_with_bryan
    pid = await db.add_penance(
        telegram_id=111, missed_day=17, makeup_day=17, task="diet",
        status="arbitration_pending",
    )
    await db.attach_arbitration_poll(pid, poll_id="poll_xyz", poll_message_id=42)
    await db.record_arbitration_vote(pid, voter_id=222, choice="pass")

    poll_answer = SimpleNamespace(
        poll_id="poll_xyz",
        user=SimpleNamespace(id=222),
        option_ids=[],  # retraction
    )
    update = SimpleNamespace(poll_answer=poll_answer)
    context = SimpleNamespace(bot_data={"db": db})

    await arb.handle_poll_answer(update, context)

    tally = await db.get_arbitration_tally(pid)
    assert tally == {}


@pytest.mark.asyncio
async def test_poll_answer_for_unknown_poll_is_noop(db_with_bryan):
    from bot.handlers import arbitration as arb

    db = db_with_bryan
    poll_answer = SimpleNamespace(
        poll_id="not_an_arbitration_poll",
        user=SimpleNamespace(id=222),
        option_ids=[0],
    )
    update = SimpleNamespace(poll_answer=poll_answer)
    context = SimpleNamespace(bot_data={"db": db})

    # Must not raise.
    await arb.handle_poll_answer(update, context)
