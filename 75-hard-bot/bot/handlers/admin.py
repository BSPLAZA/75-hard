"""Admin-only commands and the /fail self-elimination flow."""

from datetime import date

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import ADMIN_USER_ID, CHALLENGE_START_DATE
from bot.handlers.daily_card import post_daily_card, refresh_card
from bot.jobs.scheduler import evening_scoreboard_job, nudge_job
from bot.templates.messages import FAIL_CONFIRM, FAIL_DONE
from bot.utils.progress import get_day_number

# ConversationHandler states for /fail
FAIL_AWAITING_CONFIRM = 0


def _is_admin(user_id: int) -> bool:
    """Check if the user is the bot admin."""
    return user_id == ADMIN_USER_ID


# ── Admin commands ────────────────────────────────────────────────────


async def admin_status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show all users and their status."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    db = context.bot_data["db"]
    users = await db.get_all_users()

    if not users:
        await update.message.reply_text("No users registered.")
        return

    lines = ["User Status:\n"]
    for u in users:
        status = "active" if u["active"] else f"failed day {u['failed_day']}"
        paid = "paid" if u["paid"] else "unpaid"
        dm = "DM" if u["dm_registered"] else "no DM"
        lines.append(f"  {u['name']} — {status} / {paid} / {dm}")

    await update.message.reply_text("\n".join(lines))


async def admin_set_group_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Run this in a group to set it as the active group for the bot."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    chat_id = update.effective_chat.id
    context.bot_data["group_chat_id"] = chat_id
    await update.message.reply_text(
        f"Group set! Chat ID: {chat_id}\n\n"
        f"Luke is now active in this group. Use /admin_reset_day to post today's card."
    )


async def admin_reset_day_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Repost today's daily card."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    group_chat_id = context.bot_data.get("group_chat_id")
    if not group_chat_id:
        await update.message.reply_text(
            "No group configured! Add me to a group and type /admin_set_group there first."
        )
        return

    await post_daily_card(context, chat_id=group_chat_id)
    await update.message.reply_text("Daily card reposted.")


async def admin_feedback_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show unresolved feedback. Optional filter: /admin_feedback bugs"""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    db = context.bot_data["db"]
    args = context.args or []

    # Map shorthand filters to types
    type_map = {"bugs": "bug", "suggestions": "suggest", "feedback": "feedback"}
    fb_type = None
    if args:
        fb_type = type_map.get(args[0].lower(), args[0].lower())

    items = await db.get_feedback(fb_type=fb_type)

    if not items:
        await update.message.reply_text("No unresolved feedback.")
        return

    lines = ["Unresolved feedback:\n"]
    for item in items:
        lines.append(
            f"  [{item['id']}] ({item['type']}) {item['text']}"
            f" — {item['context'] or 'no context'}"
        )

    await update.message.reply_text("\n".join(lines))


async def admin_resolve_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Mark feedback as resolved: /admin_resolve [id] [status]"""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    db = context.bot_data["db"]
    args = context.args or []

    if len(args) < 1:
        await update.message.reply_text(
            "Usage: /admin_resolve <id> [status]\n"
            "Status options: acknowledged, implemented, wontfix, resolved"
        )
        return

    try:
        fb_id = int(args[0])
    except ValueError:
        await update.message.reply_text("ID must be a number.")
        return

    valid_statuses = {"acknowledged", "implemented", "wontfix", "resolved"}
    status = args[1].lower() if len(args) > 1 else "resolved"
    if status not in valid_statuses:
        await update.message.reply_text(
            f"Invalid status. Choose from: {', '.join(sorted(valid_statuses))}"
        )
        return

    await db.resolve_feedback(fb_id, status=status)
    await update.message.reply_text(f"Feedback #{fb_id} marked as {status}.")


async def admin_announce_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Post a message to the group as the bot."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /admin_announce <message>")
        return

    group_chat_id = context.bot_data.get("group_chat_id")
    if not group_chat_id:
        await update.message.reply_text("Group chat ID not configured.")
        return

    message = " ".join(context.args)
    await context.bot.send_message(chat_id=group_chat_id, text=message)
    await update.message.reply_text("Announcement sent.")


# ── /fail conversation ────────────────────────────────────────────────


async def fail_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /fail -- ask for confirmation. DMs only."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /fail in DMs only.")
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return ConversationHandler.END

    if not user["active"]:
        await update.message.reply_text("You've already been eliminated.")
        return ConversationHandler.END

    await update.message.reply_text(FAIL_CONFIRM)
    return FAIL_AWAITING_CONFIRM


async def fail_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Process the CONFIRM response to /fail."""
    text = update.message.text.strip()

    if text != "CONFIRM":
        await update.message.reply_text("Cancelled. You're still in!")
        return ConversationHandler.END

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    name = user["name"] if user else update.effective_user.first_name

    today = date.today()
    day_number = get_day_number(CHALLENGE_START_DATE, today)
    days_completed = max(0, day_number - 1)

    # Eliminate the user
    await db.eliminate_user(user_id, failed_day=day_number)

    await update.message.reply_text(
        f"You've been eliminated on Day {day_number}. "
        f"You completed {days_completed} days. Respect."
    )

    # Post farewell to the group
    group_chat_id = context.bot_data.get("group_chat_id")
    if group_chat_id:
        active_users = await db.get_active_users()
        active_count = len(active_users)
        prize_pool = active_count * 75
        returned = days_completed  # $1 per day completed
        remaining = 75 - returned

        try:
            await context.bot.send_message(
                chat_id=group_chat_id,
                text=FAIL_DONE.format(
                    name=name,
                    days=days_completed,
                    returned=returned,
                    remaining=remaining,
                    pool=prize_pool,
                    active=active_count,
                ),
            )
        except Exception:
            pass

        # Refresh the daily card
        await refresh_card(context, day_number)

    return ConversationHandler.END


async def fail_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /cancel during the fail flow."""
    await update.message.reply_text("Cancelled. You're still in!")
    return ConversationHandler.END


# ── Handler exports ───────────────────────────────────────────────────


async def admin_test_recap_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Trigger the evening scoreboard recap for testing."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    await evening_scoreboard_job(context)
    await update.message.reply_text("Recap triggered.")


async def admin_test_nudge_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Trigger the 11 PM nudge DMs for testing."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    await nudge_job(context)
    await update.message.reply_text("Nudge triggered.")


async def admin_reset_db_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Wipe all checkins, cards, and books — fresh start for testing."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    db = context.bot_data["db"]
    # Backup first
    import shutil
    from bot.config import DATABASE_PATH
    shutil.copy(DATABASE_PATH, DATABASE_PATH + ".backup")

    await db._conn.execute("DELETE FROM daily_checkins")
    await db._conn.execute("DELETE FROM daily_cards")
    await db._conn.execute("DELETE FROM books")
    await db._conn.execute("DELETE FROM feedback")
    await db._conn.commit()
    await update.message.reply_text("Database reset. Backup saved. User registrations preserved.")


def get_admin_handlers() -> list:
    """Return all admin command handlers."""
    return [
        CommandHandler("admin_set_group", admin_set_group_command),
        CommandHandler("admin_status", admin_status_command),
        CommandHandler("admin_reset_day", admin_reset_day_command),
        CommandHandler("admin_test_recap", admin_test_recap_command),
        CommandHandler("admin_test_nudge", admin_test_nudge_command),
        CommandHandler("admin_feedback", admin_feedback_command),
        CommandHandler("admin_resolve", admin_resolve_command),
        CommandHandler("admin_announce", admin_announce_command),
        CommandHandler("admin_reset_db", admin_reset_db_command),
    ]


def get_fail_handler() -> ConversationHandler:
    """Return the /fail ConversationHandler."""
    return ConversationHandler(
        entry_points=[CommandHandler("fail", fail_start)],
        states={
            FAIL_AWAITING_CONFIRM: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, fail_confirm
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", fail_cancel)],
    )
