"""User-facing /transformation command for on-demand photo composites."""

from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from bot.utils.photo_transform import render_transformation


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


def get_transformation_handler() -> CommandHandler:
    """Return the /transformation command handler."""
    return CommandHandler("transformation", transformation_command)
