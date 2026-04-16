"""Progress-tracking utility functions for the 75 Hard bot."""

from datetime import date, datetime

import pytz

WATER_GOAL = 16
BAR_LENGTH = 10
ET = pytz.timezone("US/Eastern")


def water_bar(cups: int) -> str:
    """Render a 10-char progress bar for water cups (0-16)."""
    filled = round(cups / WATER_GOAL * BAR_LENGTH)
    filled = max(0, min(BAR_LENGTH, filled))
    return "▓" * filled + "░" * (BAR_LENGTH - filled)


def today_et() -> date:
    """Return today's date in Eastern Time (not UTC)."""
    return datetime.now(ET).date()


def get_day_number(start_date: date, today: date | None = None) -> int:
    """Day 1 on start_date, Day 0 before start. Uses ET if today not provided."""
    if today is None:
        today = today_et()
    delta = (today - start_date).days
    return delta + 1


def is_all_complete(checkin: dict) -> bool:
    """Check if all 6 tasks are complete for a checkin."""
    return bool(
        checkin["workout_1_done"] and checkin["workout_2_done"]
        and checkin["water_cups"] >= WATER_GOAL
        and checkin["diet_done"] and checkin["reading_done"]
        and checkin["photo_done"]
    )


def get_missing_tasks(checkin: dict) -> list[str]:
    """Return list of incomplete task names."""
    missing = []
    if not checkin["workout_1_done"]:
        missing.append("Workout 1")
    if not checkin["workout_2_done"]:
        missing.append("Workout 2")
    if checkin["water_cups"] < WATER_GOAL:
        missing.append(f"Water ({checkin['water_cups']}/{WATER_GOAL})")
    if not checkin["reading_done"]:
        missing.append("Reading")
    if not checkin["photo_done"]:
        missing.append("Progress photo")
    if not checkin["diet_done"]:
        missing.append("Diet")
    return missing
