"""Workout logging handlers -- multi-step inline keyboard flow."""

from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import (
    CB_WORKOUT,
    CB_WORKOUT_LOC,
    CB_WORKOUT_TYPE,
    CB_WORKOUT_OUTDOOR,
    CB_WORKOUT_INDOOR,
    CHALLENGE_START_DATE,
    WORKOUT_TYPES,
)
from bot.handlers.daily_card import refresh_card, resolve_day_from_card
from bot.templates.messages import (
    WORKOUT_ALREADY_DONE,
    WORKOUT_BOTH_DONE,
    WORKOUT_LOGGED,
    WORKOUT_PICK_LOCATION,
    WORKOUT_PICK_TYPE,
    WORKOUT_WRONG_LOCATION,
)
from bot.utils.easter_eggs import (
    check_first_completion,
    check_simultaneous_workout,
    record_workout_time,
)
from bot.utils.progress import today_et, get_day_number

# Emoji map for workout types and locations
TYPE_EMOJI = {
    "run": "\U0001f3c3",    # 🏃
    "lift": "\U0001f3cb\ufe0f",  # 🏋️
    "yoga": "\U0001f9d8",   # 🧘
    "bike": "\U0001f6b4",   # 🚴
    "swim": "\U0001f3ca",   # 🏊
    "other": "\U0001f4aa",  # 💪
}

LOC_EMOJI = {
    "outdoor": "\U0001f333",  # 🌳
    "indoor": "\U0001f3e0",   # 🏠
}


def _type_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build the workout-type picker keyboard."""
    buttons = []
    row = []
    for wtype in WORKOUT_TYPES:
        emoji = TYPE_EMOJI.get(wtype, "\U0001f4aa")
        row.append(
            InlineKeyboardButton(
                f"{emoji} {wtype.capitalize()}",
                callback_data=f"{CB_WORKOUT_TYPE}{user_id}_{wtype}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def _location_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build the indoor/outdoor picker keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{LOC_EMOJI['outdoor']} Outdoor",
                    callback_data=f"{CB_WORKOUT_LOC}{user_id}_outdoor",
                ),
                InlineKeyboardButton(
                    f"{LOC_EMOJI['indoor']} Indoor",
                    callback_data=f"{CB_WORKOUT_LOC}{user_id}_indoor",
                ),
            ]
        ]
    )


async def workout_quick_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the direct Outdoor/Indoor button tap on the daily card. One tap, done."""
    query = update.callback_query
    db = context.bot_data["db"]

    location = "outdoor" if query.data == CB_WORKOUT_OUTDOOR else "indoor"

    day_number = await resolve_day_from_card(db, query.message.message_id)
    if not day_number:
        await query.answer("Card not found.", show_alert=True)
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.answer("Register first! DM me /start", show_alert=True)
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await db.create_checkin(update.effective_user.id, day_number, today_et().isoformat())
        checkin = await db.get_checkin(update.effective_user.id, day_number)

    # Check if both workouts already done
    if checkin["workout_1_done"] and checkin["workout_2_done"]:
        await query.answer(WORKOUT_ALREADY_DONE, show_alert=True)
        return

    # Check location conflict (can't do two of the same)
    if checkin["workout_1_done"] and checkin["workout_1_location"] == location:
        other = "outdoor" if location == "indoor" else "indoor"
        await query.answer(
            WORKOUT_WRONG_LOCATION.format(other_loc=location, needed_loc=other),
            show_alert=True,
        )
        return

    name = user["name"]
    slot, just_completed = await db.log_workout(update.effective_user.id, day_number, "workout", location)

    record_workout_time(update.effective_user.id, day_number)

    if slot == 2:
        await query.answer("2/2 done 💪", show_alert=True)
    else:
        await query.answer(f"1/2 — {location} logged", show_alert=True)

    await refresh_card(context, day_number)
    await db.log_event(update.effective_user.id, name, "workout_log", f"{location}, slot={slot}")

    await check_simultaneous_workout(context, update.effective_user.id, name, day_number)
    if just_completed:
        await check_first_completion(context, name, day_number)


async def workout_start_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the Workout button tap on the daily card."""
    query = update.callback_query
    db = context.bot_data["db"]

    day_number = await resolve_day_from_card(db, query.message.message_id)
    if not day_number:
        await query.answer("Card not found.", show_alert=True)
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await query.answer("Register first! DM me /start", show_alert=True)
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await db.create_checkin(
            update.effective_user.id, day_number, today.isoformat()
        )
        checkin = await db.get_checkin(update.effective_user.id, day_number)

    # Both workouts already done?
    if checkin["workout_1_done"] and checkin["workout_2_done"]:
        await query.answer(WORKOUT_ALREADY_DONE, show_alert=True)
        return

    await query.answer()

    username = update.effective_user.username or update.effective_user.first_name
    await query.message.reply_text(
        WORKOUT_PICK_TYPE.format(username=username),
        reply_markup=_type_keyboard(update.effective_user.id),
    )


async def workout_type_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle workout type selection."""
    query = update.callback_query
    data = query.data  # e.g. "wtype_12345_run"

    # Parse user_id and type from callback data
    suffix = data[len(CB_WORKOUT_TYPE):]  # "12345_run"
    parts = suffix.split("_", 1)
    if len(parts) != 2:
        await query.answer("Invalid selection.", show_alert=True)
        return

    target_user_id = int(parts[0])
    wtype = parts[1]

    # Only the user who started the flow can interact
    if update.effective_user.id != target_user_id:
        await query.answer("This isn't your workout flow!", show_alert=True)
        return

    if wtype not in WORKOUT_TYPES:
        await query.answer("Unknown workout type.", show_alert=True)
        return

    await query.answer()

    # If "other", ask user to type the workout name
    if wtype == "other":
        context.user_data["awaiting_workout_name"] = True
        context.user_data["workout_picker_message_id"] = query.message.message_id
        context.user_data["workout_picker_chat_id"] = query.message.chat_id
        try:
            await query.edit_message_text(
                "💪 What kind of workout? Type it below (e.g. hiking, boxing, basketball):"
            )
        except BadRequest:
            pass
        return

    # Store the pending type
    context.user_data["pending_workout_type"] = wtype

    emoji = TYPE_EMOJI.get(wtype, "\U0001f4aa")
    username = update.effective_user.username or update.effective_user.first_name

    try:
        await query.edit_message_text(
            WORKOUT_PICK_LOCATION.format(
                username=username, emoji=emoji, wtype=wtype.capitalize()
            ),
            reply_markup=_location_keyboard(update.effective_user.id),
        )
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def workout_location_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle workout location selection -- logs the workout."""
    query = update.callback_query
    db = context.bot_data["db"]
    data = query.data  # e.g. "wloc_12345_outdoor"

    suffix = data[len(CB_WORKOUT_LOC):]  # "12345_outdoor"
    parts = suffix.split("_", 1)
    if len(parts) != 2:
        await query.answer("Invalid selection.", show_alert=True)
        return

    target_user_id = int(parts[0])
    location = parts[1]

    if update.effective_user.id != target_user_id:
        await query.answer("This isn't your workout flow!", show_alert=True)
        return

    if location not in ("outdoor", "indoor"):
        await query.answer("Unknown location.", show_alert=True)
        return

    day_number = max(get_day_number(CHALLENGE_START_DATE, today_et()), 1)

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await query.answer("No check-in found for today.", show_alert=True)
        return

    # Validate location: second workout must be opposite of first
    if checkin["workout_1_done"] and not checkin["workout_2_done"]:
        first_loc = checkin["workout_1_location"]
        opposite = "indoor" if first_loc == "outdoor" else "outdoor"
        if location != opposite:
            await query.answer(
                WORKOUT_WRONG_LOCATION.format(
                    other_loc=first_loc, needed_loc=opposite
                ),
                show_alert=True,
            )
            return

    wtype = context.user_data.pop("pending_workout_type", "other")
    emoji = TYPE_EMOJI.get(wtype, "\U0001f4aa")

    user = await db.get_user(update.effective_user.id)
    name = user["name"] if user else update.effective_user.first_name

    slot, just_completed = await db.log_workout(update.effective_user.id, day_number, wtype, location)
    await db.log_event(update.effective_user.id, name, "workout_log", f"type={wtype} location={location} slot={slot}")
    loc_emoji = LOC_EMOJI.get(location, "")

    # Record workout time for simultaneous detection
    record_workout_time(update.effective_user.id, day_number)

    await query.answer()

    if slot == 2:
        confirm_text = WORKOUT_BOTH_DONE.format(
            emoji=emoji,
            name=name,
            location=f"{loc_emoji} {location.capitalize()}",
            wtype=wtype.capitalize(),
        )
    else:
        confirm_text = WORKOUT_LOGGED.format(
            emoji=emoji,
            name=name,
            location=f"{loc_emoji} {location.capitalize()}",
            wtype=wtype.capitalize(),
            num=slot,
        )

    try:
        await query.edit_message_text(confirm_text)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise

    await refresh_card(context, day_number)

    # Easter eggs
    await check_simultaneous_workout(context, update.effective_user.id, name, day_number)
    if just_completed:
        await check_first_completion(context, name, day_number)


async def workout_undo_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /workout_undo -- clear the most recent workout."""
    db = context.bot_data["db"]
    today = today_et()
    day_number = get_day_number(CHALLENGE_START_DATE, today)

    if day_number < 1:
        await update.message.reply_text("The challenge hasn't started yet!")
        return

    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Register first! DM me /start")
        return

    checkin = await db.get_checkin(update.effective_user.id, day_number)
    if not checkin:
        await update.message.reply_text("No check-in found for today.")
        return

    slot = await db.undo_last_workout(update.effective_user.id, day_number)
    if slot == 0:
        await update.message.reply_text("No workouts to undo today.")
    else:
        await update.message.reply_text(f"Workout {slot} cleared. Re-log when ready.")
        await refresh_card(context, day_number)


async def handle_custom_workout_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for custom workout type. Returns True if consumed."""
    if not context.user_data.get("awaiting_workout_name"):
        return False

    context.user_data.pop("awaiting_workout_name", None)
    custom_name = update.message.text.strip().lower()
    context.user_data["pending_workout_type"] = custom_name

    username = update.effective_user.username or update.effective_user.first_name

    # Edit the picker message to show location options
    picker_msg_id = context.user_data.pop("workout_picker_message_id", None)
    picker_chat_id = context.user_data.pop("workout_picker_chat_id", None)
    if picker_msg_id and picker_chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=picker_chat_id,
                message_id=picker_msg_id,
                text=WORKOUT_PICK_LOCATION.format(
                    username=username, emoji="💪", wtype=custom_name.title()
                ),
                reply_markup=_location_keyboard(update.effective_user.id),
            )
        except Exception:
            # Fallback: send a new message with the location picker
            await update.message.reply_text(
                WORKOUT_PICK_LOCATION.format(
                    username=username, emoji="💪", wtype=custom_name.title()
                ),
                reply_markup=_location_keyboard(update.effective_user.id),
            )

    return True


def get_workout_handlers() -> list:
    """Return all workout-related handlers."""
    return [
        CallbackQueryHandler(workout_quick_callback, pattern=f"^{CB_WORKOUT_OUTDOOR}$"),
        CallbackQueryHandler(workout_quick_callback, pattern=f"^{CB_WORKOUT_INDOOR}$"),
        CallbackQueryHandler(workout_start_callback, pattern=f"^{CB_WORKOUT}$"),
        CallbackQueryHandler(
            workout_type_callback, pattern=f"^{CB_WORKOUT_TYPE}"
        ),
        CallbackQueryHandler(
            workout_location_callback, pattern=f"^{CB_WORKOUT_LOC}"
        ),
        CommandHandler("workout_undo", workout_undo_command),
    ]
