"""Reading tracking handlers -- DM-based flow triggered from daily card."""

from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import CB_READ, CB_READ_NEW, CB_READ_SAME, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.templates.messages import (
    READ_ALREADY_DONE,
    READ_ASK_BOOK,
    READ_ASK_TAKEAWAY,
    READ_CHECK_DM,
    READ_SAME_BOOK,
)
from bot.utils.progress import get_day_number


async def read_start_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the Read button tap on the daily card -- send user to DMs."""
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
        checkin = await db.get_checkin(update.effective_user.id, day_number)

    if checkin["reading_done"]:
        await query.answer(READ_ALREADY_DONE, show_alert=True)
        return

    await query.answer(READ_CHECK_DM)

    # Store day number so the DM handler knows which day to log
    context.user_data["reading_day"] = day_number

    # DM the user
    current_book = user["current_book"]
    try:
        if current_book:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Yes, same book", callback_data=CB_READ_SAME
                        ),
                        InlineKeyboardButton(
                            "Started a new book", callback_data=CB_READ_NEW
                        ),
                    ]
                ]
            )
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=READ_SAME_BOOK.format(book=current_book),
                reply_markup=keyboard,
            )
        else:
            context.user_data["awaiting_book_title"] = True
            context.user_data["reading_new_book"] = True
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=READ_ASK_BOOK,
            )
    except Exception:
        await query.message.reply_text(
            f"@{update.effective_user.username or update.effective_user.first_name} "
            "I can't DM you! Tap t.me/{bot_username}?start=register first."
        )


async def read_same_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle 'Yes, same book' button in DM."""
    query = update.callback_query
    await query.answer()

    context.user_data["awaiting_takeaway"] = True
    context.user_data["reading_new_book"] = False

    try:
        await query.edit_message_text(READ_ASK_TAKEAWAY)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def read_new_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle 'Started a new book' button in DM."""
    query = update.callback_query
    await query.answer()

    context.user_data["awaiting_book_title"] = True
    context.user_data["reading_new_book"] = True

    try:
        await query.edit_message_text(READ_ASK_BOOK)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def handle_dm_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Process DM text for the reading flow.

    Returns True if the message was consumed by this handler, False otherwise.
    """
    # Only handle private messages
    if update.effective_chat.type != "private":
        return False

    db = context.bot_data["db"]
    user_id = update.effective_user.id

    # Step: awaiting book title
    if context.user_data.get("awaiting_book_title"):
        title = update.message.text.strip()
        context.user_data["pending_book_title"] = title
        context.user_data.pop("awaiting_book_title", None)
        context.user_data["awaiting_takeaway"] = True

        await update.message.reply_text(READ_ASK_TAKEAWAY)
        return True

    # Step: awaiting takeaway
    if context.user_data.get("awaiting_takeaway"):
        takeaway = update.message.text.strip()
        context.user_data.pop("awaiting_takeaway", None)

        day_number = context.user_data.pop("reading_day", None)
        if day_number is None:
            today = date.today()
            day_number = get_day_number(CHALLENGE_START_DATE, today)

        is_new_book = context.user_data.pop("reading_new_book", False)
        user = await db.get_user(user_id)

        if is_new_book:
            title = context.user_data.pop("pending_book_title", "Unknown")
            # Finish old book if any
            if user and user["current_book"]:
                await db.finish_book(user_id, finished_day=day_number)
            # Start new book
            await db.set_current_book(user_id, title, started_day=day_number)
        else:
            title = (user["current_book"] if user else None) or "Unknown"
            context.user_data.pop("pending_book_title", None)

        await db.log_reading(user_id, day_number, title, takeaway)

        name = user["name"] if user else update.effective_user.first_name
        await update.message.reply_text(
            f"Reading logged! 📖 Your takeaway from \"{title}\" will show up in tonight's recap."
        )

        # Refresh the daily card
        await refresh_card(context, day_number)

        return True

    return False


async def reread_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /reread -- allow updating today's reading entry."""
    db = context.bot_data["db"]
    today = date.today()
    day_number = get_day_number(CHALLENGE_START_DATE, today)

    if day_number < 1:
        await update.message.reply_text("The challenge hasn't started yet!")
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    # Set up the reading flow again
    context.user_data["reading_day"] = day_number
    current_book = user["current_book"]

    if current_book:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Yes, same book", callback_data=CB_READ_SAME
                    ),
                    InlineKeyboardButton(
                        "Started a new book", callback_data=CB_READ_NEW
                    ),
                ]
            ]
        )
        await update.message.reply_text(
            READ_SAME_BOOK.format(book=current_book),
            reply_markup=keyboard,
        )
    else:
        context.user_data["awaiting_book_title"] = True
        context.user_data["reading_new_book"] = True
        await update.message.reply_text(READ_ASK_BOOK)


async def setbook_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setbook <title> — set or update your current book anytime."""
    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    if not context.args:
        if user["current_book"]:
            await update.message.reply_text(f'Your current book is "{user["current_book"]}". To change it: /setbook <new title>')
        else:
            await update.message.reply_text("Usage: /setbook <book title>")
        return

    title = " ".join(context.args)
    day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)

    if user["current_book"]:
        await db.finish_book(update.effective_user.id, finished_day=day)

    await db.set_current_book(update.effective_user.id, title, started_day=day)
    await update.message.reply_text(f'Book set to "{title}" 📖')


async def setdiet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setdiet <plan> — set or update your diet plan anytime."""
    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    if not context.args:
        if user.get("diet_plan"):
            await update.message.reply_text(f'Your current diet is "{user["diet_plan"]}". To change it: /setdiet <new plan>')
        else:
            await update.message.reply_text("Usage: /setdiet <your diet plan>")
        return

    plan = " ".join(context.args)
    await db.set_diet_plan(update.effective_user.id, plan)
    await update.message.reply_text(f'Diet set to "{plan}" 🍽️')


def get_reading_handlers() -> list:
    """Return all reading-related callback handlers."""
    return [
        CallbackQueryHandler(read_start_callback, pattern=f"^{CB_READ}$"),
        CallbackQueryHandler(read_same_callback, pattern=f"^{CB_READ_SAME}$"),
        CallbackQueryHandler(read_new_callback, pattern=f"^{CB_READ_NEW}$"),
        CommandHandler("reread", reread_command),
        CommandHandler("setbook", setbook_command),
        CommandHandler("setdiet", setdiet_command),
    ]
