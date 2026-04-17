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
CT = pytz.timezone("US/Central")
MT = pytz.timezone("US/Mountain")
PT = pytz.timezone("US/Pacific")


async def morning_card_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7 AM ET -- Daily morning sequence in one shot:
      1. Short AI greeting (with yesterday context for prompt only)
      2. Yesterday's recap image (snapshot — lock isn't until 3pm ET)
      3. Bookshelf image (if anyone read yesterday)
      4. Today's daily card with buttons
    """
    db = context.bot_data["db"]
    day = get_day_number(CHALLENGE_START_DATE, today_et())
    if not (1 <= day <= CHALLENGE_DAYS):
        return

    chat_id = context.bot_data.get("group_chat_id")

    # Build yesterday's summary (used for AI prompt + recap image)
    yesterday_summary = None
    yesterday_dicts: list[dict] = []
    if day > 1 and chat_id:
        prev_checkins = await db.get_all_checkins_for_day(day - 1)
        yesterday_dicts = [dict(c) for c in prev_checkins]
        completed_names = [c["name"] for c in yesterday_dicts if is_all_complete(c)]
        incomplete_list = [
            (c["name"], get_missing_tasks(c))
            for c in yesterday_dicts if not is_all_complete(c)
        ]
        completers = [c for c in yesterday_dicts if c.get("completed_at")]
        completers.sort(key=lambda c: c["completed_at"])
        first = completers[0]["name"] if completers else None
        books = [(c["name"], c.get("book_title")) for c in yesterday_dicts if c.get("book_title")]

        yesterday_summary = {
            "day": day - 1,
            "completed": completed_names,
            "incomplete": incomplete_list,
            "first_finisher": first,
            "books": books,
        }

    # 1. AI greeting
    if chat_id:
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

    # 2. Yesterday's recap image
    if chat_id and yesterday_dicts:
        try:
            from bot.utils.image_generator import render_recap_image
            yesterday_day = day - 1
            complete = [c for c in yesterday_dicts if is_all_complete(c)]
            recap_caption = (
                f"Day {yesterday_day} recap — {len(complete)}/{len(yesterday_dicts)} done. "
                f"Backfill until 12pm PT / 3pm ET."
            )
            recap_buf = render_recap_image(yesterday_day, yesterday_dicts, CHALLENGE_DAYS)
            await context.bot.send_photo(chat_id=chat_id, photo=recap_buf, caption=recap_caption)
        except Exception as e:
            logger.warning("Yesterday recap image failed: %s", e)

    # 3. Bookshelf image (if anyone read yesterday)
    if chat_id and yesterday_dicts:
        reads = [
            c for c in yesterday_dicts
            if c.get("reading_done") and c.get("book_title")
        ]
        if reads:
            try:
                from bot.utils.bookshelf import render_bookshelf
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
            except Exception as e:
                logger.warning("Bookshelf image failed: %s", e)

    # 4. Today's daily card
    try:
        await post_daily_card(context)
        await db.log_event(None, None, "health_check_ok", f"day={day}")
    except Exception as e:
        await db.log_event(None, None, "health_check_fail", f"day={day}", error=str(e))
        raise

    await post_milestone_if_needed(context, day)


async def _nudge_for_tz(context: ContextTypes.DEFAULT_TYPE, tz_label: str) -> None:
    """Send same-day DM nudge to users whose users.timezone matches tz_label.

    tz_label is an IANA name like "US/Eastern". Fires at 10pm in that zone.
    Uses card-based day so the nudge always references the open challenge day,
    even when 10pm PT crosses calendar midnight ET.

    Source of truth is `users.timezone` in the DB (set during init from env or
    updated dynamically via Luke's set_user_timezone tool).
    """
    from bot.utils.progress import get_current_challenge_day

    db = context.bot_data["db"]
    day = await get_current_challenge_day(db)
    if day < 1 or day > CHALLENGE_DAYS:
        return

    target_users = await db.get_users_in_timezone(tz_label)
    if not target_users:
        return
    target_ids = {u["telegram_id"] for u in target_users}

    checkins = await db.get_all_checkins_for_day(day)
    for c in checkins:
        if c["telegram_id"] not in target_ids:
            continue
        if is_all_complete(c):
            continue
        missing = get_missing_tasks(c)
        user = await db.get_user(c["telegram_id"])
        if not user or not user["dm_registered"]:
            continue
        missing_list = "\n".join(f"  - {m}" for m in missing)
        text = (
            f"Hey {user['name']} -- you have unchecked tasks for day {day}:\n\n"
            f"{missing_list}\n\n"
            "If you've done them, log them now.\n"
            "You can also backfill until 12pm PT / 3pm ET tomorrow."
        )
        try:
            await context.bot.send_message(chat_id=c["telegram_id"], text=text)
        except Exception:
            pass


async def nudge_job_et(context: ContextTypes.DEFAULT_TYPE) -> None:
    """10 PM ET -- DM nudge for east-coast users."""
    await _nudge_for_tz(context, "US/Eastern")


async def nudge_job_ct(context: ContextTypes.DEFAULT_TYPE) -> None:
    """10 PM CT -- DM nudge for central-time users."""
    await _nudge_for_tz(context, "US/Central")


async def nudge_job_mt(context: ContextTypes.DEFAULT_TYPE) -> None:
    """10 PM MT -- DM nudge for mountain-time users."""
    await _nudge_for_tz(context, "US/Mountain")


async def nudge_job_pt(context: ContextTypes.DEFAULT_TYPE) -> None:
    """10 PM PT -- DM nudge for west-coast users."""
    await _nudge_for_tz(context, "US/Pacific")


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
    """9 AM ET -- Remind users about yesterday's incomplete tasks (gives 6h to lock)."""
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
                    f"log them now if you did them. you have until 12pm PT / 3pm ET."
                ),
            )
        except Exception:
            pass


async def noon_cutoff_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """12 PM PT -- Lock previous day, flag incomplete users to admin."""
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


async def spicy_moment_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """9 PM ET -- ask Luke to find one spicy moment from today and post it.

    Fires after most logging is done but while east coast is still awake. Posts
    to the group only if the AI returns something interesting (not "NONE").
    """
    db = context.bot_data["db"]
    chat_id = context.bot_data.get("group_chat_id")
    if not chat_id:
        return

    from bot.utils.progress import get_current_challenge_day
    day = await get_current_challenge_day(db)
    if day < 1 or day > CHALLENGE_DAYS:
        return

    today_raw = await db.get_all_checkins_for_day(day)
    today_dicts = [dict(c) for c in today_raw]
    yesterday_dicts = None
    if day > 1:
        prev_raw = await db.get_all_checkins_for_day(day - 1)
        yesterday_dicts = [dict(c) for c in prev_raw] if prev_raw else None

    # Today's food summary across all users (bonus signal for the AI)
    food_lines = []
    for c in today_dicts:
        try:
            entries = await db.get_diet_entries(c["telegram_id"], day)
            if not entries:
                continue
            short = [f"{e.get('entry_text','')[:40]}" for e in entries]
            food_lines.append(f"  {c['name']}: " + "; ".join(short))
        except Exception:
            pass
    food_summary = "\n".join(food_lines)

    from bot.utils.luke_ai import generate_spicy_moment
    start = time_mod.monotonic()
    text = await generate_spicy_moment(day, today_dicts, yesterday_dicts, food_summary)
    latency_ms = int((time_mod.monotonic() - start) * 1000)
    await db.log_event(None, None, "ai_spicy", f"day={day} fired={'yes' if text else 'no'}", latency_ms=latency_ms)
    if not text:
        return
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.warning("Failed to post spicy moment: %s", e)


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
    """Register all daily scheduled jobs.

    Schedule (decided 2026-04-16):
      7am  ET            morning card (greeting + yesterday recap + bookshelf + today)
      9am  ET            DM reminder for users with incomplete yesterday tasks
      12pm PT (3pm ET)   yesterday locks; admin warnings for incompletes
      10pm ET            same-day DM nudge for east-coast users
      10pm PT (1am ET)   same-day DM nudge for west-coast users
      8pm  ET Sunday     weekly digest
      3am  ET            db backup

    Standalone evening scoreboard removed — folded into morning card.
    """
    job_queue.run_daily(
        morning_card_job, time=time(7, 0, tzinfo=ET), name="morning_card"
    )
    job_queue.run_daily(
        morning_after_reminder_job, time=time(9, 0, tzinfo=ET), name="morning_after_reminder"
    )
    job_queue.run_daily(
        noon_cutoff_job, time=time(12, 0, tzinfo=PT), name="noon_cutoff"
    )
    job_queue.run_daily(
        nudge_job_et, time=time(22, 0, tzinfo=ET), name="nudge_et"
    )
    job_queue.run_daily(
        nudge_job_ct, time=time(22, 0, tzinfo=CT), name="nudge_ct"
    )
    job_queue.run_daily(
        nudge_job_mt, time=time(22, 0, tzinfo=MT), name="nudge_mt"
    )
    job_queue.run_daily(
        nudge_job_pt, time=time(22, 0, tzinfo=PT), name="nudge_pt"
    )
    job_queue.run_daily(
        weekly_digest_job, time=time(20, 0, tzinfo=ET), name="weekly_digest"
    )
    # Spicy moment fires at 9pm ET — late enough that most logging is done,
    # early enough that east coast is still awake to react.
    job_queue.run_daily(
        spicy_moment_job, time=time(21, 0, tzinfo=ET), name="spicy_moment"
    )
    job_queue.run_daily(
        daily_backup_job, time=time(3, 0, tzinfo=ET), name="daily_backup"
    )
