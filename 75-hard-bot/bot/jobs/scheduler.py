"""Scheduled daily jobs for the 75 Hard bot using python-telegram-bot's JobQueue."""

import pytz
from datetime import date, time

from telegram.ext import ContextTypes

from bot.config import ADMIN_USER_ID, CHALLENGE_DAYS, CHALLENGE_START_DATE
from bot.handlers.daily_card import post_daily_card
from bot.utils.easter_eggs import post_milestone_if_needed
from bot.utils.luke_ai import generate_morning_message
from bot.utils.progress import get_day_number, get_missing_tasks, is_all_complete

ET = pytz.timezone("US/Eastern")


async def morning_card_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7 AM ET -- Post daily card. Day 1 includes participant intros."""
    db = context.bot_data["db"]
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    if not (1 <= day <= CHALLENGE_DAYS):
        return

    chat_id = context.bot_data.get("group_chat_id")

    # Generate AI morning briefing (replaces static templates)
    if chat_id:
        yesterday_summary = None
        if day > 1:
            prev_checkins = await db.get_all_checkins_for_day(day - 1)
            prev_dicts = [dict(c) for c in prev_checkins]
            completed_names = [c["name"] for c in prev_dicts if is_all_complete(c)]
            incomplete_list = [
                (c["name"], get_missing_tasks(c))
                for c in prev_dicts if not is_all_complete(c)
            ]
            # Find first finisher
            completers = [c for c in prev_dicts if c.get("completed_at")]
            completers.sort(key=lambda c: c["completed_at"])
            first = completers[0]["name"] if completers else None
            books = [(c["name"], c.get("book_title")) for c in prev_dicts if c.get("book_title")]

            yesterday_summary = {
                "day": day - 1,
                "completed": completed_names,
                "incomplete": incomplete_list,
                "first_finisher": first,
                "books": books,
            }

        active_users = await db.get_active_users()
        all_users = await db.get_all_users()
        ai_message = await generate_morning_message(
            day_number=day,
            active_count=len(active_users),
            total_count=len(all_users),
            yesterday_summary=yesterday_summary,
        )
        if ai_message:
            try:
                await context.bot.send_message(chat_id=chat_id, text=ai_message)
            except Exception:
                pass

    await post_daily_card(context)

    # Post milestone message if applicable (separate message after the card)
    await post_milestone_if_needed(context, day)


async def evening_scoreboard_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """10 PM ET -- Post wrap-up summary."""
    db = context.bot_data["db"]
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)
    if day > CHALLENGE_DAYS:
        return

    checkins_raw = await db.get_all_checkins_for_day(day)
    active_users = await db.get_active_users()
    if not checkins_raw:
        return

    # Convert to dicts immediately
    checkins = [dict(c) for c in checkins_raw]
    complete = [c for c in checkins if is_all_complete(c)]
    remaining = CHALLENGE_DAYS - day

    from bot.utils.image_generator import render_recap_image
    image_buf = render_recap_image(day, checkins, CHALLENGE_DAYS)

    # Build reading caption
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

    # Send as photo with caption (max 1024 chars for caption)
    if len(caption) > 1024:
        # Send image without caption, then caption as separate message
        await context.bot.send_photo(chat_id=chat_id, photo=image_buf)
        await context.bot.send_message(chat_id=chat_id, text=caption)
    else:
        await context.bot.send_photo(chat_id=chat_id, photo=image_buf, caption=caption)


async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """11 PM ET -- DM users with incomplete tasks."""
    db = context.bot_data["db"]
    day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)
    if day > CHALLENGE_DAYS:
        return

    checkins = await db.get_all_checkins_for_day(day)
    for c in checkins:
        if is_all_complete(c):
            continue
        missing = get_missing_tasks(c)
        user = await db.get_user(c["telegram_id"])
        if not user or not user["dm_registered"]:
            continue
        missing_list = "\n".join(f"  - {m}" for m in missing)
        text = (
            f"Hey {user['name']} -- you have unchecked tasks for today:\n\n"
            f"{missing_list}\n\n"
            "If you've done them, log them now.\n"
            "You can also backfill until noon tomorrow."
        )
        try:
            await context.bot.send_message(chat_id=c["telegram_id"], text=text)
        except Exception:
            pass


async def noon_cutoff_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """12 PM ET -- Lock previous day, flag incomplete users to admin."""
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    yesterday = day - 1
    if yesterday < 1:
        return

    db = context.bot_data["db"]
    checkins = await db.get_all_checkins_for_day(yesterday)
    for c in checkins:
        if not is_all_complete(c):
            missing = get_missing_tasks(c)
            user = await db.get_user(c["telegram_id"])
            name = user["name"] if user else "?"
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=(
                        f"WARNING: {name} has incomplete tasks for Day {yesterday}: "
                        f"{', '.join(missing)}. Use /admin_eliminate if needed."
                    ),
                )
            except Exception:
                pass


def schedule_jobs(job_queue) -> None:
    """Register all daily scheduled jobs."""
    job_queue.run_daily(
        morning_card_job, time=time(7, 0, tzinfo=ET), name="morning_card"
    )
    job_queue.run_daily(
        evening_scoreboard_job, time=time(22, 0, tzinfo=ET), name="evening_scoreboard"
    )
    job_queue.run_daily(nudge_job, time=time(23, 0, tzinfo=ET), name="nudge")
    job_queue.run_daily(
        noon_cutoff_job, time=time(12, 0, tzinfo=ET), name="noon_cutoff"
    )
