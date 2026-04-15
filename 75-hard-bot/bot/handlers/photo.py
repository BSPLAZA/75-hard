"""Photo progress handlers -- DM-based flow triggered from daily card."""

from datetime import date

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bot.config import CB_PHOTO, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.utils.easter_eggs import check_first_completion
from bot.templates.messages import (
    PHOTO_ASK,
    PHOTO_CHECK_DM,
    PHOTO_GROUP_NOTIFY,
    PHOTO_NEED_PHOTO,
    PHOTO_SAVED,
    PHOTO_UPDATED,
)
from bot.utils.progress import get_day_number


async def photo_start_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the Photo button tap on the daily card -- send user to DMs."""
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
        await db.create_checkin(
            update.effective_user.id, day_number, today.isoformat()
        )

    await query.answer("check your DMs 📸", show_alert=True)

    # DM them directly with a prompt
    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"send me your Day {day_number} progress photo 📸",
        )
    except Exception:
        await query.answer("DM me first — tap t.me/lockedinlukebot to start", show_alert=True)
        return

    # Mark that the user should send a photo (works in group or DM)
    context.user_data["awaiting_photo"] = True
    context.user_data["photo_day"] = day_number

    # DM the user
    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=PHOTO_ASK.format(day=day_number),
        )
    except Exception:
        await query.message.reply_text(
            f"@{update.effective_user.username or update.effective_user.first_name} "
            "I can't DM you! Tap t.me/{bot_username}?start=register first."
        )


async def handle_dm_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process a photo sent in DMs."""
    if update.effective_chat.type != "private":
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id

    today = date.today()
    day_number = context.user_data.get(
        "photo_day", get_day_number(CHALLENGE_START_DATE, today)
    )

    if day_number < 1:
        await update.message.reply_text("The challenge hasn't started yet!")
        return

    user = await db.get_user(user_id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    checkin = await db.get_checkin(user_id, day_number)
    if not checkin:
        await db.create_checkin(user_id, day_number, today.isoformat())
        checkin = await db.get_checkin(user_id, day_number)

    # Get the highest-resolution photo
    photo = update.message.photo[-1]
    file_id = photo.file_id

    already_had_photo = checkin["photo_done"]
    just_completed = await db.log_photo(user_id, day_number, file_id)
    await db.log_event(user_id, user["name"], "photo_submit", f"day={day_number}")

    context.user_data.pop("awaiting_photo", None)
    context.user_data.pop("photo_day", None)

    if already_had_photo:
        await update.message.reply_text(PHOTO_UPDATED.format(day=day_number))
    else:
        await update.message.reply_text(PHOTO_SAVED.format(day=day_number))

    # Refresh the daily card
    await refresh_card(context, day_number)

    if just_completed:
        name_for_egg = user["name"] if user else update.effective_user.first_name
        await check_first_completion(context, name_for_egg, day_number)

    # Notify the group
    group_chat_id = context.bot_data.get("group_chat_id")
    if group_chat_id:
        name = user["name"]
        # Count how many users have submitted photos today
        checkins = await db.get_all_checkins_for_day(day_number)
        photo_count = sum(1 for c in checkins if c["photo_done"])
        total = len(checkins)
        try:
            await context.bot.send_message(
                chat_id=group_chat_id,
                text=PHOTO_GROUP_NOTIFY.format(
                    name=name, count=photo_count, total=total
                ),
            )
        except Exception:
            pass


async def handle_dm_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Reject non-photo files in DMs when we're expecting a photo."""
    if update.effective_chat.type != "private":
        return

    if context.user_data.get("awaiting_photo"):
        await update.message.reply_text(PHOTO_NEED_PHOTO)


def get_photo_handlers() -> list:
    """Return photo-related callback handlers."""
    return [
        CallbackQueryHandler(photo_start_callback, pattern=f"^{CB_PHOTO}$"),
    ]


def get_dm_photo_handler() -> MessageHandler:
    """Return the MessageHandler for photos in private chats."""
    return MessageHandler(
        filters.PHOTO & filters.ChatType.PRIVATE, handle_dm_photo
    )
