"""Generate scoreboard images for the daily recap using Pillow."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.utils.progress import is_all_complete, WATER_GOAL

# Colors
BG = "#0d1117"
SURFACE = "#161b22"
BORDER = "#30363d"
TEXT_PRIMARY = "#e6edf3"
TEXT_DIM = "#7d8590"
GREEN = "#3fb950"
GREEN_DIM = "#238636"
RED_DIM = "#da3633"
BLUE = "#58a6ff"
ORANGE = "#d29922"
ACCENT = "#3fb950"

# Font paths — check local system first, fall back to bundled
ASSETS_DIR = Path(__file__).parent.parent / "assets"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Inter-Bold.ttf" if bold else "Inter-Medium.ttf"
    font_path = ASSETS_DIR / name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    # Fallback to system fonts
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_recap_image(
    day_number: int,
    checkins: list[dict],
    challenge_days: int = 75,
) -> BytesIO:
    """Generate a recap scoreboard image. Returns a BytesIO PNG buffer."""
    # Layout — 2x resolution for crisp rendering on phones
    scale = 2
    padding = 40 * scale
    row_height = 56 * scale
    header_height = 100 * scale
    footer_height = 70 * scale
    dot_size = 16 * scale
    dot_gap = 12 * scale

    num_users = len(checkins)
    width = 700 * scale
    height = header_height + (num_users * row_height) + footer_height + padding

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(28 * scale, bold=True)
    font_name = _load_font(20 * scale)
    font_label = _load_font(13 * scale)
    font_footer = _load_font(16 * scale)
    font_water = _load_font(14 * scale)

    # Header
    from datetime import timedelta
    from bot.config import CHALLENGE_START_DATE
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    target_date = CHALLENGE_START_DATE + timedelta(days=day_number - 1)
    weekday = weekdays[target_date.weekday()]

    title = f"DAY {day_number} RECAP  ·  {weekday}"
    draw.text((padding, padding), title, fill=TEXT_PRIMARY, font=font_title)

    # Column labels
    labels_y = header_height - 24 * scale
    col_start = 260 * scale
    labels = ["W1", "W2", "WATER", "PIC", "READ", "DIET"]
    label_x_positions = [
        col_start,
        col_start + 40 * scale,
        col_start + 90 * scale,
        col_start + 190 * scale,
        col_start + 230 * scale,
        col_start + 270 * scale,
    ]
    for i, label in enumerate(labels):
        draw.text((label_x_positions[i], labels_y), label, fill=TEXT_DIM, font=font_label)

    # Separator line
    draw.line([(padding, header_height - 4), (width - padding, header_height - 4)], fill=BORDER, width=1)

    # Sort: completed first (by name), then incomplete (by name)
    completed = sorted([c for c in checkins if is_all_complete(c)], key=lambda x: x["name"].lower())
    incomplete = sorted([c for c in checkins if not is_all_complete(c)], key=lambda x: x["name"].lower())
    ordered = completed + incomplete

    # Rows
    for idx, c in enumerate(ordered):
        y = header_height + (idx * row_height) + 16
        name = c["name"]
        done = is_all_complete(c)

        # Name
        name_color = GREEN if done else TEXT_PRIMARY
        draw.text((padding, y), name, fill=name_color, font=font_name)

        # Status dots
        tasks = [
            c["workout_1_done"],
            c["workout_2_done"],
            None,  # water placeholder
            c["photo_done"],
            c["reading_done"],
            c["diet_done"],
        ]

        for i, task in enumerate(tasks):
            cx = label_x_positions[i] + 8
            cy = y + 6

            if i == 2:
                # Water — show as fraction text
                cups = c["water_cups"]
                water_color = GREEN if cups >= WATER_GOAL else TEXT_DIM
                draw.text((label_x_positions[i], y + 2), f"{cups}/{WATER_GOAL}", fill=water_color, font=font_water)
                continue

            if task:
                draw.ellipse(
                    [cx - dot_size // 2, cy - dot_size // 2, cx + dot_size // 2, cy + dot_size // 2],
                    fill=GREEN,
                )
                # Checkmark inside
                draw.text((cx - 5 * scale, cy - 7 * scale), "✓", fill=BG, font=_load_font(12 * scale, bold=True))
            else:
                draw.ellipse(
                    [cx - dot_size // 2, cy - dot_size // 2, cx + dot_size // 2, cy + dot_size // 2],
                    outline=BORDER,
                    width=2,
                )

        # Completion star
        if done:
            draw.text((width - padding - 30 * scale, y), "★", fill=ORANGE, font=_load_font(22 * scale))

    # Separator
    footer_y = header_height + (num_users * row_height) + 8
    draw.line([(padding, footer_y), (width - padding, footer_y)], fill=BORDER, width=1)

    # Footer
    num_complete = len(completed)
    remaining = challenge_days - day_number
    footer_text = f"{num_complete}/{num_users} completed  ·  {remaining} days to go"
    draw.text((padding, footer_y + 16), footer_text, fill=TEXT_DIM, font=font_footer)

    # Output
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def render_weekly_digest_image(
    week_number: int,
    user_stats: list[dict],
    total_workouts: int,
    total_water: int,
    total_reading_days: int,
    first_finisher_name: str | None,
    first_finisher_count: int,
) -> BytesIO:
    """Generate a weekly digest scoreboard image. Returns a BytesIO PNG buffer.

    user_stats is a list of dicts with keys:
        name, days_complete, total_days, consistency (list of bool for each day)
    """
    scale = 2
    padding = 40 * scale
    row_height = 64 * scale
    header_height = 110 * scale
    stats_height = 140 * scale
    user_section_header = 40 * scale
    footer_height = 60 * scale
    dot_size = 18 * scale
    dot_gap = 8 * scale

    num_users = len(user_stats)
    width = 700 * scale
    height = (
        header_height
        + stats_height
        + user_section_header
        + (num_users * row_height)
        + footer_height
        + padding
    )

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(26 * scale, bold=True)
    font_subtitle = _load_font(16 * scale)
    font_stat_value = _load_font(28 * scale, bold=True)
    font_stat_label = _load_font(12 * scale)
    font_name = _load_font(18 * scale)
    font_score = _load_font(16 * scale, bold=True)
    font_section = _load_font(14 * scale, bold=True)
    font_footer = _load_font(14 * scale)

    # ── Header ────────────────────────────────────────────────────────
    y = padding
    draw.text((padding, y), f"WEEK {week_number} DIGEST", fill=TEXT_PRIMARY, font=font_title)
    y += 38 * scale
    draw.text((padding, y), "Sunday Recap", fill=TEXT_DIM, font=font_subtitle)
    y += 30 * scale
    draw.line([(padding, y), (width - padding, y)], fill=BORDER, width=2)

    # ── Aggregate stats (2x2 grid) ────────────────────────────────────
    y += 20 * scale
    col_w = (width - 2 * padding) // 2
    stats = [
        (str(total_workouts), "WORKOUTS"),
        (f"{total_water}", "CUPS OF WATER"),
        (f"{total_reading_days * 10}+", "PAGES READ"),
        (
            f"{first_finisher_name} ({first_finisher_count}x)" if first_finisher_name else "---",
            "FIRST TO FINISH",
        ),
    ]
    for i, (value, label) in enumerate(stats):
        col = i % 2
        row = i // 2
        sx = padding + col * col_w
        sy = y + row * 60 * scale

        draw.text((sx, sy), value, fill=GREEN, font=font_stat_value)
        draw.text((sx, sy + 34 * scale), label, fill=TEXT_DIM, font=font_stat_label)

    y += 120 * scale
    draw.line([(padding, y), (width - padding, y)], fill=BORDER, width=2)

    # ── Per-user consistency ──────────────────────────────────────────
    y += 16 * scale
    draw.text((padding, y), "CONSISTENCY", fill=TEXT_DIM, font=font_section)

    # Day labels (M T W T F S S or Day numbers)
    day_labels = ["M", "T", "W", "T", "F", "S", "S"]
    dots_start_x = 300 * scale
    for i, label in enumerate(day_labels):
        lx = dots_start_x + i * (dot_size + dot_gap)
        draw.text((lx, y), label, fill=TEXT_DIM, font=_load_font(10 * scale))

    y += user_section_header

    # Sort by completion rate descending
    sorted_users = sorted(user_stats, key=lambda u: u["days_complete"], reverse=True)

    for idx, u in enumerate(sorted_users):
        uy = y + idx * row_height
        name = u["name"]
        days_done = u["days_complete"]
        total_days = u["total_days"]

        # Name
        name_color = GREEN if days_done == total_days else TEXT_PRIMARY
        draw.text((padding, uy + 8 * scale), name, fill=name_color, font=font_name)

        # Score
        score_text = f"{days_done}/{total_days}"
        draw.text((200 * scale, uy + 8 * scale), score_text, fill=TEXT_DIM, font=font_score)

        # Consistency dots
        consistency = u.get("consistency", [])
        for i, completed in enumerate(consistency):
            cx = dots_start_x + i * (dot_size + dot_gap) + dot_size // 2
            cy = uy + 16 * scale
            if completed:
                draw.ellipse(
                    [cx - dot_size // 2, cy - dot_size // 2, cx + dot_size // 2, cy + dot_size // 2],
                    fill=GREEN,
                )
            else:
                draw.ellipse(
                    [cx - dot_size // 2, cy - dot_size // 2, cx + dot_size // 2, cy + dot_size // 2],
                    outline=RED_DIM,
                    width=2,
                )

        # Perfect week star
        if days_done == total_days and total_days > 0:
            draw.text(
                (width - padding - 30 * scale, uy + 4 * scale),
                "★",
                fill=ORANGE,
                font=_load_font(22 * scale),
            )

    # ── Footer ────────────────────────────────────────────────────────
    footer_y = y + num_users * row_height + 8
    draw.line([(padding, footer_y), (width - padding, footer_y)], fill=BORDER, width=2)

    perfect_count = sum(1 for u in user_stats if u["days_complete"] == u["total_days"] and u["total_days"] > 0)
    footer_text = f"{perfect_count}/{num_users} had a perfect week"
    draw.text((padding, footer_y + 14 * scale), footer_text, fill=TEXT_DIM, font=font_footer)

    # Output
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
