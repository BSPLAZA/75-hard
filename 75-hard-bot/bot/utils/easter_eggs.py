"""Easter egg messages and surprise notifications for the 75 Hard bot."""

import html
import logging
from datetime import datetime, timezone, timedelta

from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _is_all_complete(c: dict) -> bool:
    """Local copy to avoid circular imports with utils.progress."""
    return bool(
        c.get("workout_1_done")
        and c.get("workout_2_done")
        and (c.get("water_cups") or 0) >= 16
        and c.get("diet_done")
        and c.get("reading_done")
        and c.get("photo_done")
    )

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


# ── Streak Milestones ───────────────────────────────────────────────────

STREAK_MILESTONES = {
    7: "🔥 {name} just sealed a 7-day perfect streak. one whole week, no slip-ups.",
    14: "🔥🔥 {name} on a 14-day streak. two weeks of doing all 6, every single day.",
    21: "🔥🔥🔥 {name} hit 21 days perfect. that's the habit-formation threshold.",
    30: "💎 {name} just locked in a 30-day perfect streak. unreal.",
    50: "💎💎 {name} on a 50-day streak. half the people who started 75 hard would have quit by now.",
    75: "👑 {name} — 75 perfect days. the whole damn challenge. legendary.",
}


async def check_streak_milestone(
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user_id: int,
    user_name: str,
    day_number: int,
) -> None:
    """If this user just hit a streak milestone (7/14/21/30/50/75), announce it.

    A "streak" = consecutive days, ending today, where the user completed all 6 tasks.
    """
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    # Walk backwards from today, counting consecutive complete days
    streak = 0
    for d in range(day_number, 0, -1):
        c = await db.get_checkin(user_id, d)
        if not c:
            break
        if not _is_all_complete(dict(c)):
            break
        streak += 1

    msg_template = STREAK_MILESTONES.get(streak)
    if not msg_template:
        return

    safe_name = html.escape(user_name)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg_template.format(name=safe_name),
        )
    except Exception:
        pass


# ── Comeback ────────────────────────────────────────────────────────────

async def check_comeback(
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user_id: int,
    user_name: str,
    day_number: int,
) -> None:
    """If user completed today AND failed to complete yesterday → comeback shoutout."""
    if day_number <= 1:
        return

    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    yesterday = await db.get_checkin(user_id, day_number - 1)
    if not yesterday or _is_all_complete(dict(yesterday)):
        return  # no yesterday data, or yesterday was already complete (no comeback)

    safe_name = html.escape(user_name)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"💪 {safe_name} bounced back today after missing some of yesterday's tasks. that's the energy.",
        )
    except Exception:
        pass


# ── Squad Complete ──────────────────────────────────────────────────────

# Track which days we've already announced squad-complete to avoid double-firing
_squad_announced_days: set[int] = set()


async def check_squad_complete(
    context: ContextTypes.DEFAULT_TYPE,
    db,
    day_number: int,
) -> None:
    """If every active user has completed today, announce it (once per day)."""
    if day_number in _squad_announced_days:
        return

    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    active_users = await db.get_active_users()
    checkins = await db.get_all_checkins_for_day(day_number)
    if not checkins or not active_users:
        return

    completed = [c for c in checkins if _is_all_complete(dict(c))]
    if len(completed) < len(active_users):
        return

    # Mark before sending so we don't re-fire on a race
    _squad_announced_days.add(day_number)

    # Format the time of the last completion in a friendly way
    completers = sorted(
        [dict(c) for c in checkins if dict(c).get("completed_at")],
        key=lambda c: c["completed_at"] or "",
    )
    last_finish_iso = completers[-1].get("completed_at") if completers else None
    time_str = ""
    if last_finish_iso:
        try:
            import pytz as _pytz
            ET = _pytz.timezone("US/Eastern")
            ts = datetime.fromisoformat(last_finish_iso.replace("Z", "+00:00"))
            ts_et = ts.astimezone(ET)
            time_str = f" — locked in by {ts_et.strftime('%-I:%M %p')} ET"
        except Exception:
            time_str = ""

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎯 SQUAD COMPLETE — Day {day_number}{time_str}. all 6 tasks, all 4 of you. let's fucking go.",
        )
    except Exception:
        pass


# ── One-stop entry point ────────────────────────────────────────────────

async def fire_completion_easter_eggs(
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user_id: int,
    user_name: str,
    day_number: int,
) -> None:
    """Call from anywhere a user just completed all 6 tasks (just_completed=True).

    Bundles squad-complete (highest priority), first-finisher, streak, and comeback.
    Squad-complete suppresses first-finisher (they don't co-occur cleanly).
    """
    # Squad complete checks if EVERYONE is done — fire first, since it's the headline
    active_users = await db.get_active_users()
    checkins = await db.get_all_checkins_for_day(day_number)
    completed = [c for c in checkins if _is_all_complete(dict(c))]
    is_squad_complete = active_users and len(completed) >= len(active_users)

    if is_squad_complete:
        await check_squad_complete(context, db, day_number)
    else:
        # First-finisher only fires when count == 1
        await check_first_completion(context, user_name, day_number)

    # These can stack on top either way
    await check_streak_milestone(context, db, user_id, user_name, day_number)
    await check_comeback(context, db, user_id, user_name, day_number)
