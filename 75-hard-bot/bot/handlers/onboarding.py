"""Registration flow for DMs. Uses ConversationHandler."""

from difflib import SequenceMatcher

from telegram import Update
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
    DM_REGISTRATION_ALREADY,
    DM_REGISTRATION_ASK_NAME,
    DM_REGISTRATION_NOT_FOUND,
    DM_REGISTRATION_SUCCESS,
    WELCOME_ALL_REGISTERED,
    WELCOME_GROUP,
)

AWAITING_NAME = 0


def _fuzzy_match(name: str, candidates: list[str]) -> str | None:
    """Return the best fuzzy match from *candidates*, or None if nothing scores >= 0.5."""
    name_lower = name.strip().lower()
    best_match, best_ratio = None, 0.0
    for c in candidates:
        ratio = SequenceMatcher(None, name_lower, c.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = c
    return best_match if best_ratio >= 0.5 else None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start in DMs -- kick off registration."""
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if user and user["dm_registered"]:
        await update.message.reply_text(
            DM_REGISTRATION_ALREADY.format(name=user["name"])
        )
        return ConversationHandler.END

    await update.message.reply_text(DM_REGISTRATION_ASK_NAME)
    return AWAITING_NAME


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Match the typed name against the participant list."""
    db = context.bot_data["db"]
    typed_name = update.message.text.strip()
    matched = _fuzzy_match(typed_name, PARTICIPANTS)

    if not matched:
        await update.message.reply_text(DM_REGISTRATION_NOT_FOUND)
        return ConversationHandler.END

    # Check if name already claimed by another telegram user
    existing = await db.get_user_by_name(matched)
    if existing and existing["dm_registered"]:
        await update.message.reply_text(f"{matched} is already registered.")
        return ConversationHandler.END

    # Either update an existing row or create a new one
    if existing:
        await db.update_telegram_id(matched, update.effective_user.id)
    else:
        await db.add_user(update.effective_user.id, matched)
        await db.register_dm(update.effective_user.id)

    await update.message.reply_text(DM_REGISTRATION_SUCCESS.format(name=matched))

    # Update the welcome message in the group chat
    await _update_welcome_message(context)
    return ConversationHandler.END


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
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)
            ],
        },
        fallbacks=[CommandHandler("start", start_command)],
        per_chat=True,
        per_user=True,
    )
