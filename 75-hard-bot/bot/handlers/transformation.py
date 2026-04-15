"""User-facing /transformation command for on-demand photo composites."""

from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from bot.utils.photo_transform import render_transformation
from bot.utils.timelapse import render_timelapse


async def transformation_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Generate and send the user's Day 1 vs latest transformation composite."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /transformation in DMs.")
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)

    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    photos = await db.get_photo_file_ids(user_id)

    if not photos:
        await update.message.reply_text(
            "No progress photos found yet. Submit your first photo via the daily card!"
        )
        return

    day1_photo = photos[0]
    latest_photo = photos[-1]

    if day1_photo["day_number"] == latest_photo["day_number"]:
        await update.message.reply_text(
            "You only have one day of photos so far. "
            "Keep going -- your transformation will be ready once you have photos from multiple days!"
        )
        return

    await update.message.reply_text("Building your transformation... one sec.")

    try:
        buf = await render_transformation(
            bot=context.bot,
            name=user["name"],
            day1_file_id=day1_photo["photo_file_id"],
            current_file_id=latest_photo["photo_file_id"],
            current_day=latest_photo["day_number"],
        )
        await update.message.reply_photo(
            photo=buf,
            caption=(
                f"{user['name']}'s transformation -- "
                f"Day 1 to Day {latest_photo['day_number']}"
            ),
        )
    except Exception as e:
        await update.message.reply_text(
            f"Couldn't generate your transformation: {e}"
        )


async def timelapse_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Generate and send an animated GIF timelapse of all progress photos."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /timelapse in DMs.")
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)

    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    photos = await db.get_photo_file_ids(user_id)

    if len(photos) < 2:
        await update.message.reply_text(
            "Need at least 2 days of photos for a timelapse. Keep going!"
        )
        return

    await update.message.reply_text(f"building your timelapse from {len(photos)} photos... hang tight")

    try:
        gif = await render_timelapse(
            bot=context.bot,
            name=user["name"],
            photos=photos,
        )
        if gif:
            from telegram import InputFile
            await context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=InputFile(gif, filename="timelapse.mp4"),
                caption=f"{user['name']}'s 75 Hard timelapse - Day {photos[0]['day_number']} to Day {photos[-1]['day_number']}",
            )
        else:
            await update.message.reply_text("couldn't generate the timelapse. try again later")
    except Exception as e:
        await update.message.reply_text(f"timelapse failed: {e}")


def get_transformation_handler() -> CommandHandler:
    """Return the /transformation command handler."""
    return CommandHandler("transformation", transformation_command)


def get_timelapse_handler() -> CommandHandler:
    """Return the /timelapse command handler."""
    return CommandHandler("timelapse", timelapse_command)
