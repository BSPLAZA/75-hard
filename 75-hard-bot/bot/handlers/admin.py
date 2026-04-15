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
from bot.jobs.scheduler import evening_scoreboard_job, nudge_job, weekly_digest_job
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

    # Generate invite link
    try:
        invite = await context.bot.create_chat_invite_link(
            chat_id=chat_id,
            name="75 Hard — Locked In",
        )
        context.bot_data["group_invite_link"] = invite.invite_link
        await update.message.reply_text(
            f"Group set! Invite link ready.\n"
            f"Use /admin_reset_day to post today's card.\n"
            f"Use /admin_confirm_payment <name> after someone pays."
        )
    except Exception:
        await update.message.reply_text(
            f"Group set! But I couldn't create an invite link.\n"
            f"Make sure I have 'Invite Users' admin permission."
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

    remaining_days = 75 - day_number
    redemption_cost = remaining_days + 50

    await update.message.reply_text(
        f"You've been eliminated on Day {day_number}. "
        f"You completed {days_completed} days. Respect.\n\n"
        f"Want back in? Type /redeem\n"
        f"Cost: ${remaining_days} (remaining days) + $50 (penalty) = ${redemption_cost}\n"
        f"The $50 goes into the prize pool. You can only redeem once."
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


# ── /redeem conversation ─────────────────────────────────────────────

REDEEM_AWAITING_CONFIRM = 10


async def redeem_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle /redeem — show cost and ask for confirmation. DMs only."""
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /redeem in DMs only.")
        return ConversationHandler.END

    db = context.bot_data["db"]
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return ConversationHandler.END

    if user["active"]:
        await update.message.reply_text("You're still active. No need to redeem.")
        return ConversationHandler.END

    if user.get("redeemed"):
        await update.message.reply_text("You've already used your one redemption. No second chances.")
        return ConversationHandler.END

    today = date.today()
    day_number = max(get_day_number(CHALLENGE_START_DATE, today), 1)
    remaining_days = 75 - day_number
    penalty = 50
    total_cost = remaining_days + penalty

    context.user_data["redeem_cost"] = total_cost
    context.user_data["redeem_penalty"] = penalty
    context.user_data["redeem_remaining"] = remaining_days

    venmo_note = "75 Hard - Redemption"
    venmo_deeplink = f"https://venmo.com/bryanedit?txn=pay&amount={total_cost}&note={venmo_note.replace(' ', '%20')}"

    await update.message.reply_text(
        f"REDEMPTION\n"
        f"\n"
        f"You failed on Day {user['failed_day']}. Here's your way back.\n"
        f"\n"
        f"Cost breakdown:\n"
        f"  ${remaining_days} for the {remaining_days} remaining days\n"
        f"  ${penalty} redemption penalty (goes to prize pool)\n"
        f"  ${total_cost} total\n"
        f"\n"
        f"Pay here: {venmo_deeplink}\n"
        f"\n"
        f"You rejoin at Day {day_number}. All tasks reset for today.\n"
        f"You only get one redemption. No third chances.\n"
        f"\n"
        f"Type REDEEM after you've sent payment."
    )
    return REDEEM_AWAITING_CONFIRM


async def redeem_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Process the REDEEM confirmation."""
    text = update.message.text.strip()

    if text != "REDEEM":
        await update.message.reply_text("Cancelled. Type /redeem to try again.")
        return ConversationHandler.END

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    name = user["name"] if user else update.effective_user.first_name
    penalty = context.user_data.pop("redeem_penalty", 50)
    total_cost = context.user_data.pop("redeem_cost", 0)

    # Reactivate
    await db.redeem_user(user_id, fee=total_cost)

    # Create today's checkin
    today = date.today()
    day_number = max(get_day_number(CHALLENGE_START_DATE, today), 1)
    await db.create_checkin(user_id, day_number, today.isoformat())

    await update.message.reply_text(
        f"You're back. Day {day_number} starts now.\n"
        f"\n"
        f"${penalty} added to the prize pool. Don't waste this."
    )

    # Announce in group
    group_chat_id = context.bot_data.get("group_chat_id")
    if group_chat_id:
        active_users = await db.get_active_users()
        try:
            await context.bot.send_message(
                chat_id=group_chat_id,
                text=(
                    f"{name} just redeemed back into the challenge. "
                    f"Paid ${total_cost} to get back in. ${penalty} added to the prize pool.\n"
                    f"\n"
                    f"{len(active_users)} standing."
                ),
            )
        except Exception:
            pass

        await refresh_card(context, day_number)

    context.user_data.pop("redeem_remaining", None)
    return ConversationHandler.END


# ── Handler exports ───────────────────────────────────────────────────


async def admin_test_recap_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Trigger the evening scoreboard recap for testing."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    try:
        from bot.utils.image_generator import render_recap_image
        from bot.utils.progress import get_day_number, is_all_complete
        from bot.config import CHALLENGE_START_DATE, CHALLENGE_DAYS

        db = context.bot_data["db"]
        chat_id = context.bot_data.get("group_chat_id") or update.effective_chat.id
        day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)

        checkins_raw = await db.get_all_checkins_for_day(day)
        checkins = [dict(c) for c in checkins_raw]

        if not checkins:
            await update.message.reply_text("No checkins for today.")
            return

        complete = [c for c in checkins if is_all_complete(c)]
        remaining = CHALLENGE_DAYS - day

        image_buf = render_recap_image(day, checkins, CHALLENGE_DAYS)

        # Build caption
        reads = [
            (c["name"], c.get("book_title"), c.get("reading_takeaway"))
            for c in checkins
            if c["reading_done"] and c.get("book_title")
        ]
        caption_parts = []
        if reads:
            caption_parts.append("📖  What we read today\n")
            for name, book, takeaway in reads:
                caption_parts.append(f"{name} — {book}")
                if takeaway:
                    caption_parts.append(f'"{takeaway}"\n')
        caption_parts.append(f"{len(complete)}/{len(checkins)} completed · {remaining} days to go")
        caption = "\n".join(caption_parts)

        await context.bot.send_photo(chat_id=chat_id, photo=image_buf, caption=caption[:1024])
    except Exception as e:
        import traceback
        await update.message.reply_text(f"Recap failed:\n{traceback.format_exc()[-500:]}")


async def admin_test_morning_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Test the AI morning message."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    try:
        from bot.utils.luke_ai import generate_morning_message
        from bot.utils.progress import get_day_number, is_all_complete, get_missing_tasks
        from bot.config import CHALLENGE_START_DATE

        db = context.bot_data["db"]
        day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)

        # For testing: use today's checkins AS yesterday's data so we can see
        # the AI react to actual DB state
        checkins_raw = await db.get_all_checkins_for_day(day)
        checkins = [dict(c) for c in checkins_raw]

        yesterday_summary = None
        if checkins:
            completed = [c["name"] for c in checkins if is_all_complete(c)]
            incomplete = [(c["name"], get_missing_tasks(c)) for c in checkins if not is_all_complete(c)]
            completers = sorted([c for c in checkins if c.get("completed_at")], key=lambda c: c["completed_at"] or "")
            first = completers[0]["name"] if completers else None
            books = [(c["name"], c.get("book_title")) for c in checkins if c.get("book_title")]
            yesterday_summary = {
                "day": day,
                "completed": completed,
                "incomplete": incomplete,
                "first_finisher": first,
                "books": books,
            }

        active = await db.get_active_users()
        all_users = await db.get_all_users()
        msg = await generate_morning_message(day + 1, len(active), len(all_users), yesterday_summary)
        await update.message.reply_text(msg or "AI generation failed - check API key.")
    except Exception as e:
        import traceback
        await update.message.reply_text(f"Failed:\n{traceback.format_exc()[-500:]}")


async def admin_test_nudge_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Trigger the 11 PM nudge DMs for testing."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    await nudge_job(context)
    await update.message.reply_text("Nudge triggered.")


async def admin_test_digest_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Preview the weekly Sunday digest."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    try:
        from bot.jobs.scheduler import _gather_weekly_data
        from bot.utils.image_generator import render_weekly_digest_image
        from bot.utils.luke_ai import generate_weekly_reflection
        from bot.config import CHALLENGE_START_DATE
        from bot.utils.progress import get_day_number

        db = context.bot_data["db"]
        chat_id = update.effective_chat.id
        current_day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)

        data = await _gather_weekly_data(db, current_day)

        if not data["user_stats"]:
            await update.message.reply_text("No checkin data found for the past 7 days.")
            return

        # Generate digest image
        image_buf = render_weekly_digest_image(
            week_number=data["week_number"],
            user_stats=data["user_stats"],
            total_workouts=data["total_workouts"],
            total_water=data["total_water"],
            total_reading_days=data["total_reading_days"],
            first_finisher_name=data["first_finisher_name"],
            first_finisher_count=data["first_finisher_count"],
        )

        # Build reading log
        reading_parts = []
        if data["reading_log"]:
            reading_parts.append("📖 This week's reading\n")
            for entry in data["reading_log"]:
                books_str = ", ".join(
                    f'"{b["title"]}" ({b["days"]} day{"s" if b["days"] != 1 else ""})'
                    for b in entry["books"]
                )
                reading_parts.append(f'{entry["name"]} — {books_str}')

        reading_text = "\n".join(reading_parts) if reading_parts else ""

        # Generate AI reflection
        reflection = await generate_weekly_reflection(
            week_number=data["week_number"],
            user_stats=data["user_stats"],
            reading_log=data["reading_log"],
        )

        # Build caption
        caption_parts = []
        if reading_text:
            caption_parts.append(reading_text)
        if reflection:
            caption_parts.append(f"\n{reflection}")

        caption = "\n".join(caption_parts) if caption_parts else f"Week {data['week_number']} digest"

        # Send to current chat
        if len(caption) > 1024:
            await context.bot.send_photo(chat_id=chat_id, photo=image_buf)
            await context.bot.send_message(chat_id=chat_id, text=caption)
        else:
            await context.bot.send_photo(chat_id=chat_id, photo=image_buf, caption=caption)

    except Exception as e:
        import traceback
        await update.message.reply_text(f"Digest failed:\n{traceback.format_exc()[-500:]}")


async def admin_confirm_payment_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Confirm a user's payment and send them the group invite link."""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /admin_confirm_payment <name>")
        return

    name = " ".join(context.args)
    db = context.bot_data["db"]
    user = await db.get_user_by_name(name)

    if not user:
        await update.message.reply_text(f"No user named '{name}' found.")
        return

    if not user["dm_registered"]:
        await update.message.reply_text(f"{name} hasn't registered with the bot yet.")
        return

    # Mark as paid
    await db._conn.execute(
        "UPDATE users SET paid = 1 WHERE telegram_id = ?", (user["telegram_id"],)
    )
    await db._conn.commit()

    # Send invite link
    invite_link = context.bot_data.get("group_invite_link")
    if invite_link:
        try:
            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text=(
                    "✅ Payment confirmed!\n"
                    "\n"
                    f"👉 Join the group: {invite_link}\n"
                    "\n"
                    "See you in there 🔥"
                ),
            )
            await update.message.reply_text(f"Payment confirmed for {name}. Invite link sent.")
        except Exception as e:
            await update.message.reply_text(f"Payment confirmed for {name} but couldn't DM them: {e}")
    else:
        await update.message.reply_text(
            f"Payment confirmed for {name}, but no group invite link yet. "
            "Add me to a group first and I'll generate one."
        )


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
        CommandHandler("admin_test_morning", admin_test_morning_command),
        CommandHandler("admin_test_nudge", admin_test_nudge_command),
        CommandHandler("admin_test_digest", admin_test_digest_command),
        CommandHandler("admin_feedback", admin_feedback_command),
        CommandHandler("admin_resolve", admin_resolve_command),
        CommandHandler("admin_announce", admin_announce_command),
        CommandHandler("admin_reset_db", admin_reset_db_command),
        CommandHandler("admin_confirm_payment", admin_confirm_payment_command),
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


def get_redeem_handler() -> ConversationHandler:
    """Return the /redeem ConversationHandler."""
    return ConversationHandler(
        entry_points=[CommandHandler("redeem", redeem_start)],
        states={
            REDEEM_AWAITING_CONFIRM: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, redeem_confirm
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", fail_cancel)],
    )
