"""Post and refresh the daily scoreboard card for the group chat."""

from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import (
    CB_DIET,
    CB_PHOTO,
    CB_READ,
    CB_WATER,
    CB_WORKOUT_OUTDOOR,
    CB_WORKOUT_INDOOR,
    CHALLENGE_START_DATE,
)
from bot.templates.messages import CARD_EXPIRED
from bot.utils.card_renderer import render_card
from bot.utils.progress import today_et, get_day_number


def build_card_keyboard() -> InlineKeyboardMarkup:
    """Return the 5-button inline keyboard for the daily card."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💧 Water +1", callback_data=CB_WATER),
                InlineKeyboardButton("☀️ Outdoor", callback_data=CB_WORKOUT_OUTDOOR),
                InlineKeyboardButton("🏋️ Indoor", callback_data=CB_WORKOUT_INDOOR),
            ],
            [
                InlineKeyboardButton("📖 Read", callback_data=CB_READ),
                InlineKeyboardButton("📸 Photo", callback_data=CB_PHOTO),
                InlineKeyboardButton("🍽️ Diet", callback_data=CB_DIET),
            ],
        ]
    )


async def post_daily_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None = None,
    force_day: int | None = None,
) -> None:
    """Create check-in rows for all active users, render the card, send and pin it.

    Use force_day to override the day number (for testing / admin_reset_day before start).
    """
    db = context.bot_data["db"]
    chat_id = chat_id or context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    today = today_et()
    day_number = force_day or get_day_number(CHALLENGE_START_DATE, today)
    if day_number < 1:
        day_number = 1  # Allow preview card before challenge starts

    # Ensure every active user has a check-in row for today
    active_users = await db.get_active_users()
    for user in active_users:
        await db.create_checkin(user["telegram_id"], day_number, today.isoformat())

    # Gather check-ins and previous day's for ordering
    checkins = await db.get_all_checkins_for_day(day_number)
    checkin_dicts = [dict(c) for c in checkins]

    prev_checkins = None
    if day_number > 1:
        prev = await db.get_all_checkins_for_day(day_number - 1)
        prev_checkins = [dict(c) for c in prev] if prev else None

    active_count = len(active_users)
    prize_pool = active_count * 75

    # Fetch active penances whose makeup_day = today, keyed by user.
    # Used by the renderer to show 2× targets on the right cells.
    penances_by_user: dict[int, list[dict]] = {}
    for u in active_users:
        rows = await db.get_penances_for_makeup_day(u["telegram_id"], day_number)
        if rows:
            penances_by_user[u["telegram_id"]] = [dict(r) for r in rows]

    card_text = render_card(
        day_number=day_number,
        active_count=active_count,
        prize_pool=prize_pool,
        checkins=checkin_dicts,
        prev_checkins=prev_checkins,
        penances_by_user=penances_by_user,
    )

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=card_text,
        reply_markup=build_card_keyboard(),
        parse_mode="HTML",
    )

    # Save the card reference
    await db.save_card(day_number, today.isoformat(), msg.message_id, chat_id)

    # Pin the new card (unpin_all first to keep only latest pinned)
    try:
        await context.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=msg.message_id,
            disable_notification=True,
        )
    except Exception:
        pass


async def refresh_card(
    context: ContextTypes.DEFAULT_TYPE,
    day_number: int,
) -> None:
    """Re-read the DB for *day_number*, re-render, and edit the message in-place."""
    db = context.bot_data["db"]
    card = await db.get_card(day_number)
    if not card:
        return

    checkins = await db.get_all_checkins_for_day(day_number)
    checkin_dicts = [dict(c) for c in checkins]

    prev_checkins = None
    if day_number > 1:
        prev = await db.get_all_checkins_for_day(day_number - 1)
        prev_checkins = [dict(c) for c in prev] if prev else None

    active_users = await db.get_active_users()
    active_count = len(active_users)
    prize_pool = active_count * 75

    penances_by_user: dict[int, list[dict]] = {}
    for u in active_users:
        rows = await db.get_penances_for_makeup_day(u["telegram_id"], day_number)
        if rows:
            penances_by_user[u["telegram_id"]] = [dict(r) for r in rows]

    card_text = render_card(
        day_number=day_number,
        active_count=active_count,
        prize_pool=prize_pool,
        checkins=checkin_dicts,
        prev_checkins=prev_checkins,
        penances_by_user=penances_by_user,
    )

    try:
        await context.bot.edit_message_text(
            chat_id=card["chat_id"],
            message_id=card["message_id"],
            text=card_text,
            reply_markup=build_card_keyboard(),
            parse_mode="HTML",
        )
    except BadRequest as exc:
        # Telegram raises this if the text hasn't actually changed
        if "message is not modified" not in str(exc).lower():
            raise


async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/card -- reply with a link to today's card or re-post if missing."""
    db = context.bot_data["db"]
    today = today_et()
    day_number = get_day_number(CHALLENGE_START_DATE, today)

    if day_number < 1:
        await update.message.reply_text("The challenge hasn't started yet!")
        return

    card = await db.get_card(day_number)
    if card and card["message_id"]:
        chat_id = card["chat_id"]
        msg_id = card["message_id"]
        # Try linking to the existing message
        try:
            # Refresh the card so it's up to date, then tell user where it is
            await refresh_card(context, day_number)
            await update.message.reply_text(
                f"Today's card is pinned above (Day {day_number})."
            )
        except Exception:
            await update.message.reply_text(
                f"Today's card is pinned above (Day {day_number})."
            )
    else:
        # No card yet -- post one
        target_chat = update.effective_chat.id
        await post_daily_card(context, chat_id=target_chat)


async def resolve_day_from_card(db, message_id: int) -> int | None:
    """Look up which day a card belongs to by its message_id.

    Returns the day_number if found, otherwise None.
    """
    card = await db.get_card_by_message_id(message_id)
    return card["day_number"] if card else None


def get_card_command_handler() -> CommandHandler:
    """Return the /card command handler."""
    return CommandHandler("card", card_command)
