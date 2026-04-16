"""Scheduled daily jobs for the 75 Hard bot using python-telegram-bot's JobQueue."""

import logging
import pytz
import time as time_mod
from collections import Counter, defaultdict
from datetime import date, time

from telegram.ext import ContextTypes

from bot.config import ADMIN_USER_ID, CHALLENGE_DAYS, CHALLENGE_START_DATE
from bot.handlers.daily_card import post_daily_card
from bot.utils.easter_eggs import post_milestone_if_needed
from bot.utils.luke_ai import generate_morning_message, generate_weekly_reflection
from bot.utils.progress import today_et, get_day_number, get_missing_tasks, is_all_complete

logger = logging.getLogger(__name__)

ET = pytz.timezone("US/Eastern")


async def morning_card_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7 AM ET -- Post daily card. Day 1 includes participant intros."""
    db = context.bot_data["db"]
    day = get_day_number(CHALLENGE_START_DATE, today_et())
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
        start = time_mod.monotonic()
        ai_message = await generate_morning_message(
            day_number=day,
            active_count=len(active_users),
            total_count=len(all_users),
            yesterday_summary=yesterday_summary,
        )
        latency_ms = int((time_mod.monotonic() - start) * 1000)
        await db.log_event(None, None, "ai_morning", f"day={day}", latency_ms=latency_ms)
        if ai_message:
            try:
                await context.bot.send_message(chat_id=chat_id, text=ai_message)
            except Exception:
                pass

    try:
        await post_daily_card(context)
        await db.log_event(None, None, "health_check_ok", f"day={day}")
    except Exception as e:
        await db.log_event(None, None, "health_check_fail", f"day={day}", error=str(e))
        raise

    # Post milestone message if applicable (separate message after the card)
    await post_milestone_if_needed(context, day)


async def evening_scoreboard_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """10 PM ET -- Post wrap-up summary."""
    db = context.bot_data["db"]
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    day = max(get_day_number(CHALLENGE_START_DATE, today_et()), 1)
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

    caption = f"{len(complete)}/{len(checkins)} completed · {remaining} days to go"

    await context.bot.send_photo(chat_id=chat_id, photo=image_buf, caption=caption)

    # Send bookshelf image if anyone read today
    reads = [
        c for c in checkins
        if c.get("reading_done") and c.get("book_title")
    ]
    if reads:
        from bot.utils.bookshelf import render_bookshelf

        # Get cover URLs for each reader
        readers = []
        for c in reads:
            cover_url = await db.get_current_book_cover(c["telegram_id"])
            readers.append({
                "name": c["name"],
                "book_title": c["book_title"],
                "takeaway": c.get("reading_takeaway", ""),
                "cover_url": cover_url,
            })

        bookshelf_buf = await render_bookshelf(readers)
        if bookshelf_buf:
            await context.bot.send_photo(chat_id=chat_id, photo=bookshelf_buf)

    await db.log_event(None, None, "ai_recap", f"day={day} complete={len(complete)}/{len(checkins)}")


async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """11 PM ET -- DM users with incomplete tasks."""
    db = context.bot_data["db"]
    day = max(get_day_number(CHALLENGE_START_DATE, today_et()), 1)
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


async def _gather_weekly_data(db, current_day: int) -> dict:
    """Gather all data needed for the weekly digest.

    Returns a dict with keys: user_stats, total_workouts, total_water,
    total_reading_days, first_finisher_name, first_finisher_count,
    reading_log, week_number, day_range.
    """
    # Determine the 7-day range ending today
    start_day = max(1, current_day - 6)
    end_day = current_day

    # Collect all checkins for the week
    all_checkins = []
    for d in range(start_day, end_day + 1):
        day_checkins = await db.get_all_checkins_for_day(d)
        all_checkins.extend([(d, dict(c)) for d, c in [(d, c) for c in day_checkins]])

    # Build per-user stats
    user_days = defaultdict(list)  # name -> list of (day_number, checkin_dict)
    for day_num, c in all_checkins:
        user_days[c["name"]].append((day_num, c))

    user_stats = []
    total_workouts = 0
    total_water = 0
    total_reading_days = 0
    first_finisher_counter = Counter()

    # Reading log: name -> {title -> count}
    reading_books = defaultdict(lambda: defaultdict(int))

    for name, day_entries in user_days.items():
        days_complete = 0
        consistency = []

        # Sort by day number to get correct ordering
        day_entries.sort(key=lambda x: x[0])

        for day_num, c in day_entries:
            completed = is_all_complete(c)
            consistency.append(completed)
            if completed:
                days_complete += 1

            # Tally workouts
            if c.get("workout_1_done"):
                total_workouts += 1
            if c.get("workout_2_done"):
                total_workouts += 1

            # Tally water
            total_water += c.get("water_cups", 0)

            # Tally reading
            if c.get("reading_done"):
                total_reading_days += 1
                book = c.get("book_title")
                if book:
                    reading_books[name][book] += 1

        user_stats.append({
            "name": name,
            "days_complete": days_complete,
            "total_days": len(day_entries),
            "consistency": consistency,
        })

    # Determine who finished first the most
    for d in range(start_day, end_day + 1):
        day_checkins = [c for day_num, c in all_checkins if day_num == d]
        completers = [c for c in day_checkins if c.get("completed_at")]
        if completers:
            completers.sort(key=lambda c: c["completed_at"])
            first_finisher_counter[completers[0]["name"]] += 1

    first_finisher_name = None
    first_finisher_count = 0
    if first_finisher_counter:
        first_finisher_name, first_finisher_count = first_finisher_counter.most_common(1)[0]

    # Build reading log
    reading_log = []
    for name in sorted(reading_books.keys()):
        books = [{"title": title, "days": count} for title, count in reading_books[name].items()]
        books.sort(key=lambda b: b["days"], reverse=True)
        reading_log.append({"name": name, "books": books})

    week_number = max(1, (current_day + 6) // 7)

    return {
        "user_stats": user_stats,
        "total_workouts": total_workouts,
        "total_water": total_water,
        "total_reading_days": total_reading_days,
        "first_finisher_name": first_finisher_name,
        "first_finisher_count": first_finisher_count,
        "reading_log": reading_log,
        "week_number": week_number,
    }


async def _send_transformation_dms(context, db, current_day: int) -> None:
    """DM each eligible user their Day 1 vs current day transformation composite."""
    from bot.utils.photo_transform import render_transformation

    active_users = await db.get_active_users()
    for user in active_users:
        if not user["dm_registered"]:
            continue
        try:
            photos = await db.get_photo_file_ids(user["telegram_id"])
            if not photos:
                continue

            # Need a Day 1 photo
            day1_photo = next(
                (p for p in photos if p["day_number"] == 1), None
            )
            if not day1_photo:
                continue

            # Need a photo from the current day (or most recent)
            latest_photo = photos[-1]
            if latest_photo["day_number"] == 1:
                continue  # only have Day 1

            buf = await render_transformation(
                bot=context.bot,
                name=user["name"],
                day1_file_id=day1_photo["photo_file_id"],
                current_file_id=latest_photo["photo_file_id"],
                current_day=latest_photo["day_number"],
            )
            await context.bot.send_photo(
                chat_id=user["telegram_id"],
                photo=buf,
                caption=(
                    f"Your transformation so far -- "
                    f"Day 1 to Day {latest_photo['day_number']}. "
                    f"Keep going!"
                ),
            )
        except Exception as e:
            logger.warning(
                "Could not send transformation DM to %s: %s",
                user["name"],
                e,
            )


async def weekly_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """8 PM ET Sunday -- Post weekly digest with image, AI reflection, and reading log."""
    # Only run on Sundays
    if today_et().weekday() != 6:
        return

    db = context.bot_data["db"]
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    current_day = max(get_day_number(CHALLENGE_START_DATE, today_et()), 1)
    if current_day > CHALLENGE_DAYS:
        return

    try:
        data = await _gather_weekly_data(db, current_day)

        if not data["user_stats"]:
            return

        # Generate the digest image
        from bot.utils.image_generator import render_weekly_digest_image
        image_buf = render_weekly_digest_image(
            week_number=data["week_number"],
            user_stats=data["user_stats"],
            total_workouts=data["total_workouts"],
            total_water=data["total_water"],
            total_reading_days=data["total_reading_days"],
            first_finisher_name=data["first_finisher_name"],
            first_finisher_count=data["first_finisher_count"],
            reading_log=data["reading_log"],
        )

        # Generate AI reflection as caption (reading is now in the image)
        start = time_mod.monotonic()
        reflection = await generate_weekly_reflection(
            week_number=data["week_number"],
            user_stats=data["user_stats"],
            reading_log=data["reading_log"],
        )
        latency_ms = int((time_mod.monotonic() - start) * 1000)
        await db.log_event(None, None, "ai_weekly", f"week={data['week_number']}", latency_ms=latency_ms)

        caption = reflection or f"Week {data['week_number']} digest"

        # Send
        if len(caption) > 1024:
            await context.bot.send_photo(chat_id=chat_id, photo=image_buf)
            await context.bot.send_message(chat_id=chat_id, text=caption)
        else:
            await context.bot.send_photo(chat_id=chat_id, photo=image_buf, caption=caption)

        # ── Send transformation DMs to users with Day 1 + current day photos ──
        if current_day >= 7:
            await _send_transformation_dms(context, db, current_day)

    except Exception as e:
        logger.error("Weekly digest job failed: %s", e)
        try:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"Weekly digest failed: {e}",
            )
        except Exception:
            pass


async def morning_after_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """9 AM ET -- Remind users about yesterday's incomplete tasks."""
    db = context.bot_data["db"]
    from bot.utils.progress import get_current_challenge_day
    day = await get_current_challenge_day(db)
    yesterday = day - 1
    if yesterday < 1:
        return

    checkins = await db.get_all_checkins_for_day(yesterday)
    for c in checkins:
        c = dict(c)
        if is_all_complete(c):
            continue
        missing = get_missing_tasks(c)
        user = await db.get_user(c["telegram_id"])
        if not user or not user["dm_registered"]:
            continue

        missing_list = "\n".join(f"  - {m.lower()}" for m in missing)
        try:
            await context.bot.send_message(
                chat_id=c["telegram_id"],
                text=(
                    f"hey -- you still have incomplete tasks from yesterday (day {yesterday}):\n\n"
                    f"{missing_list}\n\n"
                    f"log them now if you did them. you have until noon ET."
                ),
            )
        except Exception:
            pass


async def noon_cutoff_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """12 PM ET -- Lock previous day, flag incomplete users to admin."""
    day = get_day_number(CHALLENGE_START_DATE, today_et())
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


async def daily_backup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """3 AM ET -- Backup the database."""
    import shutil
    from bot.config import DATABASE_PATH
    try:
        backup_path = DATABASE_PATH + f".backup-day{get_day_number(CHALLENGE_START_DATE, today_et())}"
        shutil.copy2(DATABASE_PATH, backup_path)
        logger.info("Database backed up to %s", backup_path)
    except Exception as e:
        logger.error("Backup failed: %s", e)


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
        morning_after_reminder_job, time=time(9, 0, tzinfo=ET), name="morning_after_reminder"
    )
    job_queue.run_daily(
        noon_cutoff_job, time=time(12, 0, tzinfo=ET), name="noon_cutoff"
    )
    job_queue.run_daily(
        weekly_digest_job, time=time(20, 0, tzinfo=ET), name="weekly_digest"
    )
    job_queue.run_daily(
        daily_backup_job, time=time(3, 0, tzinfo=ET), name="daily_backup"
    )
