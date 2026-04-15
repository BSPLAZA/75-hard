"""Easter egg messages and surprise notifications for the 75 Hard bot."""

import html
from datetime import datetime, timezone, timedelta

from telegram.ext import ContextTypes

# ── Day Milestones ──────────────────────────────────────────────────────

MILESTONES = {
    1: "75 days. It starts now. No one knows who they'll be on Day 75.",
    7: "One week down. Most people quit in the first week. You didn't.",
    14: "Two weeks. You're building something real.",
    21: "Three weeks. They say it takes 21 days to build a habit.",
    30: "One month. You're in the top 40% of people who attempt 75 Hard.",
    50: "50 days. Most people can't do 5.",
    60: "60 days. The finish line is closer than the start.",
    69: "Nice.",
    75: "75 days. You did it. Every single one of you. \U0001f3c6",
}


def get_milestone_message(day: int) -> str | None:
    """Return a milestone message for the given day, or None."""
    return MILESTONES.get(day)


async def post_milestone_if_needed(
    context: ContextTypes.DEFAULT_TYPE, day: int
) -> None:
    """Post a milestone message to the group chat if the day qualifies."""
    msg = get_milestone_message(day)
    if not msg:
        return

    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    try:
        await context.bot.send_message(chat_id=chat_id, text=msg)
    except Exception:
        pass


# ── Early Bird (First to Complete) ──────────────────────────────────────

async def check_first_completion(
    context: ContextTypes.DEFAULT_TYPE,
    user_name: str,
    day_number: int,
) -> None:
    """If this user is the first to complete all tasks today, announce it."""
    db = context.bot_data["db"]
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    checkins = await db.get_all_checkins_for_day(day_number)
    completed = [c for c in checkins if c.get("completed_at")]

    # Only announce if exactly one person has completed (the one who just did)
    if len(completed) != 1:
        return

    safe_name = html.escape(user_name)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\U0001f3c6 {safe_name} finished first today!",
        )
    except Exception:
        pass


# ── Simultaneous Workouts ──────────────────────────────────────────────

# In-memory tracker: maps (user_id, day_number) -> timestamp of last workout log
_recent_workouts: dict[tuple[int, int], datetime] = {}

# Track which pairs have already been announced today to avoid spam.
_announced_pairs: dict[frozenset, int] = {}

SIMULTANEOUS_WINDOW = timedelta(minutes=15)


def _cleanup_old_workouts(day_number: int) -> None:
    """Remove workout records from previous days."""
    global _recent_workouts, _announced_pairs
    _recent_workouts = {
        k: v for k, v in _recent_workouts.items() if k[1] == day_number
    }
    _announced_pairs = {
        pair: d for pair, d in _announced_pairs.items() if d == day_number
    }


def record_workout_time(user_id: int, day_number: int) -> None:
    """Record the current time as when this user logged a workout."""
    _cleanup_old_workouts(day_number)
    _recent_workouts[(user_id, day_number)] = datetime.now(timezone.utc)


async def check_simultaneous_workout(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    user_name: str,
    day_number: int,
) -> None:
    """Check if another user logged a workout in the last 15 minutes and announce."""
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    db = context.bot_data["db"]
    now = datetime.now(timezone.utc)
    cutoff = now - SIMULTANEOUS_WINDOW

    for (other_id, other_day), timestamp in _recent_workouts.items():
        if other_day != day_number:
            continue
        if other_id == user_id:
            continue
        if timestamp < cutoff:
            continue

        # Check if this pair was already announced today
        pair_key = frozenset((user_id, other_id))
        if _announced_pairs.get(pair_key) == day_number:
            continue

        _announced_pairs[pair_key] = day_number

        # Look up the other user's name
        other_user = await db.get_user(other_id)
        other_name = other_user["name"] if other_user else "Someone"

        safe_name1 = html.escape(user_name)
        safe_name2 = html.escape(other_name)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"\U0001f525 {safe_name1} and {safe_name2} both getting after it right now!",
            )
        except Exception:
            pass

        # Only announce one pair per workout log to avoid spam
        return
