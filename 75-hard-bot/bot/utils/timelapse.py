"""Generate timelapse video from progress photos using Pillow + ffmpeg."""

import logging
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent.parent / "assets"

BG = "#0d1117"
GREEN = "#3fb950"
DIM = "#7d8590"


def _load_font(size: int, bold: bool = False):
    name = "Inter-Bold.ttf" if bold else "Inter-Medium.ttf"
    font_path = ASSETS_DIR / name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    for path in ["/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _make_frame(photo: Image.Image, day_number: int, name: str, total_days: int) -> Image.Image:
    """Create a single labeled frame."""
    frame_w, frame_h = 1080, 1440
    label_h = 140
    total_h = frame_h + label_h

    frame = Image.new("RGB", (frame_w, total_h), BG)
    draw = ImageDraw.Draw(frame)

    # Resize photo maintaining aspect ratio
    photo_copy = photo.copy()
    photo_copy.thumbnail((frame_w - 20, frame_h - 20), Image.LANCZOS)
    pw, ph = photo_copy.size
    x = (frame_w - pw) // 2
    y = (frame_h - ph) // 2
    frame.paste(photo_copy, (x, y))

    font_day = _load_font(56, bold=True)
    font_name = _load_font(28)

    draw.text((30, frame_h + 20), f"DAY {day_number}", fill=GREEN, font=font_day)

    progress = f"{name}  ·  {day_number}/{total_days}"
    bbox = draw.textbbox((0, 0), progress, font=font_name)
    tw = bbox[2] - bbox[0]
    draw.text((frame_w - tw - 30, frame_h + 35), progress, fill=DIM, font=font_name)

    return frame


async def render_timelapse(
    bot,
    name: str,
    photos: list[dict],
    total_days: int = 75,
    seconds_per_frame: float = 1.5,
) -> BytesIO | None:
    """Generate an MP4 timelapse from progress photos.

    Falls back to sending high-quality PNGs as a media group if ffmpeg unavailable.
    """
    if len(photos) < 2:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        frame_paths = []

        for i, photo_data in enumerate(photos):
            try:
                file = await bot.get_file(photo_data["photo_file_id"])
                buf = BytesIO()
                await file.download_to_memory(buf)
                buf.seek(0)
                img = Image.open(buf).convert("RGB")

                frame = _make_frame(img, photo_data["day_number"], name, total_days)

                # Ensure dimensions are even (required by h264)
                w, h = frame.size
                if w % 2: w -= 1
                if h % 2: h -= 1
                frame = frame.resize((w, h), Image.LANCZOS)

                frame_path = tmpdir / f"frame_{i:04d}.png"
                frame.save(frame_path, format="PNG")
                frame_paths.append(frame_path)
            except Exception as e:
                logger.warning("Could not process photo for day %d: %s", photo_data["day_number"], e)
                continue

        if len(frame_paths) < 2:
            return None

        # Use ffmpeg to create MP4
        output_path = tmpdir / "timelapse.mp4"
        fps = 1.0 / seconds_per_frame

        try:
            # Create a concat file for variable-length frames
            concat_file = tmpdir / "concat.txt"
            with open(concat_file, "w") as f:
                for i, fp in enumerate(frame_paths):
                    duration = seconds_per_frame if i < len(frame_paths) - 1 else seconds_per_frame * 3
                    f.write(f"file '{fp}'\n")
                    f.write(f"duration {duration}\n")
                # ffmpeg concat needs the last file repeated
                f.write(f"file '{frame_paths[-1]}'\n")

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_file),
                    "-vf", "scale=1080:-2",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "fast",
                    "-crf", "23",
                    "-movflags", "+faststart",
                    str(output_path),
                ],
                capture_output=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.error("ffmpeg failed: %s", result.stderr.decode()[-500:])
                return None

            buf = BytesIO()
            with open(output_path, "rb") as f:
                buf.write(f.read())
            buf.seek(0)
            return buf

        except FileNotFoundError:
            logger.warning("ffmpeg not found, cannot generate video")
            return None
        except Exception as e:
            logger.error("Timelapse video generation failed: %s", e)
            return None
