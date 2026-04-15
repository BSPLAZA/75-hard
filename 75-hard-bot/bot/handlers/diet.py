"""Diet tracking handler -- inline button callback."""

from datetime import date

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.config import CB_DIET, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card
from bot.templates.messages import CARD_EXPIRED, DIET_OFF, DIET_ON
from bot.utils.progress import get_day_number


async def diet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the Diet inline button tap -- toggles diet_done."""
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

    now_on = await db.toggle_diet(update.effective_user.id, day_number)
    popup = DIET_ON if now_on else DIET_OFF
    await query.answer(popup, show_alert=False)
    await refresh_card(context, day_number)


def get_diet_callback_handler() -> CallbackQueryHandler:
    """Return the inline button handler for diet toggles."""
    return CallbackQueryHandler(diet_callback, pattern=f"^{CB_DIET}$")
