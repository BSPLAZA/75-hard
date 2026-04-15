"""Generate side-by-side photo transformation composites."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Colors (match the dark theme from image_generator.py)
BG = "#0d1117"
TEXT_PRIMARY = "#e6edf3"
TEXT_DIM = "#7d8590"
BORDER = "#30363d"
GREEN = "#3fb950"

# Font path
ASSETS_DIR = Path(__file__).parent.parent / "assets"


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font_path = ASSETS_DIR / "Inter-Bold.ttf"
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit_photo(photo: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Resize a photo to fit within max_w x max_h, maintaining aspect ratio."""
    ratio = min(max_w / photo.width, max_h / photo.height)
    new_w = int(photo.width * ratio)
    new_h = int(photo.height * ratio)
    return photo.resize((new_w, new_h), Image.LANCZOS)


async def _download_photo(bot, file_id: str) -> Image.Image:
    """Download a photo from Telegram by file_id and return a PIL Image."""
    file = await bot.get_file(file_id)
    buf = BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


async def render_transformation(
    bot,
    name: str,
    day1_file_id: str,
    current_file_id: str,
    current_day: int,
) -> BytesIO:
    """Create a side-by-side transformation composite.

    Downloads both photos from Telegram, places them side by side with labels
    on a dark background matching the bot's visual theme.

    Args:
        bot: telegram.Bot instance for downloading files.
        name: User's display name for the header.
        day1_file_id: Telegram file_id for the Day 1 photo.
        current_file_id: Telegram file_id for the current day photo.
        current_day: The current day number.

    Returns:
        BytesIO PNG buffer ready to send.
    """
    scale = 2
    total_width = 1200 * scale
    padding = 40 * scale
    header_height = 80 * scale
    photo_area_w = 540 * scale  # each photo area
    photo_area_h = 720 * scale
    gap = 40 * scale  # gap between photos
    label_height = 50 * scale
    footer_pad = 30 * scale

    total_height = (
        padding + header_height + photo_area_h + label_height + footer_pad + padding
    )

    img = Image.new("RGB", (total_width, total_height), BG)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(32 * scale)
    font_label = _load_font(22 * scale)

    # -- Header: "NAME'S TRANSFORMATION" --
    title = f"{name.upper()}'S TRANSFORMATION"
    bbox = draw.textbbox((0, 0), title, font=font_title)
    tw = bbox[2] - bbox[0]
    title_x = (total_width - tw) // 2
    title_y = padding
    draw.text((title_x, title_y), title, fill=TEXT_PRIMARY, font=font_title)

    # Separator line under header
    sep_y = padding + header_height - 10 * scale
    draw.line(
        [(padding, sep_y), (total_width - padding, sep_y)],
        fill=BORDER,
        width=2,
    )

    # -- Download and place photos --
    day1_img = await _download_photo(bot, day1_file_id)
    current_img = await _download_photo(bot, current_file_id)

    day1_fit = _fit_photo(day1_img, photo_area_w, photo_area_h)
    current_fit = _fit_photo(current_img, photo_area_w, photo_area_h)

    photos_y = padding + header_height

    # Left photo (Day 1): centered in left half
    left_center_x = padding + photo_area_w // 2
    left_x = left_center_x - day1_fit.width // 2
    left_y = photos_y + (photo_area_h - day1_fit.height) // 2
    img.paste(day1_fit, (left_x, left_y))

    # Right photo (Current): centered in right half
    right_start = padding + photo_area_w + gap
    right_center_x = right_start + photo_area_w // 2
    right_x = right_center_x - current_fit.width // 2
    right_y = photos_y + (photo_area_h - current_fit.height) // 2
    img.paste(current_fit, (right_x, right_y))

    # -- Labels under photos --
    label_y = photos_y + photo_area_h + 10 * scale

    # "DAY 1" label centered under left photo
    day1_label = "DAY 1"
    bbox = draw.textbbox((0, 0), day1_label, font=font_label)
    lw = bbox[2] - bbox[0]
    draw.text(
        (left_center_x - lw // 2, label_y),
        day1_label,
        fill=TEXT_DIM,
        font=font_label,
    )

    # "DAY N" label centered under right photo
    current_label = f"DAY {current_day}"
    bbox = draw.textbbox((0, 0), current_label, font=font_label)
    lw = bbox[2] - bbox[0]
    draw.text(
        (right_center_x - lw // 2, label_y),
        current_label,
        fill=GREEN,
        font=font_label,
    )

    # -- Arrow between photos --
    arrow_y = photos_y + photo_area_h // 2
    arrow_x = padding + photo_area_w + gap // 2
    arrow_text = ">"
    bbox = draw.textbbox((0, 0), arrow_text, font=font_label)
    aw = bbox[2] - bbox[0]
    ah = bbox[3] - bbox[1]
    draw.text(
        (arrow_x - aw // 2, arrow_y - ah // 2),
        arrow_text,
        fill=TEXT_DIM,
        font=font_label,
    )

    # Output
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
