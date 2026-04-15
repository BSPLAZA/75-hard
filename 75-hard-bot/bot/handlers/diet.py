"""Diet tracking handler -- inline button callback."""

from datetime import date

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.config import CB_DIET
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.templates.messages import DIET_OFF, DIET_ON


async def diet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the Diet inline button tap -- toggles diet_done."""
    query = update.callback_query
    db = context.bot_data["db"]

    day_number = await resolve_day_from_card(db, query.message.message_id)
    if not day_number:
        await query.answer("Card not found.", show_alert=True)
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.answer("Register first! DM me /start", show_alert=True)
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await db.create_checkin(update.effective_user.id, day_number, date.today().isoformat())

    now_on = await db.toggle_diet(update.effective_user.id, day_number)
    popup = DIET_ON if now_on else DIET_OFF
    await query.answer(popup, show_alert=False)
    await refresh_card(context, day_number)


def get_diet_callback_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(diet_callback, pattern=f"^{CB_DIET}$")
