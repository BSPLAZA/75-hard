"""Tests for step 5 — self-fail flow + midnight cutoff sweep.

Three contracts are pinned:
  1. The declare_self_fail tool exists and gates against soft cases.
  2. /admin_settle_failure handler is wired.
  3. midnight_cutoff_job's three sweeps execute correctly against a real
     in-memory DB, with a stubbed bot that captures outbound messages.

The third is the load-bearing one — it tests the actual logic, not just
structure. Uses a stub Bot/Context so we don't need a real Telegram client.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import inspect
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from bot.database import Database
from bot.handlers import admin as admin_module
from bot.utils import luke_chat


# ── Tool + handler registration ────────────────────────────────────────


def _find_tool(name: str) -> dict | None:
    for t in luke_chat.TOOLS:
        if t["name"] == name:
            return t
    return None


def test_declare_self_fail_tool_registered():
    assert _find_tool("declare_self_fail") is not None


def test_declare_self_fail_warns_against_soft_cases():
    """The tool description must explicitly tell Luke this is for unambiguous
    final exit, not soft cases that could be penance'd."""
    desc = _find_tool("declare_self_fail")["description"].lower()
    assert "soft" in desc or "ambiguous" in desc
    assert "declare_penance" in desc  # routes ambiguous cases away


def test_declare_self_fail_no_required_inputs():
    """User can call it with just 'I'm out' — no required inputs."""
    schema = _find_tool("declare_self_fail")["input_schema"]
    assert "required" not in schema or schema.get("required") == []


def test_admin_settle_failure_handler_registered():
    handlers = admin_module.get_admin_handlers()
    names = []
    for h in handlers:
        for c in (h.commands if hasattr(h, "commands") else []):
            names.append(c)
    assert "admin_settle_failure" in names


def test_admin_settle_failure_announces_in_group():
    """Settle handler must post acknowledgement to group chat — accountability
    is the whole point per design Q4."""
    src = inspect.getsource(admin_module._admin_settle_failure_command)
    assert "group_chat_id" in src
    assert "send_message" in src
    # Cardi register hint — uses lowercase plain phrasing
    assert "respect" in src or "stepped out" in src


# ── midnight_cutoff_job sweep — load-bearing test ──────────────────────


class _StubBot:
    """Captures outbound send_message calls for assertion."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, *, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    async def edit_message_text(self, *, chat_id, message_id, text, **kwargs):
        pass

    async def pin_chat_message(self, **kwargs):
        pass


@pytest_asyncio.fixture
async def db_with_users():
    """In-memory DB seeded with two registered users + a daily card row
    so get_current_challenge_day works."""
    database = Database(":memory:")
    await database.init()
    await database.add_user(111, "Bryan", tier=75)
    await database.add_user(222, "Kat", tier=75)
    # Mark them DM-registered so the cutoff DM goes through
    for uid in (111, 222):
        await database._conn.execute(
            "UPDATE users SET dm_registered = 1 WHERE telegram_id = ?", (uid,)
        )
    await database._conn.commit()
    # Seed a daily_cards row matching reality at the midnight-cutoff moment:
    # the most recent card is for the day that JUST ended. The next day's
    # card hasn't posted yet (it'll post 4 hours later at 7am ET). So
    # get_current_challenge_day returns 17, the cutoff job binds that to
    # `yesterday`, and creates new penances with makeup_day=18 (the new day).
    await database.save_card(day_number=17, date="2026-05-01", message_id=1, chat_id=-1)
    yield database
    await database.close()


def _make_context(db, bot):
    """Minimal job-context-like object."""
    return SimpleNamespace(bot_data={"db": db, "group_chat_id": -1}, bot=bot)


@pytest.mark.asyncio
async def test_cutoff_resolves_recovered_penance(db_with_users):
    """Penance with makeup_day=yesterday and water target met → recovered."""
    db = db_with_users
    # Cutoff fires at midnight PT, locking the day whose card was just active.
    # In our fixture that's day 17. Bryan's penance has makeup_day=17 (he was
    # making up missed day 16 by doing 2× on day 17). Sweep evaluates whether
    # day-17's checkin met the doubled target.
    pid = await db.add_penance(111, missed_day=16, makeup_day=17, task="water")
    # Bryan's day-17 checkin shows 32 cups (2x target met)
    await db.create_checkin(111, 17, "2026-05-01")
    await db.set_water(111, 17, 32)

    # Run sweep
    from bot.jobs.scheduler import midnight_cutoff_job
    bot = _StubBot()
    await midnight_cutoff_job(_make_context(db, bot))

    row = dict(await db.get_penance(pid))
    assert row["status"] == "recovered"
    assert row["resolved_at"] is not None


@pytest.mark.asyncio
async def test_cutoff_fails_unrecovered_penance(db_with_users):
    """Penance with makeup_day=yesterday and target NOT met → failed."""
    db = db_with_users
    pid = await db.add_penance(111, missed_day=16, makeup_day=17, task="water")
    await db.create_checkin(111, 17, "2026-05-01")
    await db.set_water(111, 17, 20)  # below 32

    from bot.jobs.scheduler import midnight_cutoff_job
    bot = _StubBot()
    await midnight_cutoff_job(_make_context(db, bot))

    row = dict(await db.get_penance(pid))
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_cutoff_sends_payment_dm_on_penance_fail(db_with_users):
    """When penance fails, the user gets a DM with Venmo/Zelle payment surface.

    Exercises the env-aware payment block in sweep 3.
    """
    db = db_with_users
    # Set the env vars via monkeypatch-like override (config reads at import,
    # so we override the module attribute directly for this test).
    import bot.config as cfg
    orig_v, orig_z = cfg.PRIZE_POOL_VENMO_USERNAME, cfg.PRIZE_POOL_ZELLE_PHONE
    cfg.PRIZE_POOL_VENMO_USERNAME = "Bryanedit"
    cfg.PRIZE_POOL_ZELLE_PHONE = "9739329336"
    try:
        await db.add_penance(111, missed_day=16, makeup_day=17, task="water")
        await db.create_checkin(111, 17, "2026-05-01")
        # water_cups = 0 → fail
        from bot.jobs.scheduler import midnight_cutoff_job
        bot = _StubBot()
        await midnight_cutoff_job(_make_context(db, bot))

        # Look for the self-fail DM among the bot's outbound messages
        dms_to_bryan = [m for m in bot.sent if m["chat_id"] == 111]
        assert any("venmo" in m["text"].lower() for m in dms_to_bryan), (
            f"expected Venmo line in self-fail DM. Got: {[m['text'] for m in dms_to_bryan]}"
        )
        assert any("Bryanedit" in m["text"] for m in dms_to_bryan)
        assert any("9739329336" in m["text"] for m in dms_to_bryan)
    finally:
        cfg.PRIZE_POOL_VENMO_USERNAME = orig_v
        cfg.PRIZE_POOL_ZELLE_PHONE = orig_z


@pytest.mark.asyncio
async def test_cutoff_does_NOT_auto_create_penance_for_incomplete_tasks(db_with_users):
    """v55: midnight cutoff no longer auto-creates penance rows for incomplete
    tasks. Bryan's design — incomplete at midnight could be 'didn't do' OR
    'forgot to log', and the morning nudge at 9am ET disambiguates per task.
    Pre-empting that with auto-create treats both cases identically."""
    db = db_with_users
    # Bryan: missed several tasks. Kat: missed nothing.
    await db.create_checkin(111, 17, "2026-05-01")  # all 0 by default
    await db.create_checkin(222, 17, "2026-05-01")
    await db._conn.execute(
        """UPDATE daily_checkins
           SET workout_1_done=1, workout_2_done=1, water_cups=16,
               diet_done=1, reading_done=1, photo_done=1
           WHERE telegram_id=? AND day_number=?""",
        (222, 17),
    )
    await db._conn.commit()

    from bot.jobs.scheduler import midnight_cutoff_job
    bot = _StubBot()
    await midnight_cutoff_job(_make_context(db, bot))

    # Bryan should have NO new penance rows from the cutoff. Morning nudge
    # will ask him per task at 9am ET.
    bryan_pens = await db.get_penances_for_missed_day(111, 17)
    assert len(bryan_pens) == 0, (
        f"cutoff must not auto-create penance — got {[dict(r) for r in bryan_pens]}"
    )
    kat_pens = await db.get_penances_for_missed_day(222, 17)
    assert len(kat_pens) == 0


@pytest.mark.asyncio
async def test_cutoff_does_not_auto_create_for_user_with_no_checkin_row(db_with_users):
    """A user who never logged anything yesterday must NOT get auto-penance.
    The morning nudge handles the 'did/missed?' disambiguation."""
    db = db_with_users
    # Bryan has NO daily_checkins row for day 17.
    from bot.jobs.scheduler import midnight_cutoff_job
    bot = _StubBot()
    await midnight_cutoff_job(_make_context(db, bot))

    bryan_pens = await db.get_penances_for_missed_day(111, 17)
    assert len(bryan_pens) == 0


@pytest.mark.asyncio
async def test_cutoff_admin_warning_mentions_morning_nudge(db_with_users):
    """Admin DM at midnight should point at the 9am ET morning nudge as the
    disambiguation path, NOT claim penance was already auto-created."""
    db = db_with_users
    await db.create_checkin(111, 17, "2026-05-01")  # incomplete
    from bot.jobs.scheduler import midnight_cutoff_job
    bot = _StubBot()
    await midnight_cutoff_job(_make_context(db, bot))
    # Admin should have received a warning — find it
    admin_msgs = [m for m in bot.sent if m["chat_id"] == 999]
    assert admin_msgs, "expected admin warning DM"
    text = admin_msgs[0]["text"].lower()
    # Old phrasing must be gone
    assert "auto-created" not in text
    # New phrasing pointing to morning nudge
    assert "morning" in text or "9am" in text or "nudge" in text


@pytest.mark.asyncio
async def test_cutoff_does_not_double_create_existing_penance(db_with_users):
    """If user already declared penance via the morning nudge, the sweep
    must not create a duplicate."""
    db = db_with_users
    await db.create_checkin(111, 17, "2026-05-01")  # all 0
    # Bryan declared water penance via morning nudge yesterday already
    pid = await db.add_penance(111, missed_day=17, makeup_day=18, task="water")

    from bot.jobs.scheduler import midnight_cutoff_job
    bot = _StubBot()
    await midnight_cutoff_job(_make_context(db, bot))

    # No second water penance for day 17
    pens = await db.get_penances_for_missed_day(111, 17)
    water_rows = [r for r in pens if dict(r)["task"] == "water"]
    assert len(water_rows) == 1
    assert dict(water_rows[0])["id"] == pid
