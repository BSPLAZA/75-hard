"""Render the daily scoreboard card for the group chat."""

from bot.utils.progress import water_bar, is_all_complete, WATER_GOAL

CHALLENGE_DAYS = 75


def render_card(
    day_number: int,
    active_count: int,
    prize_pool: int,
    checkins: list[dict],
) -> str:
    """Build the daily card text.

    Parameters
    ----------
    day_number:   Current day of the challenge (1-75).
    active_count: Number of participants still active.
    prize_pool:   Total prize pool in dollars.
    checkins:     List of dicts, each with "name" plus all checkin fields.

    Returns
    -------
    Formatted multi-line string suitable for Telegram monospace rendering.
    """
    all_complete = bool(checkins) and all(is_all_complete(c) for c in checkins)
    standing_text = "STILL STANDING" if all_complete else "STANDING"

    header = f"DAY {day_number} / {CHALLENGE_DAYS} — {active_count}/{active_count} {standing_text} — ${prize_pool}"

    lines = [header, ""]

    # Find the longest name for alignment
    max_name_len = max((len(c["name"]) for c in checkins), default=0)

    for c in checkins:
        w1 = "✅" if c["workout_1_done"] else ".."
        w2 = "✅" if c["workout_2_done"] else ".."
        bar = water_bar(c["water_cups"])
        cups = f"{c['water_cups']}/{WATER_GOAL}"
        pic = "✅" if c["photo_done"] else ".."
        read = "✅" if c["reading_done"] else ".."
        diet = "✅" if c["diet_done"] else ".."

        star = " ⭐" if is_all_complete(c) else ""
        name = c["name"].ljust(max_name_len)

        line = f"{name}  {w1}  {w2}  {bar}  {cups}  {pic}  {read}  {diet}{star}"
        lines.append(line)

    # Footer legend
    lines.append("")
    legend_pad = " " * max_name_len
    lines.append(f"{legend_pad}  W1  W2     WATER      PIC READ DIET")

    return "\n".join(lines)
