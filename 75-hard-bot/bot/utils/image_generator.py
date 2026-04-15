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
