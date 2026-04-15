"""Water tracking handlers -- inline button callback and /water command."""

from datetime import date

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import CB_WATER, CHALLENGE_START_DATE, WATER_GOAL
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.templates.messages import WATER_FULL, WATER_POPUP, WATER_SET
from bot.utils.easter_eggs import check_first_completion
from bot.utils.progress import get_day_number


async def water_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the Water +1 inline button tap."""
    query = update.callback_query
    db = context.bot_data["db"]

    # Resolve day from the card that was tapped
    day_number = await resolve_day_from_card(db, query.message.message_id)
    if not day_number:
        await query.answer("Card not found. Use /admin_reset_day.", show_alert=True)
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.answer("Register first! DM me /start", show_alert=True)
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await db.create_checkin(update.effective_user.id, day_number, date.today().isoformat())
        checkin = await db.get_checkin(update.effective_user.id, day_number)

    if checkin["water_cups"] >= WATER_GOAL:
        await query.answer(WATER_FULL, show_alert=False)
        return

    new_cups, just_completed = await db.increment_water(update.effective_user.id, day_number)
    await db.log_event(update.effective_user.id, user["name"], "water_tap", f"cups={new_cups}")
    await query.answer(WATER_POPUP.format(cups=new_cups), show_alert=False)
    await refresh_card(context, day_number)

    if just_completed:
        user_obj = await db.get_user(update.effective_user.id)
        name = user_obj["name"] if user_obj else update.effective_user.first_name
        await check_first_completion(context, name, day_number)


async def water_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /water set N to manually correct cup count."""
    db = context.bot_data["db"]
    today = date.today()
    day_number = max(get_day_number(CHALLENGE_START_DATE, today), 1)

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

    just_completed = await db.set_water(update.effective_user.id, day_number, cups)
    await update.message.reply_text(WATER_SET.format(cups=cups))
    await refresh_card(context, day_number)

    if just_completed:
        name = user["name"] if user else update.effective_user.first_name
        await check_first_completion(context, name, day_number)


def get_water_callback_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(water_callback, pattern=f"^{CB_WATER}$")


def get_water_command_handler() -> CommandHandler:
    return CommandHandler("water", water_set_command)
