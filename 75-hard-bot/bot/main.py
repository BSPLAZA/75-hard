"""Main entry point for the 75 Hard Telegram bot."""

import logging
import os
import random
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    ChatMemberHandler,
    MessageHandler,
    filters,
)

from bot.config import BOT_TOKEN, DATABASE_PATH, GROUP_CHAT_ID, ORGANIZER, PARTICIPANTS, USER_TIMEZONES
from bot.database import Database
from bot.handlers.admin import get_admin_handlers, get_fail_handler, get_redeem_handler
from bot.handlers.transformation import get_transformation_handler, get_timelapse_handler
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

    # Restore persisted settings (survive restarts/deploys)
    saved_chat_id = await db.get_setting("group_chat_id")
    saved_invite = await db.get_setting("group_invite_link")
    application.bot_data["group_chat_id"] = int(saved_chat_id) if saved_chat_id else (GROUP_CHAT_ID if GROUP_CHAT_ID else None)
    application.bot_data["group_invite_link"] = saved_invite

    # Pre-populate participants with placeholder IDs
    all_users = await db.get_all_users()
    existing_names = {u["name"] for u in all_users}
    for name in PARTICIPANTS:
        if name not in existing_names:
            placeholder_id = random.randint(900000000, 999999999)
            await db.add_user(placeholder_id, name)

    # Seed users.timezone from USER_TIMEZONES env on first boot. Idempotent —
    # only sets timezone for users that don't already have one in the DB so
    # Luke's set_user_timezone tool changes are preserved across restarts.
    for u in await db.get_all_users():
        if u["timezone"] is None and u["name"] in USER_TIMEZONES:
            await db.set_user_timezone(u["telegram_id"], USER_TIMEZONES[u["name"]])

    schedule_jobs(application.job_queue)
    logger.info(
        "Bot initialized. %d users, jobs scheduled. group_chat_id=%s, invite_link=%s",
        len(await db.get_all_users()),
        application.bot_data.get("group_chat_id"),
        "yes" if application.bot_data.get("group_invite_link") else "no",
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


def _setup_phoenix_tracing() -> None:
    """Register the Phoenix tracer + Anthropic instrumentor before any LLM call.

    Skipped silently if PHOENIX_API_KEY is unset — keeps test environments and
    Phoenix-less deploys functional. Must run once at startup, before any
    anthropic.Anthropic(...) client is constructed.
    """
    if not os.getenv("PHOENIX_API_KEY"):
        logger.info("phoenix: PHOENIX_API_KEY not set; tracing disabled")
        return
    try:
        from phoenix.otel import register
        from openinference.instrumentation.anthropic import AnthropicInstrumentor

        tracer_provider = register(
            project_name=os.getenv("PHOENIX_PROJECT_NAME", "luke-75-hard"),
            endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT"),
            auto_instrument=False,
        )
        AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)
        logger.info(
            "phoenix: tracing enabled, project=%s endpoint=%s",
            os.getenv("PHOENIX_PROJECT_NAME", "luke-75-hard"),
            os.getenv("PHOENIX_COLLECTOR_ENDPOINT"),
        )
    except Exception as exc:
        logger.warning("phoenix: failed to enable tracing (%s); continuing without", exc)


def main() -> None:
    """Build the application, register handlers, and start polling."""
    _setup_phoenix_tracing()

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
    app.add_handler(get_transformation_handler())
    app.add_handler(get_timelapse_handler())
    app.add_handler(get_water_command_handler())
    for h in get_feedback_handlers():
        app.add_handler(h)
    for h in get_admin_handlers():
        app.add_handler(h)

    # Text handlers — custom workout name (group), reading flow (DM), and AI chat (DM)
    from bot.utils.luke_chat import chat_with_luke

    PRIVATE_BOT_REPLY_NEW = (
        "👋 this is a private accountability bot for a closed 75 Hard challenge.\n\n"
        f"if {ORGANIZER} invited you, type /start to begin onboarding.\n\n"
        "if not — sorry, nothing for you here."
    )
    PRIVATE_BOT_REPLY_LONG = (
        "this is a private bot for a closed 75 Hard challenge — you're not on the roster. "
        f"if you think you should be, ask {ORGANIZER} to add your name."
    )

    async def combined_text_handler(update: Update, context):
        """Route text messages to the right handler."""
        if await handle_custom_workout_name(update, context):
            return
        if update.effective_chat.type == "private":
            # Track AI chat quality: user continued conversation instead of using /command
            if context.user_data.get("last_was_ai_chat"):
                db = context.bot_data["db"]
                await db.log_event(update.effective_user.id, None, "ai_chat_success")
                context.user_data.pop("last_was_ai_chat", None)

            # Active conversation flows first (reading, onboarding, etc.)
            if await handle_dm_text(update, context):
                return

            # No active flow — full AI chat with database tools
            message = update.message.text.strip()
            if len(message) < 2:
                return
            # Hard cap to bound Anthropic input tokens and prevent runaway cost
            # from a single oversized DM (legit user messages are ≤ 500 chars).
            if len(message) > 2000:
                message = message[:2000]
                try:
                    await update.message.reply_text(
                        "(your message was long, truncated to 2000 chars)"
                    )
                except Exception:
                    pass

            db = context.bot_data["db"]
            user_id = update.effective_user.id

            # Gate: only registered participants may invoke the LLM (cost protection
            # + privacy). Strangers get a polite turn-away. Onboarding still works
            # because that flow is owned by the higher-priority ConversationHandler.
            user_row = await db.get_user(user_id)
            if not user_row or not user_row["dm_registered"]:
                u = update.effective_user
                logger.warning(
                    "STRANGER_DM_BLOCKED chat_id=%d username=%s text=%r",
                    user_id, u.username or "", message[:80],
                )
                try:
                    await db.log_event(
                        user_id, u.first_name or u.username or "",
                        "stranger_dm_blocked", f"text={message[:120]!r}",
                    )
                except Exception:
                    pass
                # Short messages look like onboarding attempts — point to /start.
                # Long messages are conversational; gently turn them away.
                reply = PRIVATE_BOT_REPLY_NEW if len(message) <= 40 else PRIVATE_BOT_REPLY_LONG
                await update.message.reply_text(reply)
                return

            result = await chat_with_luke(message, db, user_id, context=context)

            # Handle media requests (transformation/timelapse)
            if result.get("media") == "transformation":
                from bot.handlers.transformation import transformation_command
                await transformation_command(update, context)
                return
            elif result.get("media") == "timelapse":
                from bot.handlers.transformation import timelapse_command
                await timelapse_command(update, context)
                return

            # Refresh the daily card if a tracker action was taken
            if result.get("refresh_card"):
                from bot.handlers.daily_card import refresh_card
                from bot.utils.progress import get_day_number as _gdn
                from bot.config import CHALLENGE_START_DATE as _csd
                from datetime import date as _d
                day = max(_gdn(_csd, _d.today()), 1)
                await refresh_card(context, day)
                # Also refresh any backfilled day's card
                for bf_day in result.get("refresh_days", set()):
                    if bf_day != day:
                        await refresh_card(context, bf_day)

            # If Luke set up a photo backfill, prime user_data so the next
            # DM photo lands on the requested day instead of today.
            bf_photo_day = result.get("backfill_photo_day")
            if bf_photo_day:
                context.user_data["awaiting_photo"] = True
                context.user_data["photo_day"] = bf_photo_day

            if result.get("cover_url"):
                await update.message.reply_photo(
                    photo=result["cover_url"],
                    caption=result["text"],
                )
            elif result["text"]:
                await update.message.reply_text(result["text"])

            # Mark that the last interaction was AI chat (for quality tracking)
            context.user_data["last_was_ai_chat"] = True

    # Track AI chat fallback: user sent a /command after AI chat (negative signal)
    async def ai_fallback_tracker(update: Update, context):
        """Detect when a user falls back to a /command after an AI chat response."""
        if (
            update.effective_chat
            and update.effective_chat.type == "private"
            and context.user_data.get("last_was_ai_chat")
        ):
            db = context.bot_data["db"]
            cmd = update.message.text.split()[0] if update.message and update.message.text else "unknown"
            await db.log_event(update.effective_user.id, None, "ai_chat_fallback", f"cmd={cmd}")
            context.user_data.pop("last_was_ai_chat", None)

    app.add_handler(
        MessageHandler(
            filters.COMMAND & filters.ChatType.PRIVATE,
            ai_fallback_tracker,
        ),
        group=-1,  # Runs before other handlers, doesn't consume the update
    )

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
