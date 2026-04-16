"""Photo progress handlers -- DM-based flow triggered from daily card."""

from datetime import date

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bot.config import CB_PHOTO, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.utils.easter_eggs import check_first_completion
from bot.templates.messages import (
    PHOTO_ASK,
    PHOTO_NEED_PHOTO,
    PHOTO_SAVED,
    PHOTO_UPDATED,
)
from bot.utils.progress import today_et, get_day_number


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
            update.effective_user.id, day_number, today_et().isoformat()
        )

    await query.answer("check your DMs 📸", show_alert=True)

    # Mark that the user should send a photo (works in group or DM)
    context.user_data["awaiting_photo"] = True
    context.user_data["photo_day"] = day_number

    # DM the user with the prompt
    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=PHOTO_ASK.format(day=day_number),
        )
    except Exception:
        await query.message.reply_text(
            f"@{update.effective_user.username or update.effective_user.first_name} "
            "I can't DM you! Tap t.me/lockedinlukebot to start a chat first."
        )


async def handle_dm_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process a photo sent in DMs.

    Two paths:
      A) User opted in (tapped photo button OR Luke set up a backfill):
         save as progress photo.
      B) User just sent a photo + caption (or no caption):
         route to Luke with vision so he can answer questions about it.
    """
    if update.effective_chat.type != "private":
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id

    user = await db.get_user(user_id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id
    caption = update.message.caption or ""

    awaiting = context.user_data.get("awaiting_photo")
    photo_day = context.user_data.get("photo_day")

    # Path A: progress-photo save (only when user opted in)
    if awaiting and photo_day:
        if photo_day < 1:
            await update.message.reply_text("The challenge hasn't started yet!")
            return

        checkin = await db.get_checkin(user_id, photo_day)
        if not checkin:
            await db.create_checkin(user_id, photo_day, today_et().isoformat())
            checkin = await db.get_checkin(user_id, photo_day)

        already_had_photo = checkin["photo_done"]
        just_completed = await db.log_photo(user_id, photo_day, file_id)
        await db.log_event(user_id, user["name"], "photo_submit", f"day={photo_day}")

        context.user_data.pop("awaiting_photo", None)
        context.user_data.pop("photo_day", None)

        if already_had_photo:
            await update.message.reply_text(PHOTO_UPDATED.format(day=photo_day))
        else:
            await update.message.reply_text(PHOTO_SAVED.format(day=photo_day))

        await refresh_card(context, photo_day)

        if just_completed:
            name_for_egg = user["name"] if user else update.effective_user.first_name
            await check_first_completion(context, name_for_egg, photo_day)
        return

    # Path B: photo sent without opt-in — route to Luke with vision
    try:
        import base64 as _b64
        tg_file = await context.bot.get_file(file_id)
        buf = await tg_file.download_as_bytearray()
        image_b64 = _b64.b64encode(bytes(buf)).decode()
    except Exception as e:
        await update.message.reply_text(
            "couldn't download that photo. if it's your progress photo, "
            "tap the 📸 button on today's card first."
        )
        return

    from bot.utils.luke_chat import chat_with_luke
    prompt = caption.strip() or "the user sent a photo with no caption. ask them what they'd like to do with it: save as their progress photo for today, or ask a question about it."
    result = await chat_with_luke(prompt, db, user_id, image_b64=image_b64)
    if result.get("text"):
        await update.message.reply_text(result["text"])


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
