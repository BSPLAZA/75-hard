"""Compliance grid DM and admin handlers.

Surfaces bot/utils/compliance_grid.render_compliance_grid into Telegram —
either as a DM ("show me my grid") or as an admin command.
"""

import logging
from typing import Iterable

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import CHALLENGE_START_DATE
from bot.utils.compliance_grid import render_compliance_grid
from bot.utils.progress import get_current_challenge_day, today_et, get_day_number

logger = logging.getLogger(__name__)

CHALLENGE_DAYS = 75


async def _build_grid_for_user(db, user_id: int, today_day: int) -> tuple:
    """Common path: fetch checkins + penances for user, return (user_name, image_buf)."""
    user = await db.get_user(user_id)
    if not user:
        return None, None
    user_name = dict(user)["name"]

    # Build {day → checkin_dict} for every day the user has a row.
    # get_all_checkins_for_day returns rows for one day; we need many days.
    # Cheap approach: query each day individually. With 75 days × 5 users
    # this is ~375 queries on a tiny SQLite DB — milliseconds. Optimize
    # later if it ever shows up in profiling.
    checkins_by_day: dict[int, dict] = {}
    for day in range(1, today_day + 1):
        row = await db.get_checkin(user_id, day)
        if row:
            checkins_by_day[day] = dict(row)

    # All penance rows for this user (any status, any day).
    penance_rows = []
    for day in range(1, today_day + 1):
        rows = await db.get_penances_for_missed_day(user_id, day)
        penance_rows.extend(dict(r) for r in rows)

    # cutoff_passed_through_day = yesterday. midnight PT of today's day rolls
    # at the start of today, so day N-1 is locked once we're in day N.
    cutoff_through = max(today_day - 1, 0)

    image_buf = render_compliance_grid(
        user_name=user_name,
        today_day=today_day,
        challenge_days=CHALLENGE_DAYS,
        checkins_by_day=checkins_by_day,
        penance_rows=penance_rows,
        cutoff_passed_through_day=cutoff_through,
    )
    return user_name, image_buf


async def send_compliance_grid_dm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Render the calling user's compliance grid and DM it back."""
    db = context.bot_data["db"]
    user_id = update.effective_user.id
    today_day = await get_current_challenge_day(db) or max(
        get_day_number(CHALLENGE_START_DATE, today_et()), 1
    )

    name, buf = await _build_grid_for_user(db, user_id, today_day)
    if not buf:
        await update.message.reply_text(
            "couldn't build your grid — are you registered? hit /start if not."
        )
        return
    await update.message.reply_photo(
        photo=buf,
        caption=f"{name}'s compliance, days 1–{today_day}.",
    )


async def admin_compliance_grid_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Admin: render a specific user's grid (or every active user's, stacked).

    Usage:
      /admin_compliance_grid           — every active user
      /admin_compliance_grid @bryan    — just bryan
      /admin_compliance_grid Bryan     — also works (case-insensitive name match)
    """
    from bot.handlers.admin import _is_admin, _admin_reply

    if not _is_admin(update.effective_user.id):
        await _admin_reply(update, context, "Admin only.")
        return

    db = context.bot_data["db"]
    today_day = await get_current_challenge_day(db) or max(
        get_day_number(CHALLENGE_START_DATE, today_et()), 1
    )

    target = " ".join(context.args).strip() if context.args else ""
    target = target.lstrip("@").strip().lower()

    active = await db.get_active_users()
    targets: Iterable[dict] = active
    if target:
        targets = [u for u in active if dict(u)["name"].lower() == target]
        if not targets:
            await _admin_reply(update, context, f"No active user named '{target}'.")
            return

    for u in targets:
        u = dict(u)
        try:
            name, buf = await _build_grid_for_user(db, u["telegram_id"], today_day)
            if buf:
                await update.message.reply_photo(
                    photo=buf,
                    caption=f"{name} · days 1–{today_day}",
                )
        except Exception as e:
            logger.warning("compliance grid failed for %s: %s", u.get("name"), e)
            await _admin_reply(update, context, f"Grid failed for {u.get('name')}: {e}")
