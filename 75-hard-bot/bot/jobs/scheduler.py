"""Scheduled daily jobs for the 75 Hard bot using python-telegram-bot's JobQueue."""

import html
import pytz
from datetime import date, time

from telegram.ext import ContextTypes

from bot.config import ADMIN_USER_ID, CHALLENGE_DAYS, CHALLENGE_START_DATE
from bot.handlers.daily_card import post_daily_card
from bot.utils.easter_eggs import post_milestone_if_needed
from bot.utils.progress import get_day_number, get_missing_tasks, is_all_complete

ET = pytz.timezone("US/Eastern")


async def morning_card_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7 AM ET -- Post daily card. Day 1 includes participant intros."""
    db = context.bot_data["db"]
    day = get_day_number(CHALLENGE_START_DATE, date.today())
    if not (1 <= day <= CHALLENGE_DAYS):
        return

    chat_id = context.bot_data.get("group_chat_id")

    # Day 1: post participant introductions before the card
    if day == 1 and chat_id:
        users = await db.get_active_users()
        lines = [
            "🔥 DAY 1 — LET'S GO\n",
            "Meet your squad:\n",
        ]
        for u in sorted(users, key=lambda x: x["name"].lower()):
            diet = u.get("diet_plan") or "not set yet"
            book = u.get("current_book") or "TBD"
            lines.append(f"  {u['name']}")
            lines.append(f"    🍽️ {diet}")
            lines.append(f'    📖 "{book}"')
            lines.append("")

        lines.append("75 days starts now. No one knows who they'll be on Day 75.")

        try:
            await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))
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

    checkins = await db.get_all_checkins_for_day(day)
    active_users = await db.get_active_users()
    if not checkins:
        return

    complete = [c for c in checkins if is_all_complete(c)]
    incomplete = [c for c in checkins if not is_all_complete(c)]
    remaining = CHALLENGE_DAYS - day

    parts = [f"<b>Day {day} Recap</b>"]

    # Completed section
    if complete:
        completed_lines = ["", "<b>Completed</b>"]
        for c in sorted(complete, key=lambda x: x["name"].lower()):
            safe_name = html.escape(c["name"])
            completed_lines.append(f"\u2705 {safe_name} \u2014 6/6")
        parts.extend(completed_lines)

    # In Progress section
    if incomplete:
        progress_lines = ["", "<b>In Progress</b>"]
        for c in sorted(incomplete, key=lambda x: x["name"].lower()):
            safe_name = html.escape(c["name"])
            tasks_done = sum([
                bool(c["workout_1_done"]),
                bool(c["workout_2_done"]),
                c["water_cups"] >= 16,
                bool(c["diet_done"]),
                bool(c["reading_done"]),
                bool(c["photo_done"]),
            ])
            missing = get_missing_tasks(c)
            missing_str = ", ".join(m.lower() for m in missing)
            progress_lines.append(
                f"\u23f3 {safe_name} \u2014 {tasks_done}/6 (needs: {html.escape(missing_str)})"
            )
        parts.extend(progress_lines)

    # Reading section -- expandable blockquote
    reads = [
        (c["name"], c.get("book_title"), c.get("reading_takeaway"))
        for c in checkins
        if c["reading_done"] and c.get("book_title")
    ]
    if reads:
        reading_lines = ["\n<blockquote expandable>\U0001f4d6 Today's Reading\n"]
        for name, book, takeaway in reads:
            safe_name = html.escape(name)
            safe_book = html.escape(book or "")
            reading_lines.append(f'{safe_name} \u2014 "{safe_book}"')
            if takeaway:
                safe_takeaway = html.escape(takeaway)
                reading_lines.append(f'"{safe_takeaway}"')
            reading_lines.append("")
        parts.append("\n".join(reading_lines).rstrip() + "</blockquote>")

    # Footer
    parts.append(
        f"\n{len(complete)}/{len(checkins)} completed \u00b7 {remaining} days to go"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(parts),
        parse_mode="HTML",
    )


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
