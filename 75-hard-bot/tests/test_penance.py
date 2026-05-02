"""Tests for the penance state machine + database helpers.

Pure-function tests cover compute_state's six resolution branches without DB.
DB tests cover round-trip of penance_log rows and tone/payment columns.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import pytest
import pytest_asyncio

from bot.database import Database
from bot.penance import (
    BINARY_TASKS,
    PENANCE_ABLE_TASKS,
    TASKS,
    TASK_TARGETS,
    compute_state,
    is_binary,
    is_penance_able,
    is_target_met,
    render_state_label,
    target_for_task,
)


# ── Constants + simple helpers ─────────────────────────────────────────

class TestConstants:
    def test_all_tasks_have_targets(self):
        for t in TASKS:
            assert t in TASK_TARGETS

    def test_penance_able_and_binary_partition_all_tasks(self):
        assert PENANCE_ABLE_TASKS | BINARY_TASKS == set(TASKS)
        assert PENANCE_ABLE_TASKS & BINARY_TASKS == set()

    def test_diet_is_the_only_binary_task(self):
        assert BINARY_TASKS == {"diet"}

    def test_water_target_is_one_gallon(self):
        assert target_for_task("water") == 16

    def test_water_penance_target_is_two_gallons(self):
        assert target_for_task("water", in_penance=True) == 32

    def test_workout_penance_target_is_two(self):
        assert target_for_task("indoor_workout", in_penance=True) == 2

    def test_unknown_task_raises(self):
        with pytest.raises(ValueError):
            target_for_task("hopscotch")

    def test_is_penance_able_partitions_correctly(self):
        assert is_penance_able("water")
        assert is_penance_able("photo")
        assert not is_penance_able("diet")
        assert is_binary("diet")
        assert not is_binary("water")


# ── is_target_met ──────────────────────────────────────────────────────

class TestIsTargetMet:
    def test_water_below_goal(self):
        assert not is_target_met({"water_cups": 8}, "water")

    def test_water_at_goal(self):
        assert is_target_met({"water_cups": 16}, "water")

    def test_water_above_goal(self):
        assert is_target_met({"water_cups": 20}, "water")

    def test_water_in_penance_at_single_target_fails(self):
        # 16 cups is enough for a normal day but not for a penance day (32).
        assert not is_target_met({"water_cups": 16}, "water", in_penance=True)

    def test_water_in_penance_at_double_target_passes(self):
        assert is_target_met({"water_cups": 32}, "water", in_penance=True)

    def test_workout_done_boolean(self):
        assert is_target_met({"workout_1_done": 1}, "indoor_workout")
        assert not is_target_met({"workout_1_done": 0}, "indoor_workout")

    def test_workout_in_penance_needs_two(self):
        assert not is_target_met({"workout_1_done": 1}, "indoor_workout", in_penance=True)
        assert is_target_met({"workout_1_done": 2}, "indoor_workout", in_penance=True)

    def test_none_checkin_not_met(self):
        assert not is_target_met(None, "water")

    def test_none_value_not_met(self):
        assert not is_target_met({"water_cups": None}, "water")

    def test_string_value_handled(self):
        # Should not crash if a column has the wrong type
        assert not is_target_met({"water_cups": "abc"}, "water")


# ── compute_state — six resolution branches ────────────────────────────

class TestComputeState:
    """Each test pins one branch of the resolution order."""

    def test_penance_recovered_dominates_checkin(self):
        # Even if checkin has 0, a recovered penance shows recovered.
        state = compute_state(
            checkin_row={"water_cups": 0},
            penance_row={"status": "recovered"},
            task="water",
            day=5, today=10, cutoff_passed=True,
        )
        assert state == "recovered"

    def test_penance_failed_terminal(self):
        state = compute_state(
            checkin_row=None,
            penance_row={"status": "failed"},
            task="water",
            day=5, today=10, cutoff_passed=True,
        )
        assert state == "failed"

    def test_penance_arbitration_for_diet(self):
        state = compute_state(
            checkin_row={"diet_done": 0},
            penance_row={"status": "arbitration_pending"},
            task="diet",
            day=5, today=10, cutoff_passed=True,
        )
        assert state == "arbitration"

    def test_penance_in_progress(self):
        state = compute_state(
            checkin_row={"water_cups": 12},
            penance_row={"status": "in_progress"},
            task="water",
            day=5, today=6, cutoff_passed=False,
        )
        assert state == "in_penance"

    def test_target_met_no_penance_complete(self):
        state = compute_state(
            checkin_row={"water_cups": 16},
            penance_row=None,
            task="water",
            day=5, today=10, cutoff_passed=True,
        )
        assert state == "complete"

    def test_future_day_active(self):
        state = compute_state(
            checkin_row=None, penance_row=None,
            task="water",
            day=15, today=10, cutoff_passed=False,
        )
        assert state == "active"

    def test_today_pre_cutoff_active(self):
        state = compute_state(
            checkin_row={"water_cups": 4}, penance_row=None,
            task="water",
            day=10, today=10, cutoff_passed=False,
        )
        assert state == "active"

    def test_yesterday_pre_cutoff_unmarked(self):
        # Day 9, today is 10, cutoff not yet hit → still in backfill window.
        state = compute_state(
            checkin_row={"water_cups": 0}, penance_row=None,
            task="water",
            day=9, today=10, cutoff_passed=False,
        )
        assert state == "unmarked"

    def test_post_cutoff_no_penance_failed(self):
        # Day 9, today is 10, cutoff hit, no penance → terminal fail.
        # In production the auto-penance job would have created a row for
        # penance-able tasks. This branch is binary-task or job-not-yet-run.
        state = compute_state(
            checkin_row={"diet_done": 0}, penance_row=None,
            task="diet",
            day=9, today=10, cutoff_passed=True,
        )
        assert state == "failed"

    def test_unknown_task_raises(self):
        with pytest.raises(ValueError):
            compute_state(
                checkin_row=None, penance_row=None,
                task="hopscotch",
                day=1, today=1, cutoff_passed=False,
            )


# ── render labels ──────────────────────────────────────────────────────

class TestRenderStateLabel:
    def test_all_states_have_a_label(self):
        for state in [
            "active", "complete", "unmarked", "in_penance",
            "recovered", "failed", "arbitration",
        ]:
            assert isinstance(render_state_label(state), str)
            assert len(render_state_label(state)) >= 1


# ── Database round-trip ────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_add_penance_round_trip(db):
    pid = await db.add_penance(
        telegram_id=111, missed_day=5, makeup_day=6, task="water",
    )
    row = await db.get_penance(pid)
    r = dict(row)
    assert r["telegram_id"] == 111
    assert r["missed_day"] == 5
    assert r["makeup_day"] == 6
    assert r["task"] == "water"
    assert r["status"] == "in_progress"
    assert r["retroactive"] == 0
    assert r["resolved_at"] is None
    assert r["declared_at"] is not None


@pytest.mark.asyncio
async def test_add_retroactive_penance(db):
    pid = await db.add_penance(
        telegram_id=111, missed_day=3, makeup_day=18, task="reading",
        retroactive=True, detail="caught up during rollout audit",
    )
    row = dict(await db.get_penance(pid))
    assert row["retroactive"] == 1
    assert row["detail"] == "caught up during rollout audit"
    assert row["makeup_day"] == 18


@pytest.mark.asyncio
async def test_get_active_penances_filters_by_status(db):
    p1 = await db.add_penance(111, 5, 6, "water")
    p2 = await db.add_penance(111, 7, 8, "reading")
    p3 = await db.add_penance(111, 9, 10, "indoor_workout")
    await db.resolve_penance(p1, "recovered")
    await db.resolve_penance(p2, "failed")

    active = await db.get_active_penances(111)
    ids = [dict(r)["id"] for r in active]
    assert ids == [p3]


@pytest.mark.asyncio
async def test_get_active_includes_arbitration(db):
    p1 = await db.add_penance(111, 5, 6, "diet", status="arbitration_pending")
    p2 = await db.add_penance(111, 6, 7, "water")
    active = await db.get_active_penances(111)
    ids = sorted(dict(r)["id"] for r in active)
    assert ids == sorted([p1, p2])


@pytest.mark.asyncio
async def test_get_penances_for_makeup_day(db):
    # Bryan in penance for water (makeup today=18) AND reading (makeup today=18)
    p1 = await db.add_penance(111, 17, 18, "water")
    p2 = await db.add_penance(111, 17, 18, "reading")
    # Different user, same day — shouldn't appear
    await db.add_penance(222, 17, 18, "water")
    # Same user, different makeup day — shouldn't appear
    await db.add_penance(111, 16, 17, "indoor_workout")

    rows = await db.get_penances_for_makeup_day(111, 18)
    tasks = sorted(dict(r)["task"] for r in rows)
    assert tasks == ["reading", "water"]


@pytest.mark.asyncio
async def test_resolve_penance_sets_timestamp(db):
    pid = await db.add_penance(111, 5, 6, "water")
    await db.resolve_penance(pid, "recovered")
    row = dict(await db.get_penance(pid))
    assert row["status"] == "recovered"
    assert row["resolved_at"] is not None


# ── tone + payment columns ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_tone_default_cardi(db):
    await db.add_user(111, "Bryan", tier=75)
    assert await db.get_user_tone(111) == "cardi"


@pytest.mark.asyncio
async def test_set_user_tone_round_trip(db):
    await db.add_user(111, "Bryan", tier=75)
    await db.set_user_tone(111, "george lopez")
    assert await db.get_user_tone(111) == "george lopez"


@pytest.mark.asyncio
async def test_reset_tone_to_default(db):
    await db.add_user(111, "Bryan", tier=75)
    await db.set_user_tone(111, "drill sergeant")
    await db.set_user_tone(111, None)
    assert await db.get_user_tone(111) == "cardi"


@pytest.mark.asyncio
async def test_get_tone_for_unknown_user_returns_default(db):
    assert await db.get_user_tone(999) == "cardi"


@pytest.mark.asyncio
async def test_set_payment_confirmed(db):
    await db.add_user(111, "Bryan", tier=75)
    await db.set_payment_confirmed(111, day=18)
    user = dict(await db.get_user(111))
    assert user["payment_confirmed_day"] == 18
