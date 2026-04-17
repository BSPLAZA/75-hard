"""Rich DM onboarding flow — the front door to 75 Hard."""

import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from datetime import timedelta

from bot.config import (
    ADMIN_USER_ID,
    CHALLENGE_DAYS,
    CHALLENGE_START_DATE,
    PARTICIPANTS,
)
from bot.utils.books import fetch_book_cover
from bot.utils.progress import today_et, get_day_number
from bot.templates.messages import (
    WELCOME_ALL_REGISTERED,
    WELCOME_GROUP,
)

# Conversation states
AWAITING_NAME = 0
AWAITING_COMMITMENT = 1
AWAITING_DIET = 2
AWAITING_BOOK = 3
AWAITING_PAYMENT = 4

# Callback data
CB_LOCKED_IN = "onboard_locked_in"
CB_NOT_FOR_ME = "onboard_not_for_me"
CB_BOOK_LATER = "onboard_book_later"
CB_PAID = "onboard_paid"

# Users who don't need to pay
ALREADY_PAID = ["Yumna"]
ORGANIZER = "Bryan"

VENMO_USERNAME = "bryanedit"
BUY_IN = 75


def _fuzzy_match(name: str, candidates: list[str]) -> str | None:
    name_lower = name.strip().lower()
    best_match, best_ratio = None, 0.0
    for c in candidates:
        ratio = SequenceMatcher(None, name_lower, c.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = c
    return best_match if best_ratio >= 0.5 else None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if user and user["dm_registered"]:
        await update.message.reply_text(
            f"You're already registered, {user['name']}! "
            f"Sit tight — you'll get a group invite once everything is set."
        )
        return ConversationHandler.END

    welcome = (
        "🔥 LOCKED IN — 75 HARD 🔥\n"
        "\n"
        "Welcome to the hardest thing you'll do this year.\n"
        "\n"
        "75 days. 5 daily tasks. No excuses. No exceptions.\n"
        "Miss one task on any day and you're out.\n"
        "\n"
        "💰 $75 buy-in. Winners split the pot from those\n"
        "who don't make it. If everyone finishes — everyone\n"
        "gets their money back. Respect.\n"
        "\n"
        "Let's see if you've got what it takes.\n"
        "\n"
        "What's your name?"
    )

    await update.message.reply_text(welcome)
    return AWAITING_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    typed_name = update.message.text.strip()
    matched = _fuzzy_match(typed_name, PARTICIPANTS)

    if not matched:
        # Capture identity even on rejection so we can DM the user later if Bryan
        # adds them to the roster — otherwise we lose their telegram_id forever.
        u = update.effective_user
        logger.warning(
            "ONBOARDING_NAME_REJECTED typed=%r chat_id=%d username=%s first=%s last=%s",
            typed_name,
            u.id,
            u.username or "",
            u.first_name or "",
            u.last_name or "",
        )
        try:
            await db.log_event(
                u.id,
                u.first_name or u.username or "",
                "onboarding_name_rejected",
                f"typed={typed_name!r} username={u.username or ''}",
            )
        except Exception:
            pass
        await update.message.reply_text(
            "Hmm, I don't see that name on the list.\n\n"
            "The current squad is: " + ", ".join(PARTICIPANTS) + "\n\n"
            "If that's you, try typing just your first name. "
            "If you're new, ask Bryan to add you."
        )
        return AWAITING_NAME

    existing = await db.get_user_by_name(matched)
    if existing and existing["dm_registered"]:
        await update.message.reply_text(f"{matched} is already registered by someone else.")
        return ConversationHandler.END

    context.user_data["onboard_name"] = matched

    rules = (
        f"Hey {matched} 👊\n"
        "\n"
        "📋 THE RULES\n"
        "\n"
        "Every single day for 75 days:\n"
        "\n"
        "🏋️  Two workouts (one indoor, one outdoor)\n"
        "💧  Drink a gallon of water (16 cups)\n"
        "🍽️  Follow your diet (no alcohol, no cheat meals)\n"
        "📖  Read 10 pages of non-fiction\n"
        "📸  Take a progress photo\n"
        "\n"
        "📅  April 15 → June 28, 2026\n"
        "\n"
        "⚡ Note: As long as you're aligned with the core\n"
        "spirit of the rules, you're good. We'll finalize\n"
        "the details together in the group — things like\n"
        "workout duration (30 vs 45 min) and buy-in amount\n"
        "are still open for discussion.\n"
        "\n"
        "🤖 HOW IT WORKS\n"
        "\n"
        "You'll track everything through me right here in\n"
        "Telegram. Every morning I drop a daily card in the\n"
        "group chat. Tap buttons to log your tasks — water,\n"
        "workouts, reading, photos, diet. The card updates\n"
        "live so everyone sees each other's progress.\n"
        "\n"
        "No apps to download. No spreadsheets. Just tap and go.\n"
        "\n"
        "💰 STAKES\n"
        "\n"
        "$75 buy-in. If you fail on Day X, you get $X back.\n"
        "The rest goes to the prize pool for the survivors.\n"
        "\n"
        "If everyone finishes — everyone gets their $75 back.\n"
        "That would be legendary.\n"
    )

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔒 I'm locked in", callback_data=CB_LOCKED_IN),
        InlineKeyboardButton("Not for me", callback_data=CB_NOT_FOR_ME),
    ]])

    await update.message.reply_text(rules, reply_markup=buttons)
    return AWAITING_COMMITMENT


async def commitment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    matched = context.user_data.get("onboard_name")
    if not matched:
        await query.edit_message_text("Something went wrong. Send /start to try again.")
        return ConversationHandler.END

    if query.data == CB_NOT_FOR_ME:
        await query.edit_message_text(
            "No worries — respect for being honest.\n\n"
            "If you change your mind before April 15, just send /start again."
        )
        context.user_data.pop("onboard_name", None)
        return ConversationHandler.END

    # Register them
    db = context.bot_data["db"]
    existing = await db.get_user_by_name(matched)
    if existing:
        await db.update_telegram_id(matched, query.from_user.id)
    else:
        await db.add_user(query.from_user.id, matched)
        await db.register_dm(query.from_user.id)

    # If they're joining after Day 1, anchor their personal Day 1 to today (in
    # global day numbering) so we can track their 75-day window separately.
    current_day = get_day_number(CHALLENGE_START_DATE, today_et())
    if current_day > 1:
        user_now = await db.get_user_by_name(matched)
        if user_now:
            await db.set_user_start_day(user_now["telegram_id"], current_day)

    # Ask about diet
    await query.edit_message_text(
        "🍽️ WHAT'S YOUR DIET?\n"
        "\n"
        "One of the rules is to follow a diet for all 75 days.\n"
        "You choose what that means for you. Some examples:\n"
        "\n"
        "• High protein (150g+ per day)\n"
        "• Keto / low carb\n"
        "• Calorie deficit (e.g. 1800 cal/day)\n"
        "• Clean eating (no processed food)\n"
        "• Vegetarian / vegan\n"
        "• No sugar\n"
        "• Custom (describe your own)\n"
        "\n"
        "The only hard rules: no alcohol and no cheat meals.\n"
        "\n"
        "Type your diet plan below:"
    )
    return AWAITING_DIET


async def receive_diet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    matched = context.user_data.get("onboard_name")
    diet = update.message.text.strip()

    user = await db.get_user_by_name(matched)
    if user:
        await db.set_diet_plan(user["telegram_id"], diet)

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("I'll decide later", callback_data=CB_BOOK_LATER),
    ]])

    await update.message.reply_text(
        f"Got it — \"{diet}\" 💪\n"
        "\n"
        "📖 WHAT BOOK ARE YOU STARTING WITH?\n"
        "\n"
        "You'll read 10 pages of non-fiction every day.\n"
        "What's your first book going to be?\n"
        "\n"
        "Type the title below, or tap the button if you\n"
        "haven't picked one yet (you can always update later\n"
        "by DMing me).",
        reply_markup=buttons,
    )
    return AWAITING_BOOK


async def receive_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    matched = context.user_data.get("onboard_name")
    book = update.message.text.strip()

    user = await db.get_user_by_name(matched)
    if user:
        cover_url = await fetch_book_cover(book)
        await db.set_current_book(
            user["telegram_id"], book, started_day=1, cover_url=cover_url
        )

    context.user_data["onboard_book"] = book
    return await _handle_payment_step(update.effective_user.id, matched, context)


async def book_later_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matched = context.user_data.get("onboard_name")
    context.user_data["onboard_book"] = None
    return await _handle_payment_step(query.from_user.id, matched, context)


async def _handle_payment_step(user_id, matched, context):
    """Route to payment or straight to invite based on user type."""
    if matched == ORGANIZER or matched in ALREADY_PAID:
        # Skip payment — go straight to welcome + invite
        await _finalize_and_invite(user_id, matched, context)
        return ConversationHandler.END
    else:
        # Show Venmo step with self-confirm
        venmo_note = "75 Hard - Locked In"
        venmo_deeplink = (
            f"https://venmo.com/{VENMO_USERNAME}"
            f"?txn=pay&amount={BUY_IN}&note={venmo_note.replace(' ', '%20')}"
        )

        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ I've sent the payment", callback_data=CB_PAID),
        ]])

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "💰 LAST STEP — BUY-IN\n"
                "\n"
                f"Send ${BUY_IN} to @{VENMO_USERNAME} on Venmo:\n"
                "\n"
                f"👉 {venmo_deeplink}\n"
                "\n"
                f"Note: \"{venmo_note}\"\n"
                "\n"
                "Once you've sent it, tap the button below."
            ),
            reply_markup=buttons,
        )
        return AWAITING_PAYMENT


async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirms they paid — send invite link."""
    query = update.callback_query
    await query.answer()

    matched = context.user_data.get("onboard_name")
    if not matched:
        await query.edit_message_text("Something went wrong. Send /start to try again.")
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user_by_name(matched)
    if user:
        await db._conn.execute(
            "UPDATE users SET paid = 1 WHERE telegram_id = ?", (user["telegram_id"],)
        )
        await db._conn.commit()

    await query.edit_message_text("💰 Payment noted — Bryan will verify.")
    await _finalize_and_invite(query.from_user.id, matched, context)
    return ConversationHandler.END


async def _finalize_and_invite(user_id, matched, context):
    """Send the final welcome + group invite link."""
    db = context.bot_data["db"]
    user = await db.get_user_by_name(matched)
    diet = user["diet_plan"] if user and user["diet_plan"] else "not set yet"
    book = user["current_book"] if user and user["current_book"] else "TBD"

    if matched == ORGANIZER:
        text = (
            "✅ YOU'RE IN, BOSS\n"
            "\n"
            f"🍽️ Diet: {diet}\n"
            f"📖 Book: {book}\n"
            "\n"
            "🔥 Let's make this legendary."
        )
    else:
        text = (
            "✅ YOU'RE IN\n"
            "\n"
            f"🍽️ Diet: {diet}\n"
            f"📖 Book: {book}\n"
            "\n"
            "🔥 See you on the other side."
        )

    await context.bot.send_message(chat_id=user_id, text=text)

    # Late-joiner disclaimer + admin alert
    start_day = (user["start_day"] if user and "start_day" in user.keys() else 1) or 1
    if start_day > 1:
        global_finish = CHALLENGE_START_DATE + timedelta(days=CHALLENGE_DAYS - 1)
        personal_start = CHALLENGE_START_DATE + timedelta(days=start_day - 1)
        personal_finish = personal_start + timedelta(days=CHALLENGE_DAYS - 1)
        offset = start_day - 1
        disclaimer = (
            "⚠️ HEADS UP — LATE START\n"
            "\n"
            f"You're joining {offset} day{'s' if offset != 1 else ''} into the challenge.\n"
            "To do the full 75 days, you'll keep going for\n"
            f"{offset} day{'s' if offset != 1 else ''} after the group finishes.\n"
            "\n"
            f"Group finishes: {global_finish.strftime('%B %d, %Y')}\n"
            f"You finish:     {personal_finish.strftime('%B %d, %Y')}\n"
            "\n"
            f"Today is the group's Day {start_day}, but for you\n"
            "it's Day 1. The daily card and chat will show the\n"
            "group day number — just remember to count yours\n"
            f"from today ({personal_start.strftime('%B %d')}).\n"
            "\n"
            "Let's go. 💪"
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=disclaimer)
        except Exception:
            pass

        # Notify Bryan so he can manually add to the group chat
        try:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=(
                    f"🆕 LATE JOINER REGISTERED\n"
                    f"\n"
                    f"{matched} just finished onboarding (start_day={start_day}, "
                    f"finish={personal_finish.strftime('%B %d')}).\n"
                    f"\n"
                    f"Action needed: manually add @{matched.lower()} to the group chat."
                ),
            )
        except Exception:
            pass

    # Send invite link
    invite_link = context.bot_data.get("group_invite_link")
    if invite_link:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"👉 Join the group: {invite_link}",
        )

    await _update_welcome_message(context)
    await _check_all_joined(context)

    context.user_data.pop("onboard_name", None)
    context.user_data.pop("onboard_book", None)


async def _check_all_joined(context):
    """If all participants are registered AND we haven't celebrated yet, celebrate in the group.

    Gated by the all_joined_announced setting so late joiners don't re-trigger
    the fanfare every time someone new finishes onboarding.
    """
    db = context.bot_data["db"]

    already_announced = await db.get_setting("all_joined_announced")
    if already_announced:
        return

    # Skip the squad-complete celebration if the challenge is already in progress —
    # late joiners shouldn't trigger a re-announcement.
    current_day = get_day_number(CHALLENGE_START_DATE, today_et())
    if current_day > 1:
        return

    users = await db.get_all_users()
    registered = [u for u in users if u["dm_registered"]]

    if len(registered) >= len(PARTICIPANTS):
        chat_id = context.bot_data.get("group_chat_id")
        if chat_id:
            names = [u["name"] for u in sorted(registered, key=lambda x: x["name"].lower())]
            celebration = (
                "🔥🔥🔥 THE SQUAD IS COMPLETE 🔥🔥🔥\n"
                "\n"
                f"{', '.join(names[:-1])} and {names[-1]} are all in.\n"
                "\n"
                f"5 people. 75 days. ${BUY_IN * len(names)} on the line.\n"
                "\n"
                "No turning back now. 💪"
            )

            quickstart = (
                "🤖 QUICK START\n"
                "\n"
                "Every morning I'll post a daily card here.\n"
                "Tap the buttons to log your tasks:\n"
                "\n"
                "  💧  Water — tap once per cup (16 total)\n"
                "  🏋️  Workout — pick type + indoor/outdoor\n"
                "  📖  Read — I'll DM you for book + takeaway\n"
                "  📸  Photo — I'll DM you, send your pic there\n"
                "  🍽️  Diet — one tap to confirm\n"
                "\n"
                "Progress pics are saved for your transformation\n"
                "timeline at the end.\n"
                "\n"
                "Daily rhythm (all ET / PT):\n"
                "  7am ET   — new card + yesterday recap\n"
                "  9am ET   — DM if yesterday's incomplete\n"
                "  3pm ET / 12pm PT — yesterday locks\n"
                "  10pm ET / 10pm PT — final nudge in your TZ\n"
                "\n"
                "You can also DM me in plain English:\n"
                "  \"ran 3 miles outside\" / \"had 4 cups of water\"\n"
                "  \"how am i doing\" / \"undo that workout\"\n"
                "\n"
                "Check the pinned message for full rules + FAQ."
            )

            try:
                await context.bot.send_message(chat_id=chat_id, text=celebration)
                await context.bot.send_message(chat_id=chat_id, text=quickstart)
            except Exception:
                pass


async def _update_welcome_message(context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data["db"]
    welcome_msg_id = context.bot_data.get("welcome_message_id")
    chat_id = context.bot_data.get("group_chat_id")
    if not welcome_msg_id or not chat_id:
        return

    unregistered = await db.get_unregistered_names()
    all_users = await db.get_all_users()
    registered = len(all_users) - len(unregistered)

    if not unregistered:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=welcome_msg_id,
                text=WELCOME_ALL_REGISTERED,
            )
        except Exception:
            pass
    else:
        bot_info = await context.bot.get_me()
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=welcome_msg_id,
                text=WELCOME_GROUP.format(
                    bot_username=bot_info.username,
                    waiting_names=", ".join(unregistered),
                    registered=registered,
                    total=len(all_users),
                ),
            )
        except Exception:
            pass


def get_onboarding_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            AWAITING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name),
            ],
            AWAITING_COMMITMENT: [
                CallbackQueryHandler(commitment_callback, pattern=f"^{CB_LOCKED_IN}$"),
                CallbackQueryHandler(commitment_callback, pattern=f"^{CB_NOT_FOR_ME}$"),
            ],
            AWAITING_DIET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_diet),
            ],
            AWAITING_BOOK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_book),
                CallbackQueryHandler(book_later_callback, pattern=f"^{CB_BOOK_LATER}$"),
            ],
            AWAITING_PAYMENT: [
                CallbackQueryHandler(payment_callback, pattern=f"^{CB_PAID}$"),
            ],
        },
        fallbacks=[CommandHandler("start", start_command)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )
