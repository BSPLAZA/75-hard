"""Water tracking handlers -- inline button callback and /water command."""

from datetime import date

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import CB_WATER, CHALLENGE_START_DATE, WATER_GOAL
from bot.handlers.daily_card import refresh_card
from bot.templates.messages import CARD_EXPIRED, WATER_FULL, WATER_POPUP, WATER_SET
from bot.utils.progress import get_day_number


async def water_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the Water +1 inline button tap."""
    query = update.callback_query
    db = context.bot_data["db"]

    today = date.today()
    day_number = get_day_number(CHALLENGE_START_DATE, today)

    # Check the card belongs to today
    card = await db.get_card_by_message_id(query.message.message_id)
    if not card or card["day_number"] != day_number:
        await query.answer(CARD_EXPIRED, show_alert=True)
        return

    # Ensure user has a check-in row
    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.answer("Register first! DM me /start", show_alert=True)
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await db.create_checkin(update.effective_user.id, day_number, today.isoformat())
        checkin = await db.get_checkin(update.effective_user.id, day_number)

    if checkin["water_cups"] >= WATER_GOAL:
        await query.answer(WATER_FULL, show_alert=False)
        return

    new_cups = await db.increment_water(update.effective_user.id, day_number)
    await query.answer(WATER_POPUP.format(cups=new_cups), show_alert=False)
    await refresh_card(context, day_number)


async def water_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /water set N to manually correct cup count."""
    db = context.bot_data["db"]
    today = date.today()
    day_number = get_day_number(CHALLENGE_START_DATE, today)

    if day_number < 1:
        await update.message.reply_text("The challenge hasn't started yet!")
        return

    # Parse the command: /water set 5
    args = context.args or []
    if len(args) < 2 or args[0].lower() != "set":
        await update.message.reply_text("Usage: /water set <number>")
        return

    try:
        cups = int(args[1])
    except ValueError:
        await update.message.reply_text("Usage: /water set <number>")
        return

    cups = max(0, min(cups, WATER_GOAL))

    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await db.create_checkin(update.effective_user.id, day_number, today.isoformat())

    await db.set_water(update.effective_user.id, day_number, cups)
    await update.message.reply_text(WATER_SET.format(cups=cups))
    await refresh_card(context, day_number)


def get_water_callback_handler() -> CallbackQueryHandler:
    """Return the inline button handler for water taps."""
    return CallbackQueryHandler(water_callback, pattern=f"^{CB_WATER}$")


def get_water_command_handler() -> CommandHandler:
    """Return the /water command handler."""
    return CommandHandler("water", water_set_command)
