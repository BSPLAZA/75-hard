"""Rich DM onboarding flow — the front door to 75 Hard."""

from difflib import SequenceMatcher

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import PARTICIPANTS
from bot.templates.messages import (
    WELCOME_ALL_REGISTERED,
    WELCOME_GROUP,
)

# Conversation states
AWAITING_NAME = 0
AWAITING_COMMITMENT = 1
AWAITING_DIET = 2
AWAITING_BOOK = 3

# Callback data
CB_LOCKED_IN = "onboard_locked_in"
CB_NOT_FOR_ME = "onboard_not_for_me"
CB_BOOK_LATER = "onboard_book_later"

# Users who don't need to pay
ALREADY_PAID = ["Yumna"]
ORGANIZER = "Bryan"

VENMO_USERNAME = "BrianEdit"
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
    """Handle /start in DMs — big welcome, then ask for name."""
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if user and user["dm_registered"]:
        await update.message.reply_text(
            f"You're already registered, {user['name']}! "
            f"Sit tight — Bryan will add you to the group when everyone's in."
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
    """Match name, then show rules + ask for commitment."""
    db = context.bot_data["db"]
    typed_name = update.message.text.strip()
    matched = _fuzzy_match(typed_name, PARTICIPANTS)

    if not matched:
        await update.message.reply_text(
            "Hmm, I don't see that name on the list.\n\n"
            "The current squad is: " + ", ".join(PARTICIPANTS) + "\n\n"
            "If that's you, try typing just your first name. "
            "If you're new, ask Bryan to add you."
        )
        return AWAITING_NAME

    # Check if already claimed
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
    """User tapped 'I'm locked in' or 'Not for me'."""
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

    # They're in — register them
    db = context.bot_data["db"]
    existing = await db.get_user_by_name(matched)
    if existing:
        await db.update_telegram_id(matched, query.from_user.id)
    else:
        await db.add_user(query.from_user.id, matched)
        await db.register_dm(query.from_user.id)

    # Now ask about diet
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
    """User typed their diet plan — save it, ask about book."""
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
    """User typed their book title — save it, show final confirmation."""
    db = context.bot_data["db"]
    matched = context.user_data.get("onboard_name")
    book = update.message.text.strip()

    user = await db.get_user_by_name(matched)
    if user:
        await db.set_current_book(user["telegram_id"], book, started_day=1)

    await _show_final_confirmation(update.effective_user.id, matched, context, book=book)
    context.user_data.pop("onboard_name", None)
    return ConversationHandler.END


async def book_later_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped 'I'll decide later' for book."""
    query = update.callback_query
    await query.answer()
    matched = context.user_data.get("onboard_name")

    await _show_final_confirmation(query.from_user.id, matched, context, book=None, edit_message=query)
    context.user_data.pop("onboard_name", None)
    return ConversationHandler.END


async def _show_final_confirmation(user_id, matched, context, book=None, edit_message=None):
    """Show the final confirmation with payment info if needed."""
    db = context.bot_data["db"]
    user = await db.get_user_by_name(matched)
    diet = user["diet_plan"] if user else "not set"

    book_line = f'📖 Starting with: "{book}"' if book else "📖 Book: TBD (DM me when you pick one)"

    if matched == ORGANIZER:
        text = (
            "✅ YOU'RE IN, BOSS\n"
            "\n"
            f"🍽️ Diet: {diet}\n"
            f"{book_line}\n"
            "\n"
            "You're registered and running this thing.\n"
            "Once everyone else registers and pays, create\n"
            "the group and add me. I'll handle the rest.\n"
            "\n"
            "🔥 Let's make this legendary."
        )
    elif matched in ALREADY_PAID:
        text = (
            "✅ YOU'RE IN\n"
            "\n"
            f"🍽️ Diet: {diet}\n"
            f"{book_line}\n"
            "\n"
            f"Hey {matched} — your payment is already confirmed. 💰\n"
            "\n"
            "Bryan will add you to the group shortly where\n"
            "you'll align on final details before Day 1.\n"
            "\n"
            "🔥 See you on the other side."
        )
    else:
        venmo_note = "75 Hard - Locked In"
        venmo_deeplink = (
            f"https://venmo.com/{VENMO_USERNAME}"
            f"?txn=pay&amount={BUY_IN}&note={venmo_note.replace(' ', '%20')}"
        )
        text = (
            "💰 BUY-IN: $75\n"
            "\n"
            f"🍽️ Diet: {diet}\n"
            f"{book_line}\n"
            "\n"
            f"Last step — send ${BUY_IN} to @{VENMO_USERNAME} on Venmo.\n"
            "\n"
            f"👉 Pay here: {venmo_deeplink}\n"
            "\n"
            f"Note: \"{venmo_note}\"\n"
            "\n"
            "Once Bryan confirms your payment, you'll be\n"
            "added to the group where we'll align on final\n"
            "details like workout duration and kick this off.\n"
            "\n"
            "🔥 See you on the other side."
        )

    if edit_message:
        await edit_message.edit_message_text(text)
    else:
        await context.bot.send_message(chat_id=user_id, text=text)

    await _update_welcome_message(context)


async def _update_welcome_message(context: ContextTypes.DEFAULT_TYPE):
    """Edit the group welcome message to reflect who has registered."""
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
    """Return the ConversationHandler for DM registration."""
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
        },
        fallbacks=[CommandHandler("start", start_command)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )
