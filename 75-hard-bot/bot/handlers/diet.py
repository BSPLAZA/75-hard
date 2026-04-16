"""Diet tracking handler -- inline button callback."""

from datetime import date

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.utils.progress import today_et
from bot.config import CB_DIET
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.templates.messages import DIET_OFF, DIET_ON
from bot.utils.easter_eggs import check_first_completion


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
        await db.create_checkin(update.effective_user.id, day_number, today_et().isoformat())

    now_on, just_completed = await db.toggle_diet(update.effective_user.id, day_number)
    await db.log_event(update.effective_user.id, user["name"], "diet_toggle", f"on={now_on}")
    if now_on:
        diet_plan = dict(user).get("diet_plan", "your diet")
        await query.answer(f"diet confirmed — {diet_plan}\nno alcohol, no cheat meals", show_alert=True)
    else:
        await query.answer("diet un-logged\ntap again when you're back on track", show_alert=True)
    await refresh_card(context, day_number)

    if just_completed:
        name = user["name"] if user else update.effective_user.first_name
        await check_first_completion(context, name, day_number)


def get_diet_callback_handler() -> CallbackQueryHandler:
    return CallbackQueryHandler(diet_callback, pattern=f"^{CB_DIET}$")
