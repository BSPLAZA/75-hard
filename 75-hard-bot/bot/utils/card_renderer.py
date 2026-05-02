"""Render the daily scoreboard card for the group chat."""

from datetime import timedelta

from bot.config import CHALLENGE_START_DATE
from bot.utils.progress import is_all_complete, WATER_GOAL

CHALLENGE_DAYS = 75
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Map penance task names → daily_card column markers we render.
# 'water' is special-cased (counter). The rest are boolean cells.
_PENANCE_TASK_TO_CELL = {
    "workout_indoor": "workout_1",
    "workout_outdoor": "workout_2",
    "water": "water",
    "reading": "reading",
    "photo": "photo",
}


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


def _penance_cells_for(penance_rows: list[dict] | None) -> set[str]:
    """Return the set of card cells the user is in active penance for.

    Cells: 'workout_1', 'workout_2', 'water', 'reading', 'photo'. Empty set
    means no active penance — render normally.
    """
    if not penance_rows:
        return set()
    cells: set[str] = set()
    for r in penance_rows:
        if r.get("status") != "in_progress":
            continue
        cell = _PENANCE_TASK_TO_CELL.get(r.get("task"))
        if cell:
            cells.add(cell)
    return cells


def _format_penance_footer(
    ordered_checkins: list[dict],
    penances_by_user: dict[int, list[dict]] | None,
) -> str | None:
    """One-line footer summarizing who's in penance for what.

    Output shape: 'penance today: bryan 2× water · kat 2× indoor + reading'
    Returns None if nobody has an active penance (omit the footer entirely).
    """
    if not penances_by_user:
        return None
    parts: list[str] = []
    for c in ordered_checkins:
        rows = penances_by_user.get(c["telegram_id"]) or []
        active = [r for r in rows if r.get("status") == "in_progress"]
        if not active:
            continue
        labels = []
        for r in active:
            t = r.get("task", "")
            if t == "workout_indoor":
                labels.append("indoor")
            elif t == "workout_outdoor":
                labels.append("outdoor")
            elif t in ("water", "reading", "photo"):
                labels.append(t)
        if labels:
            parts.append(f"{c['name'].lower()} 2× {' + '.join(labels)}")
    if not parts:
        return None
    return "penance today: " + " · ".join(parts)


def render_card(
    day_number: int,
    active_count: int,
    prize_pool: int,
    checkins: list[dict],
    prev_checkins: list[dict] | None = None,
    penances_by_user: dict[int, list[dict]] | None = None,
) -> str:
    """Build the daily card text wrapped in HTML <pre> for monospace rendering.

    penances_by_user maps telegram_id → list of penance_log rows where
    makeup_day == day_number (today's makeup targets). When a user has an
    active penance for a task, the per-cell render shifts:
      - water: divisor doubles (16 → 32) so '12/32' shows the 2× target
      - booleans: a `p` marker in place of `·` flags pending makeup
    A footer line names who owes 2× of what so the visible state matches
    the stored penance state without anyone having to look it up.
    """
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
        pen_cells = _penance_cells_for(
            (penances_by_user or {}).get(c["telegram_id"])
        )

        # Booleans: 'p' if in penance and not yet done, else '+' done / '·' not done.
        def cell(value: int, key: str) -> str:
            if value:
                return "+"
            return "p" if key in pen_cells else "·"

        w1 = cell(c["workout_1_done"], "workout_1")
        w2 = cell(c["workout_2_done"], "workout_2")
        cups = c["water_cups"]
        water_target = WATER_GOAL * 2 if "water" in pen_cells else WATER_GOAL
        cups_str = f"{cups:>2}/{water_target}"
        pic = cell(c["photo_done"], "photo")
        read = cell(c["reading_done"], "reading")
        diet = "+" if c["diet_done"] else "·"  # diet is binary, no penance

        star = " *" if is_all_complete(c) else ""
        name = c["name"].ljust(max_name)

        line = f"{name}  {w1}  {w2}  {cups_str}  {pic} {read} {diet}{star}"
        lines.append(line)

    footer = _format_penance_footer(ordered, penances_by_user)
    if footer:
        lines.append("")
        lines.append(footer)

    card_body = "\n".join(lines)
    return f"<pre>{card_body}</pre>"
