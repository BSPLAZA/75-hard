"""Generate a bookshelf image showing everyone's book covers and reading quotes."""

import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent.parent / "assets"

BG = "#0d1117"
SURFACE = "#161b22"
BORDER = "#30363d"
TEXT_PRIMARY = "#e6edf3"
TEXT_DIM = "#7d8590"
GREEN = "#3fb950"


def _load_font(size: int, bold: bool = False):
    name = "Inter-Bold.ttf" if bold else "Inter-Medium.ttf"
    font_path = ASSETS_DIR / name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    for path in ["/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


async def _fetch_cover_image(url: str) -> Image.Image | None:
    """Download a book cover and return as PIL Image."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        logger.warning("Failed to fetch cover: %s", e)
        return None


def _wrap_text(text: str, font, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """Wrap text to fit within max_width."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


async def render_bookshelf(readers: list[dict]) -> BytesIO | None:
    """Generate a bookshelf image.

    readers: list of dicts with keys:
        name: str
        book_title: str
        takeaway: str (quote/takeaway, can be empty)
        cover_url: str | None
    """
    if not readers:
        return None

    scale = 2
    padding = 30 * scale
    row_height = 120 * scale
    cover_w = 70 * scale
    cover_h = 100 * scale
    header_height = 60 * scale
    quote_max_width = 400 * scale

    width = 700 * scale
    height = header_height + len(readers) * row_height + padding

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(22 * scale, bold=True)
    font_name = _load_font(16 * scale, bold=True)
    font_book = _load_font(13 * scale)
    font_quote = _load_font(12 * scale)

    # Header
    draw.text((padding, padding), "📖  WHAT WE READ TODAY", fill=TEXT_DIM, font=font_title)
    y = header_height

    for reader in readers:
        row_y = y + 10 * scale

        # Cover image
        cover_img = None
        if reader.get("cover_url"):
            cover_img = await _fetch_cover_image(reader["cover_url"])

        cover_x = padding
        if cover_img:
            cover_img = cover_img.resize((cover_w, cover_h), Image.LANCZOS)
            img.paste(cover_img, (cover_x, row_y))
        else:
            # Placeholder
            draw.rectangle(
                [cover_x, row_y, cover_x + cover_w, row_y + cover_h],
                outline=BORDER, width=2,
            )
            draw.text(
                (cover_x + 10 * scale, row_y + 35 * scale),
                "📖", fill=TEXT_DIM, font=_load_font(20 * scale),
            )

        # Name + book title
        text_x = cover_x + cover_w + 16 * scale
        draw.text((text_x, row_y), reader["name"], fill=TEXT_PRIMARY, font=font_name)
        draw.text(
            (text_x, row_y + 22 * scale),
            reader["book_title"], fill=GREEN, font=font_book,
        )

        # Quote (wrapped)
        takeaway = reader.get("takeaway", "")
        if takeaway:
            quote_lines = _wrap_text(f'"{takeaway}"', font_quote, quote_max_width, draw)
            for i, line in enumerate(quote_lines[:3]):  # Max 3 lines
                draw.text(
                    (text_x, row_y + 44 * scale + i * 16 * scale),
                    line, fill=TEXT_DIM, font=font_quote,
                )

        y += row_height

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
