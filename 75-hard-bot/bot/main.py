"""Main entry point for the 75 Hard Telegram bot."""

import logging
import random
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    ChatMemberHandler,
    MessageHandler,
    filters,
)

from bot.config import BOT_TOKEN, DATABASE_PATH, GROUP_CHAT_ID, PARTICIPANTS
from bot.database import Database
from bot.handlers.admin import get_admin_handlers, get_fail_handler, get_redeem_handler
from bot.handlers.daily_card import get_card_command_handler
from bot.handlers.diet import get_diet_callback_handler
from bot.handlers.feedback import get_feedback_handlers
from bot.handlers.onboarding import get_onboarding_handler
from bot.handlers.photo import get_dm_photo_handler, get_photo_handlers
from bot.handlers.reading import get_reading_handlers, handle_dm_text
from bot.handlers.water import get_water_callback_handler, get_water_command_handler
from bot.handlers.workout import get_workout_handlers, handle_custom_workout_name
from bot.jobs.scheduler import schedule_jobs
from bot.templates.messages import PINNED_FAQ, WELCOME_GROUP

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """Initialize database, pre-populate participants, and schedule jobs."""
    Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = Database(DATABASE_PATH)
    await db.init()
    application.bot_data["db"] = db
    application.bot_data["group_chat_id"] = GROUP_CHAT_ID if GROUP_CHAT_ID else None

    # Pre-populate participants with placeholder IDs
    all_users = await db.get_all_users()
    existing_names = {u["name"] for u in all_users}
    for name in PARTICIPANTS:
        if name not in existing_names:
            placeholder_id = random.randint(900000000, 999999999)
            await db.add_user(placeholder_id, name)

    schedule_jobs(application.job_queue)
    logger.info(
        "Bot initialized. %d users, jobs scheduled.",
        len(await db.get_all_users()),
    )


async def post_shutdown(application: Application) -> None:
    """Clean up the database connection."""
    db = application.bot_data.get("db")
    if db:
        await db.close()


async def handle_new_group(update: Update, context) -> None:
    """Detect when the bot is added to a group and send welcome messages."""
    if update.my_chat_member:
        new_status = update.my_chat_member.new_chat_member.status
        if new_status in ("member", "administrator"):
            chat_id = update.my_chat_member.chat.id
            context.bot_data["group_chat_id"] = chat_id
            logger.info(
                "Bot added to group: %s (chat_id: %d)",
                update.my_chat_member.chat.title,
                chat_id,
            )

            db = context.bot_data["db"]
            unregistered = await db.get_unregistered_names()
            all_users = await db.get_all_users()
            bot_info = await context.bot.get_me()

            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=WELCOME_GROUP.format(
                    bot_username=bot_info.username,
                    waiting_names=", ".join(unregistered),
                    registered=len(all_users) - len(unregistered),
                    total=len(all_users),
                ),
            )
            context.bot_data["welcome_message_id"] = msg.message_id

            faq_msg = await context.bot.send_message(
                chat_id=chat_id, text=PINNED_FAQ
            )
            try:
                await context.bot.pin_chat_message(
                    chat_id=chat_id,
                    message_id=faq_msg.message_id,
                    disable_notification=True,
                )
            except Exception:
                pass

            # Generate invite link for auto-sharing after payment
            try:
                invite = await context.bot.create_chat_invite_link(
                    chat_id=chat_id,
                    name="75 Hard — Locked In",
                )
                context.bot_data["group_invite_link"] = invite.invite_link
                logger.info("Invite link generated: %s", invite.invite_link)
            except Exception as e:
                logger.warning("Could not create invite link: %s", e)


def main() -> None:
    """Build the application, register handlers, and start polling."""
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Conversation handlers first (they need priority)
    app.add_handler(get_onboarding_handler())
    app.add_handler(get_fail_handler())
    app.add_handler(get_redeem_handler())

    # Card command
    app.add_handler(get_card_command_handler())

    # Callback handlers for daily card buttons
    app.add_handler(get_water_callback_handler())
    app.add_handler(get_diet_callback_handler())
    for h in get_workout_handlers():
        app.add_handler(h)
    for h in get_reading_handlers():
        app.add_handler(h)
    for h in get_photo_handlers():
        app.add_handler(h)

    # Command handlers
    app.add_handler(get_water_command_handler())
    for h in get_feedback_handlers():
        app.add_handler(h)
    for h in get_admin_handlers():
        app.add_handler(h)

    # Text handlers — custom workout name (group), reading flow (DM), and AI chat (DM)
    from bot.utils.luke_chat import chat_with_luke

    async def combined_text_handler(update: Update, context):
        """Route text messages to the right handler."""
        if await handle_custom_workout_name(update, context):
            return
        if update.effective_chat.type == "private":
            # Active conversation flows first (reading, onboarding, etc.)
            if await handle_dm_text(update, context):
                return

            # No active flow — full AI chat with database tools
            message = update.message.text.strip()
            if len(message) < 2:
                return

            db = context.bot_data["db"]
            result = await chat_with_luke(message, db, update.effective_user.id)

            if result.get("cover_url"):
                await update.message.reply_photo(
                    photo=result["cover_url"],
                    caption=result["text"],
                )
            elif result["text"]:
                await update.message.reply_text(result["text"])

    app.add_handler(get_dm_photo_handler())
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            combined_text_handler,
        )
    )

    # Group join detection
    app.add_handler(
        ChatMemberHandler(handle_new_group, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    logger.info("Starting 75 Hard bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
