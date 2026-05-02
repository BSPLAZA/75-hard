"""Scheduled daily jobs for the 75 Hard bot using python-telegram-bot's JobQueue."""

import logging
import pytz
import time as time_mod
from collections import Counter, defaultdict
from datetime import date, time

from telegram.ext import ContextTypes

from bot.config import ADMIN_USER_ID, CHALLENGE_DAYS, CHALLENGE_START_DATE
from bot.handlers.daily_card import post_daily_card
from bot.release_notes import CURRENT_VERSION, build_announcement
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
      2. Yesterday's recap image (snapshot — lock isn't until midnight PT tonight)
      3. Bookshelf image (if anyone read yesterday)
      4. Today's daily card with buttons
    """
    db = context.bot_data["db"]
    day = get_day_number(CHALLENGE_START_DATE, today_et())
    if not (1 <= day <= CHALLENGE_DAYS):
        return

    chat_id = context.bot_data.get("group_chat_id")

    # 0. Release notes — fire any unseen user-facing notes before the morning sequence.
    #    Marker only advances on successful post, so a failed send retries tomorrow.
    #    Also stamps last_release_announce_at so the deploy-time path debounces
    #    against this morning post (avoids back-to-back announcements when a
    #    deploy lands shortly after the 7am card).
    if chat_id:
        try:
            from datetime import datetime as _dt, timezone as _tz
            raw = await db.get_setting("last_announced_release_version")
            last_seen = int(raw) if raw is not None else None
            announcement = build_announcement(last_seen)
            if announcement:
                await context.bot.send_message(chat_id=chat_id, text=announcement)
                await db.set_setting("last_announced_release_version", str(CURRENT_VERSION))
                await db.set_setting(
                    "last_release_announce_at", _dt.now(_tz.utc).isoformat(),
                )
                logger.info("release-notes: posted announcement, marker → v%d", CURRENT_VERSION)
        except Exception as e:
            logger.warning("release-notes: announcement failed (%s); will retry tomorrow", e)

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
        await db.log_scheduled_emission("morning", ai_message)
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
                f"Backfill until midnight PT tonight."
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
            "You can also backfill until midnight PT tomorrow night."
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
        await db.log_scheduled_emission("weekly", reflection)

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
    """9 AM ET -- Proactive morning nudge: per unmarked yesterday task, ask the user
    whether they did it (and forgot to log) or missed it (and need penance).

    Upgrades the static nudge to the disambiguation flow described in the design
    (Flow A). The user's DM reply routes through chat_with_luke, which calls
    backfill_task or declare_penance per task per critical_rule 8.

    The outbound nudge is also written to conversation_log (telegram_id=user_id,
    source='dm', user_message='[bot_nudge:morning_after]') so when the user replies,
    Luke's history hydrate picks up the nudge as prior context — otherwise Luke
    would see the user's reply in a vacuum.
    """
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

        missing_list = "\n".join(f"  • {m.lower()}" for m in missing)
        nudge_text = (
            f"yo — didn't see these from yesterday (day {yesterday}):\n\n"
            f"{missing_list}\n\n"
            f"for each one tell me — did you do it and forget to log? "
            f"or you missed it and need penance? "
            f"backfill closes at midnight pacific tonight, after that it's locked fr."
        )
        try:
            await context.bot.send_message(chat_id=c["telegram_id"], text=nudge_text)
            # Persist in conversation_log so Luke's history hydrate sees the nudge
            # as prior context when the user replies.
            await db.add_conversation_log(
                telegram_id=c["telegram_id"],
                user_name=user["name"],
                source="dm",
                user_message="[bot_nudge:morning_after]",
                luke_response=nudge_text,
                tools_called=None,
            )
        except Exception as e:
            logger.warning("morning_after nudge failed for %s: %s", c.get("name"), e)


async def cutoff_warning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """11 PM PT -- DM each user with incomplete yesterday tasks; lock is in 1 hour.

    Last call before midnight_cutoff_job locks yesterday for good. Aimed at users
    like Cam who silently lose a day because they missed the morning reminder
    and never realized the cutoff was approaching.

    Schedule moved from 11am PT to 11pm PT in v51 — group asked for full-day
    backfill flexibility ("midnight PT next day"), so the warning slid to one
    hour before midnight.
    """
    db = context.bot_data["db"]
    from bot.utils.progress import get_current_challenge_day
    day = await get_current_challenge_day(db)
    yesterday = day - 1
    if yesterday < 1:
        await db.log_event(None, None, "cutoff_warning", f"day={yesterday} skipped=pre_day1")
        return

    sent = 0
    checkins = await db.get_all_checkins_for_day(yesterday)
    for c in checkins:
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
                    f"⏰ ONE HOUR until day {yesterday} locks. you're still incomplete:\n\n"
                    f"{missing_list}\n\n"
                    f"hit me back with what you actually did and I'll backfill it. "
                    f"after midnight PT the day is permanently closed."
                ),
            )
            sent += 1
        except Exception as e:
            logger.warning("cutoff_warning DM failed for %s: %s", c.get("name"), e)
    await db.log_event(None, None, "cutoff_warning", f"day={yesterday} sent={sent}")


async def midnight_cutoff_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """12 AM PT (midnight) -- Lock previous day, sweep penance state, alert admin.

    Schedule moved from noon PT to midnight PT in v51 — group asked for the
    latest possible cutoff that still preserves the day-boundary semantic.

    Three sweeps (in order):
      1. Resolve makeup-day penances. Walk every penance_log row where
         makeup_day == yesterday (= day - 1, the day that just locked).
         Check the user's checkin column for that task — recovery rule per
         task in bot.penance.is_target_met (water counter; booleans = column
         bool). Update status to 'recovered' or 'failed'.
      2. Auto-create penance for unresolved misses. Walk yesterday's
         incomplete checkins. For each penance-able task that wasn't done,
         and where no penance row exists yet, create one with
         makeup_day=today (= day). User gets a DM telling them what they
         owe today.
      3. For any user whose penance just failed in step 1 (or who has
         binary diet violations not in arbitration), DM them the self-fail
         payment surface and warn admin. The DM gives Venmo + Zelle so they
         can pay the residual buy-in to the prize pool.

    NOTE on day reference: at 00:00 PT we're at 03:00 ET, which is past the
    ET calendar rollover but BEFORE the next 7am ET morning card posts.
    daily_cards still holds the card for the day that JUST ended (the next
    day's card writes 4 hours from now). So:
      - `get_current_challenge_day(db)` returns the just-ended day → that's
        the day we lock here. Bind it to `yesterday`.
      - `day` = yesterday + 1 is the new day starting at this midnight and
        will get its card posted in 4 hours. New penances created here use
        makeup_day=day so the makeup window aligns with the new active day.
    """
    from bot.config import (
        BUY_IN, PRIZE_POOL_VENMO_USERNAME, PRIZE_POOL_ZELLE_PHONE,
    )
    from bot.penance import PENANCE_ABLE_TASKS, TASK_TARGETS, is_target_met
    from bot.utils.progress import get_current_challenge_day

    db = context.bot_data["db"]
    yesterday = await get_current_challenge_day(db)  # the day whose card was active until now
    if yesterday < 1:
        return
    day = yesterday + 1  # the new day starting at this midnight

    failed_users: set[int] = set()  # users whose penance just failed → self-fail prompt

    # Sweep 1: resolve penances whose makeup_day was yesterday.
    # Walk every active user; cheap (5 users × ~few penances each).
    active_users = await db.get_active_users()
    for u in active_users:
        u = dict(u)
        rows = await db.get_penances_for_makeup_day(u["telegram_id"], yesterday)
        if not rows:
            continue
        checkin = await db.get_checkin(u["telegram_id"], yesterday)
        checkin = dict(checkin) if checkin else None
        for r in rows:
            r = dict(r)
            # Recovery rule: target met on the makeup day.
            # is_target_met checks the doubled target for water, single for booleans.
            # For booleans we honor-system the 2× claim — schema can't distinguish
            # "1 indoor today" from "2 indoor today", so any completed cell counts.
            if checkin and is_target_met(checkin, r["task"], in_penance=(r["task"] == "water")):
                await db.resolve_penance(r["id"], "recovered")
                await db.log_event(
                    u["telegram_id"], None, "penance_recovered",
                    f"task={r['task']} missed_day={r['missed_day']}",
                )
            else:
                await db.resolve_penance(r["id"], "failed")
                failed_users.add(u["telegram_id"])
                await db.log_event(
                    u["telegram_id"], None, "penance_failed",
                    f"task={r['task']} missed_day={r['missed_day']}",
                )

    # Sweep 2: auto-create penances for unresolved yesterday misses.
    # Drive from active_users (not just users who have a checkin row) — if a
    # user never logged anything yesterday, get_all_checkins_for_day returns
    # nothing for them and they'd silently skip the auto-penance sweep. Treat
    # missing checkin as all tasks missed.
    for u in active_users:
        u = dict(u)
        c_row = await db.get_checkin(u["telegram_id"], yesterday)
        c = dict(c_row) if c_row else {}
        if c and is_all_complete(c):
            continue
        for task in PENANCE_ABLE_TASKS:
            column, _ = TASK_TARGETS[task]
            if c.get(column):
                continue  # task was actually done
            existing = await db.get_penances_for_missed_day(u["telegram_id"], yesterday)
            if any(dict(r)["task"] == task for r in existing):
                continue  # already declared (in_progress, recovered, or failed)
            await db.add_penance(
                telegram_id=u["telegram_id"],
                missed_day=yesterday,
                makeup_day=day,
                task=task,
            )
            await db.log_event(
                u["telegram_id"], None, "penance_auto_created",
                f"task={task} missed_day={yesterday}",
            )

    # Refresh checkins list for sweep 4 (admin warning).
    checkins = await db.get_all_checkins_for_day(yesterday)

    # Sweep 3: notify users with failed penances + admin warning for binary misses.
    venmo = PRIZE_POOL_VENMO_USERNAME
    zelle = PRIZE_POOL_ZELLE_PHONE
    for uid in failed_users:
        user = await db.get_user(uid)
        if not user or not dict(user)["dm_registered"]:
            continue
        remaining = max(0, 75 - day)
        owed = max(0, BUY_IN - day)  # what's left of the original buy-in
        venmo_line = f"venmo: @{venmo}" if venmo else None
        zelle_line = f"zelle: {zelle}" if zelle else None
        pay_lines = [l for l in (venmo_line, zelle_line) if l]
        pay_block = "\n".join(pay_lines) if pay_lines else "ask the organizer for payment details."
        text = (
            f"yo, your penance didn't land yesterday. that's a fail.\n\n"
            f"${owed} to the prize pool ({remaining} days you didn't finish).\n"
            f"{pay_block}\n\n"
            f"reply 'paid' when sent. /redeem if you want a way back in."
        )
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            await db.log_event(uid, None, "self_fail_payment_dm_sent", f"day={day}")
        except Exception as e:
            logger.warning("self-fail DM failed for uid=%s: %s", uid, e)

    # Sweep 4: admin warning for incomplete users (kept from prior behavior).
    for c in checkins:
        if not is_all_complete(c):
            missing = get_missing_tasks(c)
            user = await db.get_user(c["telegram_id"])
            name = dict(user)["name"] if user else "?"
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=(
                        f"midnight cutoff: {name} incomplete day {yesterday} "
                        f"({', '.join(missing)}). penance auto-created where applicable. "
                        f"binary diet violations need /admin_arbitrate (when wired)."
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

    # Today's food summary across all users — AGGREGATE ONLY, never itemized.
    # Specific food entries are private DM content; the spicy-moment prompt
    # forbids enumeration but the safer path is to not feed raw items at all.
    food_lines = []
    for c in today_dicts:
        try:
            entries = await db.get_diet_entries(c["telegram_id"], day)
            if not entries:
                continue
            total_g = 0
            for e in entries:
                v = e.get("extracted_value")
                # log_food stores extracted_unit='protein_g' (not 'g'). The
                # earlier 'g' filter was a typo and silently zeroed every
                # user's total, making the food signal dead.
                if e.get("extracted_unit") == "protein_g" and v is not None:
                    try:
                        total_g += int(float(v))
                    except (TypeError, ValueError):
                        pass
            # Don't include entry count — it's a behavioral proxy ("logged 12
            # things") and not what we want spicy-moment to riff on.
            if total_g > 0:
                food_lines.append(f"  {c['name']}: {total_g}g protein logged")
        except Exception:
            pass
    food_summary = "\n".join(food_lines)

    from bot.utils.luke_ai import generate_spicy_moment
    start = time_mod.monotonic()
    text = await generate_spicy_moment(day, today_dicts, yesterday_dicts, food_summary)
    latency_ms = int((time_mod.monotonic() - start) * 1000)
    await db.log_event(None, None, "ai_spicy", f"day={day} fired={'yes' if text else 'no'}", latency_ms=latency_ms)
    await db.log_scheduled_emission("spicy", text)
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

    Schedule (revised 2026-05-01 from group feedback — cutoff moved to
    midnight PT for full-day backfill flexibility):
      7am  ET            morning card (greeting + yesterday recap + bookshelf + today)
      9am  ET            DM reminder for users with incomplete yesterday tasks
      11pm PT (2am ET)   final warning DM (1 hour before lock)
      12am PT (3am ET)   yesterday locks; admin warnings for incompletes
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
        cutoff_warning_job, time=time(23, 0, tzinfo=PT), name="cutoff_warning"
    )
    job_queue.run_daily(
        midnight_cutoff_job, time=time(0, 0, tzinfo=PT), name="midnight_cutoff"
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
