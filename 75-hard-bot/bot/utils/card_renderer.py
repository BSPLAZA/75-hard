"""Render the daily scoreboard card for the group chat."""

from datetime import timedelta

from bot.config import CHALLENGE_START_DATE
from bot.utils.progress import is_all_complete, WATER_GOAL

CHALLENGE_DAYS = 75
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _get_weekday(day_number: int) -> str:
    target_date = CHALLENGE_START_DATE + timedelta(days=day_number - 1)
    return WEEKDAYS[target_date.weekday()]


def order_checkins(checkins: list[dict], day_number: int, prev_checkins: list[dict] | None = None) -> list[dict]:
    """Day 1: alphabetical. Day 2+: previous day's fastest completers first."""
    if day_number <= 1 or not prev_checkins:
        return sorted(checkins, key=lambda c: c["name"].lower())

    prev_completion = {}
    for pc in prev_checkins:
        if is_all_complete(pc) and pc.get("completed_at"):
            prev_completion[pc["name"]] = pc["completed_at"]

    completers = []
    others = []
    for c in checkins:
        if c["name"] in prev_completion:
            completers.append((c, prev_completion[c["name"]]))
        else:
            others.append(c)

    completers.sort(key=lambda x: x[1])
    others.sort(key=lambda c: c["name"].lower())
    return [c for c, _ in completers] + others


def render_card(
    day_number: int,
    active_count: int,
    prize_pool: int,
    checkins: list[dict],
    prev_checkins: list[dict] | None = None,
) -> str:
    """Build the daily card text wrapped in HTML <pre> for monospace rendering."""
    weekday = _get_weekday(day_number)
    header = f"DAY {day_number} / {CHALLENGE_DAYS}  ·  {weekday}"

    ordered = order_checkins(checkins, day_number, prev_checkins)

    max_name = max((len(c["name"]) for c in ordered), default=6)
    max_name = max(max_name, 6)
    pad = " " * max_name

    # Column header sits on top, aligned with data
    #          Name____  W1 W2  __/16  P R D
    legend = f"{pad}  WORK   WATER  P R D"

    lines = [header, "", legend, ""]

    for c in ordered:
        w1 = "+" if c["workout_1_done"] else "·"
        w2 = "+" if c["workout_2_done"] else "·"
        cups = c["water_cups"]
        cups_str = f"{cups:>2}/{WATER_GOAL}"
        pic = "+" if c["photo_done"] else "·"
        read = "+" if c["reading_done"] else "·"
        diet = "+" if c["diet_done"] else "·"

        star = " *" if is_all_complete(c) else ""
        name = c["name"].ljust(max_name)

        line = f"{name}  {w1}  {w2}  {cups_str}  {pic} {read} {diet}{star}"
        lines.append(line)

    card_body = "\n".join(lines)
    return f"<pre>{card_body}</pre>"
