"""Render a per-user compliance grid showing all 75 days × 6 tasks at once.

Colors each cell by the state machine in bot/penance.py. Used by the
get_my_compliance_grid DM tool and the /admin_compliance_grid command.

Layout: 3 row-groups of 25 days each (days 1-25, 26-50, 51-75). Each group
has 6 task rows (indoor / outdoor / water / diet / reading / photo). Compact
enough to read on a phone, wide enough that each cell is tappable-sized in
the eye even if not literally clickable in Telegram.
"""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.penance import TASKS, compute_state, is_target_met

# Colors — match the design v3 HTML palette.
BG = "#0d0f12"
PANEL = "#16191e"
LINE = "#2a2f37"
TEXT_PRIMARY = "#e8eaed"
TEXT_DIM = "#9aa0a6"
TEXT_FAINT = "#5f6571"

STATE_COLORS = {
    "complete":    "#2da77d",  # green
    "unmarked":    "#d4a017",  # amber
    "in_penance":  "#e07a3f",  # orange
    "recovered":   "#3d8bd4",  # blue
    "failed":      "#c04040",  # red
    "arbitration": "#9760c4",  # purple
    "active":      "#4a525e",  # gray (today / future)
}
FUTURE_FILL = "#1f242b"  # dim — for days that haven't happened yet
FUTURE_BORDER = "#2a2f37"

# Task display labels (left-column row labels).
TASK_LABELS: dict[str, str] = {
    "workout_indoor": "indoor",
    "workout_outdoor": "outdoor",
    "water": "water",
    "diet": "diet",
    "reading": "reading",
    "photo": "photo",
}

ASSETS_DIR = Path(__file__).parent.parent / "assets"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Inter-Bold.ttf" if bold else "Inter-Medium.ttf"
    font_path = ASSETS_DIR / name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _index_penances(penance_rows: list[dict]) -> dict[tuple[int, str], dict]:
    """Build a (missed_day, task) → penance_row index for O(1) lookup."""
    return {
        (int(r["missed_day"]), r["task"]): r
        for r in penance_rows
    }


def render_compliance_grid(
    user_name: str,
    today_day: int,
    challenge_days: int,
    checkins_by_day: dict[int, dict],
    penance_rows: list[dict],
    cutoff_passed_through_day: int,
) -> BytesIO:
    """Render the user's compliance grid as a PNG.

    Inputs:
      user_name                   — for the title bar
      today_day                   — current challenge day (1..challenge_days)
      challenge_days              — usually 75
      checkins_by_day             — dict[day_number → daily_checkins row dict]
      penance_rows                — all penance_log rows for this user
      cutoff_passed_through_day   — highest day whose midnight PT cutoff has
                                    already fired. Days <= this are locked.

    Returns a BytesIO PNG buffer ready to send via context.bot.send_photo.
    """
    pen_index = _index_penances(penance_rows)

    # Layout — 2x scale for retina rendering.
    scale = 2
    cell = 22 * scale
    cell_gap = 3 * scale
    label_w = 80 * scale       # left-side task labels
    pad = 32 * scale
    days_per_row = 25
    num_groups = (challenge_days + days_per_row - 1) // days_per_row  # = 3 for 75
    num_tasks = len(TASKS)
    group_h = (num_tasks * (cell + cell_gap)) + 16 * scale  # task rows + day-axis label
    group_gap = 32 * scale
    legend_h = 56 * scale
    title_h = 60 * scale

    width = pad * 2 + label_w + (cell + cell_gap) * days_per_row
    height = (
        title_h
        + num_groups * group_h
        + (num_groups - 1) * group_gap
        + legend_h
        + pad
    )

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(22 * scale, bold=True)
    font_label = _load_font(12 * scale)
    font_legend = _load_font(11 * scale)
    font_axis = _load_font(10 * scale)

    # Title bar
    title = f"{user_name} — compliance · day {today_day} of {challenge_days}"
    draw.text((pad, pad // 2), title, fill=TEXT_PRIMARY, font=font_title)

    # Render each row-group (days 1-25, 26-50, 51-75)
    y = title_h
    for group_idx in range(num_groups):
        group_start = group_idx * days_per_row + 1
        group_end = min(group_start + days_per_row - 1, challenge_days)

        # Day-axis tick row above the cells (shows start day, every 5th, end day)
        axis_y = y
        for d in range(group_start, group_end + 1):
            offset = d - group_start
            cell_x = pad + label_w + offset * (cell + cell_gap)
            if d == group_start or d == group_end or d % 5 == 0:
                tick = str(d)
                tw = draw.textbbox((0, 0), tick, font=font_axis)[2]
                draw.text(
                    (cell_x + (cell - tw) // 2, axis_y),
                    tick, fill=TEXT_FAINT, font=font_axis,
                )

        # Task rows
        for task_idx, task in enumerate(TASKS):
            row_y = y + 16 * scale + task_idx * (cell + cell_gap)
            label = TASK_LABELS[task]
            draw.text(
                (pad, row_y + (cell - 14 * scale) // 2),
                label, fill=TEXT_DIM, font=font_label,
            )
            for d in range(group_start, group_end + 1):
                offset = d - group_start
                cell_x = pad + label_w + offset * (cell + cell_gap)
                _draw_cell(
                    draw,
                    x=cell_x, y=row_y, size=cell,
                    task=task, day=d, today=today_day,
                    cutoff_passed=(d <= cutoff_passed_through_day),
                    checkin_row=checkins_by_day.get(d),
                    penance_row=pen_index.get((d, task)),
                )

        y += group_h + group_gap

    y -= group_gap  # last group doesn't need trailing gap

    # Legend at the bottom
    legend_y = y + 12 * scale
    legend_items = [
        ("complete", STATE_COLORS["complete"]),
        ("unmarked", STATE_COLORS["unmarked"]),
        ("in penance", STATE_COLORS["in_penance"]),
        ("recovered", STATE_COLORS["recovered"]),
        ("failed", STATE_COLORS["failed"]),
        ("arbitration", STATE_COLORS["arbitration"]),
        ("today / future", FUTURE_FILL),
    ]
    swatch = 12 * scale
    legend_x = pad
    for label, color in legend_items:
        # Border for the future swatch (it's dim, hard to see without one)
        if color == FUTURE_FILL:
            draw.rectangle(
                [legend_x, legend_y, legend_x + swatch, legend_y + swatch],
                fill=color, outline=FUTURE_BORDER,
            )
        else:
            draw.rectangle(
                [legend_x, legend_y, legend_x + swatch, legend_y + swatch],
                fill=color,
            )
        text_x = legend_x + swatch + 6 * scale
        draw.text((text_x, legend_y - 1 * scale), label, fill=TEXT_DIM, font=font_legend)
        tw = draw.textbbox((0, 0), label, font=font_legend)[2]
        legend_x = text_x + tw + 14 * scale

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def _draw_cell(
    draw: ImageDraw.ImageDraw,
    *,
    x: int, y: int, size: int,
    task: str, day: int, today: int, cutoff_passed: bool,
    checkin_row: dict | None,
    penance_row: dict | None,
) -> None:
    """Draw one (day, task) cell colored by its computed state.

    Future days (day > today) get a dim placeholder with a thin border so the
    grid's full 75-day footprint is visible from day 1 onward — important for
    the user's mental model of "how much challenge is left."
    """
    if day > today:
        draw.rectangle([x, y, x + size, y + size], fill=FUTURE_FILL, outline=FUTURE_BORDER)
        return

    state = compute_state(
        checkin_row=checkin_row,
        penance_row=penance_row,
        task=task,
        day=day,
        today=today,
        cutoff_passed=cutoff_passed,
    )
    # Refine: 'failed' state from compute_state when no penance row exists is
    # actually the binary-task miss. For penance-able tasks past cutoff with
    # no penance, the auto-penance job should have created a row — if it
    # didn't (e.g. job not run yet), still show as failed; otherwise the cell
    # would lie green.
    fill = STATE_COLORS.get(state, STATE_COLORS["active"])
    draw.rectangle([x, y, x + size, y + size], fill=fill)
