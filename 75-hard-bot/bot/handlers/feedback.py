"""Feedback handlers -- /feedback, /bug, /suggest commands."""

from datetime import date

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from bot.config import CHALLENGE_START_DATE
from bot.templates.messages import FEEDBACK_CONFIRM
from bot.utils.progress import get_day_number


async def _handle_feedback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, fb_type: str
) -> None:
    """Shared handler for all feedback commands."""
    if not context.args:
        await update.message.reply_text(f"Usage: /{fb_type} <your message>")
        return

    db = context.bot_data["db"]
    text = " ".join(context.args)
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    await db.add_feedback(
        update.effective_user.id, fb_type, text, f"day {day}"
    )
    await update.message.reply_text(FEEDBACK_CONFIRM.format(type=fb_type))


async def feedback_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /feedback."""
    await _handle_feedback(update, context, "feedback")


async def bug_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /bug."""
    await _handle_feedback(update, context, "bug")


async def suggest_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /suggest."""
    await _handle_feedback(update, context, "suggest")


def get_feedback_handlers() -> list:
    """Return the three feedback command handlers."""
    return [
        CommandHandler("feedback", feedback_command),
        CommandHandler("bug", bug_command),
        CommandHandler("suggest", suggest_command),
    ]
