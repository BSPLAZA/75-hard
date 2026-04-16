"""Reading tracking handlers -- DM-based flow triggered from daily card."""

from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import CB_READ, CB_READ_NEW, CB_READ_SAME, CHALLENGE_START_DATE
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.utils.books import fetch_book_cover
from bot.utils.easter_eggs import check_first_completion
from bot.templates.messages import (
    READ_ALREADY_DONE,
    READ_ASK_BOOK,
    READ_ASK_TAKEAWAY,
    READ_CHECK_DM,
    READ_SAME_BOOK,
)
from bot.utils.progress import today_et, get_day_number


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

    user_id = update.effective_user.id
    current_book = user["current_book"]

    # If they have a book set, one-tap: mark reading done immediately
    if current_book:
        just_completed = await db.log_reading(user_id, day_number, current_book, "")
        await query.answer(f'reading logged — "{current_book}"', show_alert=True)
        await refresh_card(context, day_number)
        await db.log_event(user_id, user["name"], "reading_log", current_book)
        if just_completed:
            await check_first_completion(context, user["name"], day_number)

        # DM them asking for an optional quote
        try:
            context.user_data["awaiting_takeaway"] = True
            context.user_data["reading_day"] = day_number
            await context.bot.send_message(
                chat_id=user_id,
                text=f'what was your takeaway from "{current_book}" today? (or skip — just type "skip")',
            )
        except Exception:
            pass
        return

    # No book set — need to DM them to set one
    await query.answer("DM me to set your book first — /setbook <title>", show_alert=True)
    return

    # --- Legacy DM flow below (kept for /reread and NLP) ---
    context.user_data["reading_day"] = day_number

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

    # Step: awaiting diet plan input (from /setdiet with no args)
    if context.user_data.get("awaiting_diet_input"):
        context.user_data.pop("awaiting_diet_input", None)
        plan = update.message.text.strip()
        await db.set_diet_plan(user_id, plan)
        await update.message.reply_text(
            f'diet set to "{plan}" 🍽️\n\n'
            f"just DM me what you eat throughout the day and i'll track it for you"
        )
        return True

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

        # Handle skip
        if takeaway.lower() in ("skip", "no", "nah", "pass", "none"):
            await update.message.reply_text("no worries 👍")
            context.user_data.pop("reading_day", None)
            context.user_data.pop("reading_new_book", None)
            context.user_data.pop("pending_book_title", None)
            return True

        day_number = context.user_data.pop("reading_day", None)
        if day_number is None:
            today = today_et()
            day_number = get_day_number(CHALLENGE_START_DATE, today)

        is_new_book = context.user_data.pop("reading_new_book", False)
        user = await db.get_user(user_id)

        if is_new_book:
            title = context.user_data.pop("pending_book_title", "Unknown")
            # Finish old book if any
            if user and user["current_book"]:
                await db.finish_book(user_id, finished_day=day_number)
            # Start new book (fetch cover in background — never blocks)
            cover_url = await fetch_book_cover(title)
            await db.set_current_book(
                user_id, title, started_day=day_number, cover_url=cover_url
            )
        else:
            title = (user["current_book"] if user else None) or "Unknown"
            context.user_data.pop("pending_book_title", None)

        just_completed = await db.log_reading(user_id, day_number, title, takeaway)
        await db.log_event(user_id, user["name"] if user else None, "reading_log", f"book={title}")

        name = user["name"] if user else update.effective_user.first_name
        await update.message.reply_text(
            f"Reading logged! 📖 Your takeaway from \"{title}\" will show up in tonight's recap."
        )

        # Refresh the daily card
        await refresh_card(context, day_number)

        if just_completed:
            await check_first_completion(context, name, day_number)

        return True

    return False


async def reread_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /reread -- allow updating today's reading entry."""
    db = context.bot_data["db"]
    today = today_et()
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
            await update.message.reply_text(f'you\'re reading "{user["current_book"]}". to change it: /setbook <new title>')
        else:
            context.user_data["awaiting_book_title"] = True
            context.user_data["reading_new_book"] = True
            await update.message.reply_text("what book are you reading? type the title:")
        return

    title = " ".join(context.args)
    day = max(get_day_number(CHALLENGE_START_DATE, today_et()), 1)

    if user["current_book"]:
        await db.finish_book(update.effective_user.id, finished_day=day)

    cover_url = await fetch_book_cover(title)
    await db.set_current_book(
        update.effective_user.id, title, started_day=day, cover_url=cover_url
    )

    if cover_url:
        await update.message.reply_photo(
            photo=cover_url, caption=f'Book set to "{title}" 📖'
        )
    else:
        await update.message.reply_text(f'Book set to "{title}" 📖')


async def setdiet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setdiet — set your diet plan. Once set, it's locked for the challenge."""
    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    user = dict(user)
    existing_diet = user.get("diet_plan")

    if not context.args:
        if existing_diet:
            await update.message.reply_text(
                f'your diet is set to: "{existing_diet}"\n\n'
                f"DM me what you eat throughout the day and i'll track it for you.\n\n"
                f"if you made an error, use /setdiet <corrected plan>"
            )
        else:
            context.user_data["awaiting_diet_input"] = True
            await update.message.reply_text(
                "what's your diet plan? some examples:\n\n"
                "  high protein 170g per day\n"
                "  calorie deficit 1800 cal\n"
                "  clean eating, no processed food\n"
                "  keto, under 20g carbs\n\n"
                "type your plan below:"
            )
        return

    plan = " ".join(context.args)

    if existing_diet:
        await update.message.reply_text(
            f'diet updated from "{existing_diet}" to "{plan}"\n\n'
            f"DM me what you eat and i'll keep a running tally 🍽️"
        )
    else:
        await update.message.reply_text(
            f'diet set to "{plan}" 🍽️\n\n'
            f"just DM me what you eat throughout the day and i'll track it for you"
        )

    await db.set_diet_plan(update.effective_user.id, plan)


async def bookshelf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bookshelf — show what everyone is reading with covers and quotes."""
    db = context.bot_data["db"]
    day = max(get_day_number(CHALLENGE_START_DATE, today_et()), 1)

    checkins_raw = await db.get_all_checkins_for_day(day)
    checkins = [dict(c) for c in checkins_raw]

    reads = [c for c in checkins if c.get("reading_done") and c.get("book_title")]

    if not reads:
        # Show current books even if nobody read today
        users = await db.get_active_users()
        readers = []
        for u in users:
            u = dict(u)
            if u.get("current_book"):
                cover_url = await db.get_current_book_cover(u["telegram_id"])
                readers.append({
                    "name": u["name"],
                    "book_title": u["current_book"],
                    "takeaway": "",
                    "cover_url": cover_url,
                })
    else:
        readers = []
        for c in reads:
            cover_url = await db.get_current_book_cover(c["telegram_id"])
            readers.append({
                "name": c["name"],
                "book_title": c["book_title"],
                "takeaway": c.get("reading_takeaway", ""),
                "cover_url": cover_url,
            })

    if not readers:
        await update.message.reply_text("nobody has a book set yet")
        return

    from bot.utils.bookshelf import render_bookshelf
    buf = await render_bookshelf(readers)
    if buf:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=buf,
        )
    else:
        await update.message.reply_text("couldn't generate the bookshelf")


def get_reading_handlers() -> list:
    """Return all reading-related callback handlers."""
    return [
        CallbackQueryHandler(read_start_callback, pattern=f"^{CB_READ}$"),
        CallbackQueryHandler(read_same_callback, pattern=f"^{CB_READ_SAME}$"),
        CallbackQueryHandler(read_new_callback, pattern=f"^{CB_READ_NEW}$"),
        CommandHandler("reread", reread_command),
        CommandHandler("setbook", setbook_command),
        CommandHandler("setdiet", setdiet_command),
        CommandHandler("bookshelf", bookshelf_command),
    ]
