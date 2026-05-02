"""Penance state machine + task constants for the 75 Hard accountability bot.

Pure functions for computing per-(user, day, task) state from a daily_checkins
row + the matching penance_log rows. No side effects, no DB access — testable
in isolation.

State machine (from design doc v3, /tmp/luke-penance-grid-design.html):

  active ──done──→ complete ✓
     │
     │ day N ends
     ▼
  unmarked ──"I missed it" or silent past midnight PT N+1──→ in_penance | failed
                                                          │
  arbitration ←── "I had a glass of wine" (binary task)   ▼
                                                       recovered ✓ / failed

PENANCE_ABLE_TASKS can recover via 2× makeup the next day.
BINARY_TASKS (just 'diet' currently) cannot — they fail or go to arbitration.
"""

from typing import Literal

# Canonical task names. "workout_indoor" / "workout_outdoor" replace the
# schema's workout_1 / workout_2 in user-facing contexts (per group feedback —
# users don't think of them by number, they think of them by location).
TASKS: list[str] = [
    "workout_indoor",
    "workout_outdoor",
    "water",
    "diet",
    "reading",
    "photo",
]

# Action-quantity tasks: missing one allows penance (do 2× makeup_day to recover).
PENANCE_ABLE_TASKS: frozenset[str] = frozenset({
    "workout_indoor",
    "workout_outdoor",
    "water",
    "reading",
    "photo",
})

# Binary tasks: no penance possible. Miss = fail (subject to arbitration for
# soft cases like Skeptic's wine glass). Currently just diet (alcohol + cheat).
BINARY_TASKS: frozenset[str] = frozenset({"diet"})

# Maps each task to (daily_checkins column, base target). Booleans use 1.
# water uses 16 cups (1 gallon). All targets are doubled when in penance.
TASK_TARGETS: dict[str, tuple[str, int]] = {
    "workout_indoor": ("workout_1_done", 1),
    "workout_outdoor": ("workout_2_done", 1),
    "water": ("water_cups", 16),
    "diet": ("diet_done", 1),
    "reading": ("reading_done", 1),
    "photo": ("photo_done", 1),
}

TaskState = Literal[
    "active",
    "complete",
    "unmarked",
    "in_penance",
    "recovered",
    "failed",
    "arbitration",
]


def is_penance_able(task: str) -> bool:
    """True if a missed task can be made up via 2× makeup penance.

    diet is the only non-penance-able task: cheat / alcohol can't be undone
    by doubling tomorrow."""
    return task in PENANCE_ABLE_TASKS


def is_binary(task: str) -> bool:
    return task in BINARY_TASKS


def target_for_task(task: str, *, in_penance: bool = False) -> int:
    """Return the value-or-boolean target for a task. Doubled if in penance.

    in_penance=True is used on the makeup_day to compute the doubled target
    (e.g., water 16 → 32 cups, workouts 1 → 2)."""
    if task not in TASK_TARGETS:
        raise ValueError(f"unknown task: {task}")
    _, base = TASK_TARGETS[task]
    return base * 2 if in_penance else base


def is_target_met(checkin_row: dict | None, task: str, *, in_penance: bool = False) -> bool:
    """Did this user hit the target on the given day?

    For booleans (workouts, reading, etc.), penance doubling means the user
    must have a value of >= 2 on the makeup day. The schema's *_done columns
    are integers, so log_workout_dm needs to bump them to 2 on penance days.
    """
    if checkin_row is None:
        return False
    if task not in TASK_TARGETS:
        raise ValueError(f"unknown task: {task}")
    column, _ = TASK_TARGETS[task]
    target = target_for_task(task, in_penance=in_penance)
    value = checkin_row.get(column)
    if value is None:
        return False
    try:
        return int(value) >= target
    except (TypeError, ValueError):
        return False


def compute_state(
    checkin_row: dict | None,
    penance_row: dict | None,
    task: str,
    *,
    day: int,
    today: int,
    cutoff_passed: bool,
) -> TaskState:
    """Pure function: compute the state of one (user, day, task) cell.

    Resolution order (first match wins):
      1. penance_row dictates state if present (recovered / failed / arbitration / in_penance)
      2. target met on day → 'complete'
      3. day in the future → 'active'
      4. day is today and cutoff hasn't hit → 'active'
      5. day is past but inside backfill window → 'unmarked'
      6. day is past, cutoff hit, no penance → 'failed' (caller's auto-penance job
         should have created a penance row already if penance-able; if not,
         this is the terminal fail state)

    Args:
      checkin_row    — daily_checkins row dict for (user, day), or None.
      penance_row    — penance_log row for (user, day, task), or None.
      task           — task identifier, must be in TASKS.
      day            — the challenge day this cell represents.
      today          — current challenge day.
      cutoff_passed  — has midnight PT of (day+1) passed?
    """
    if task not in TASK_TARGETS:
        raise ValueError(f"unknown task: {task}")

    # Step 1: penance row drives state when present.
    if penance_row is not None:
        status = penance_row.get("status")
        if status == "recovered":
            return "recovered"
        if status == "failed":
            return "failed"
        if status == "arbitration_pending":
            return "arbitration"
        if status == "in_progress":
            return "in_penance"

    # Step 2: target met → complete (regardless of cutoff posture or day).
    if is_target_met(checkin_row, task):
        return "complete"

    # Step 3 & 4: future / today / pre-cutoff → still active.
    if day > today:
        return "active"
    if day == today and not cutoff_passed:
        return "active"

    # Step 5: past day, still within backfill window → unmarked.
    if not cutoff_passed:
        return "unmarked"

    # Step 6: past cutoff, no penance, no completion → failed.
    # (Auto-penance job should have created a penance row for penance-able
    # tasks; if it didn't run yet, this is a transient state.)
    return "failed"


def render_state_label(state: TaskState) -> str:
    """Short single-character label for grid rendering."""
    return {
        "active": "·",
        "complete": "✓",
        "unmarked": "?",
        "in_penance": "P",
        "recovered": "R",
        "failed": "X",
        "arbitration": "A",
    }[state]
