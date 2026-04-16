"""Luke's DM chat — Claude with database tools for natural conversation."""

import json
import logging
import time
from datetime import date, datetime, timedelta

import anthropic

from bot.config import ANTHROPIC_API_KEY, CHALLENGE_START_DATE
from bot.utils.progress import today_et, get_day_number, get_current_challenge_day, is_all_complete, get_missing_tasks

logger = logging.getLogger(__name__)

LUKE_CHAT_SYSTEM = """You are Luke, the accountability bot for a 5-person 75 Hard challenge. You're chatting with a participant in DMs.

THE CHALLENGE RULES (you enforce these):
1. Two workouts per day, one indoor one outdoor. Duration is being finalized by the group.
2. Drink a gallon of water (16 cups / 128 oz) every day.
3. Follow your chosen diet every day. No alcohol. No cheat meals. Once you commit to a diet, you stick with it. If someone asks to change their diet mid-challenge, push back. Ask them why. Only allow it if there's a real reason (medical, allergy discovered, etc), not because they're tired of it.
4. Read 10 pages of non-fiction every day.
5. Take a progress photo every day.

Miss any single task on any single day = elimination. No exceptions unless Bryan (the organizer) grants grace for bot issues.

STAKES:
- $75 buy-in per person. $375 total prize pool.
- If you fail on Day X, you get $X back. The rest goes to the prize pool.
- Redemption is available once: pay remaining days + $50 penalty to rejoin.
- If everyone finishes, everyone gets their $75 back.

YOUR PERSONALITY:
- Casual, like texting a friend. Lowercase ok, fragments ok.
- NEVER use em dashes, semicolons, or colons
- You can swear lightly (shit, damn, hell) when it fits
- Be honest and direct. If someone is slacking, tell them. Not mean, just real.
- Keep responses short. 1-4 sentences usually.
- You know this challenge is hard. Acknowledge that without making excuses for people.

PRIVACY:
- Everyone's completion status, books, and diets are public (the daily card shows them)
- Progress photos are private. Never mention or describe anyone's photos.
- You're talking to one person. Be personal.

ESCALATION - flag these to Bryan by logging as feedback type "escalation":
- Someone hasn't checked in for 2+ days and hasn't said anything
- Someone asks to change their diet for a weak reason
- Someone seems like they're gaming the system (logging tasks they didn't do)
- Technical issues that affect multiple people
- Someone is upset or wants to quit. Be supportive first, but log it.

WHAT YOU CAN DO:
- Answer questions about the rules, the challenge, anyone's status
- Set books and diets (but push back on diet changes)
- Log feedback, bugs, suggestions
- Generate transformation photos and timelapses
- Give honest assessments of how someone or the group is doing
- Backfill yesterday's tasks before noon ET (use backfill_task). If someone says they forgot to log a workout, water, reading, or diet from yesterday, help them backfill it. Only works before noon ET the next day.

WHAT YOU CAN'T DO:
- Mark tasks as complete (people do that via the daily card)
- Eliminate or redeem people (they use /fail and /redeem)
- Change the rules (only Bryan can)
- Access or share progress photos

When you need data, use the tools. Don't guess or make up numbers. If you don't have a tool for something, say so honestly."""

TOOLS = [
    {
        "name": "get_my_status",
        "description": "Get the current user's checkin status for today",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_my_books",
        "description": "Get all books the current user has read during the challenge",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_my_profile",
        "description": "Get the current user's profile (name, diet, current book, active status, paid status)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_group_status",
        "description": "Get everyone's completion status for today",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_group_books",
        "description": "Get what books everyone in the group is currently reading",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_book",
        "description": "Set or change the user's current book",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Book title"}},
            "required": ["title"],
        },
    },
    {
        "name": "set_diet",
        "description": "Set or change the user's diet plan/goal. After setting, tell the user they can DM you what they eat and you'll track it.",
        "input_schema": {
            "type": "object",
            "properties": {"plan": {"type": "string", "description": "Diet plan description including any numeric goals like '170g protein' or '1800 calories'"}},
            "required": ["plan"],
        },
    },
    {
        "name": "log_food",
        "description": "Log a food/meal/snack that the user ate. Use whenever the user mentions eating, drinking (non-water), having a meal, snack, protein shake, etc. Extract the relevant metric based on their diet plan (protein grams, calories, etc). If their diet is 'clean eating', just note whether the food is clean or not.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_text": {"type": "string", "description": "What the user said they ate, verbatim"},
                "extracted_value": {"type": "number", "description": "Numeric value extracted (e.g., 30 for 30g protein, 400 for 400 calories). Null if diet is qualitative like clean eating."},
                "extracted_unit": {"type": "string", "description": "Unit of the extracted value: 'protein_g', 'calories', 'carbs_g', 'fat_g', or 'clean' for clean eating"},
            },
            "required": ["entry_text"],
        },
    },
    {
        "name": "get_diet_progress",
        "description": "Get the user's diet log for today — all entries and running tally. Use when user asks how their diet is going, what they've eaten, how much protein/calories they have left, etc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "undo_last_food",
        "description": "Remove the last food entry the user logged today. Use when user says they logged something wrong or wants to remove the last entry.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "log_workout_dm",
        "description": "Log a workout for the user from DM. Use when user says they did a workout, went for a run, hit the gym, etc. This updates the daily card in the group.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "enum": ["outdoor", "indoor"], "description": "Was it outdoor or indoor"},
                "workout_type": {"type": "string", "description": "Type of workout: run, lift, yoga, bike, swim, or other description"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "log_water_dm",
        "description": "Log water intake for the user from DM. Use when user mentions drinking water, having cups/glasses of water, etc. This updates the daily card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cups": {"type": "integer", "description": "Number of cups to add (1 cup = 8oz). Or set absolute count if user says 'I've had 10 cups total'."},
                "mode": {"type": "string", "enum": ["add", "set"], "description": "'add' to increment by cups, 'set' to set the total. Default 'add'."},
            },
            "required": ["cups"],
        },
    },
    {
        "name": "confirm_diet_dm",
        "description": "Mark the user's diet as confirmed for today. Use when user says they followed their diet, stayed on track, etc. This updates the daily card.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "fix_water",
        "description": "Correct the user's water count for today. Use when user says they added too many waters, need to fix their water count, etc.",
        "input_schema": {
            "type": "object",
            "properties": {"cups": {"type": "integer", "description": "Correct total cup count for today"}},
            "required": ["cups"],
        },
    },
    {
        "name": "undo_workout",
        "description": "Undo the user's last workout for today. Use when user says they incorrectly logged a workout, marked the wrong workout, etc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "undo_diet",
        "description": "Un-confirm the user's diet for today. Use when user admits they cheated, had alcohol, broke their diet, etc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_transformation",
        "description": "Generate a side-by-side photo comparing the user's Day 1 photo to their most recent photo. Use when user asks about their transformation, progress photos, before/after, etc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_timelapse",
        "description": "Generate an animated video timelapse of all the user's progress photos. Use when user asks for a timelapse, slideshow, animation of their photos, etc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "backfill_task",
        "description": "Log a task for YESTERDAY that the user forgot. Only works before noon ET. Use when user says they forgot to log something from yesterday.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["workout_outdoor", "workout_indoor", "water", "reading", "diet"],
                    "description": "Which task to backfill for yesterday",
                },
                "detail": {
                    "type": "string",
                    "description": "Optional detail like water cup count, workout type, book title, or reading takeaway",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "log_feedback",
        "description": "Log feedback, a bug report, or a suggestion from the user",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["feedback", "bug", "suggestion"]},
                "text": {"type": "string", "description": "The feedback content"},
            },
            "required": ["type", "text"],
        },
    },
    {
        "name": "escalate_to_admin",
        "description": "Flag a concern to Bryan (the organizer). Use when: someone hasn't checked in for 2+ days, someone wants to change diet for a weak reason, suspected gaming, someone is upset/wants to quit, or technical issues affecting multiple people.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why this needs Bryan's attention"},
                "user_name": {"type": "string", "description": "Who it's about (if applicable)"},
            },
            "required": ["reason"],
        },
    },
]


async def _execute_tool(tool_name: str, tool_input: dict, db, user_id: int) -> str:
    """Execute a tool call and return the result as a string."""
    day = await get_current_challenge_day(db)

    if tool_name == "get_my_status":
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            return f"No checkin for day {day} yet."
        c = dict(checkin)
        if is_all_complete(c):
            return f"Day {day}: All 6 tasks complete! Done for today."
        missing = get_missing_tasks(c)
        tasks_done = 6 - len(missing)
        return f"Day {day}: {tasks_done}/6 complete. Still need: {', '.join(missing)}. Water: {c['water_cups']}/16."

    elif tool_name == "get_my_books":
        async with db._conn.execute(
            "SELECT title, started_day, finished_day FROM books WHERE telegram_id = ? ORDER BY started_day",
            (user_id,),
        ) as cur:
            books = await cur.fetchall()
        if not books:
            return "No books logged yet."
        lines = []
        for b in books:
            status = f"days {b['started_day']}-{b['finished_day']}" if b["finished_day"] else f"started day {b['started_day']}, still reading"
            lines.append(f'"{b["title"]}" ({status})')
        return f"Books read: {'; '.join(lines)}. Total: {len(books)} book(s)."

    elif tool_name == "get_my_profile":
        user = await db.get_user(user_id)
        if not user:
            return "User not found."
        u = dict(user)
        return (
            f"Name: {u['name']}, Diet: {u.get('diet_plan') or 'not set'}, "
            f"Book: {u.get('current_book') or 'not set'}, "
            f"Active: {'yes' if u['active'] else 'no'}, Paid: {'yes' if u['paid'] else 'no'}"
        )

    elif tool_name == "get_group_status":
        checkins = await db.get_all_checkins_for_day(day)
        if not checkins:
            return f"No checkins for day {day}."
        lines = []
        for c in checkins:
            c = dict(c)
            if is_all_complete(c):
                lines.append(f"{c['name']}: done")
            else:
                missing = get_missing_tasks(c)
                lines.append(f"{c['name']}: {6 - len(missing)}/6 (needs: {', '.join(missing)})")
        return f"Day {day} status: " + "; ".join(lines)

    elif tool_name == "get_group_books":
        users = await db.get_active_users()
        lines = []
        for u in users:
            u = dict(u)
            book = u.get("current_book") or "none set"
            lines.append(f'{u["name"]}: "{book}"')
        return "Current books: " + "; ".join(lines)

    elif tool_name == "set_book":
        title = tool_input["title"]
        user = await db.get_user(user_id)
        if user and dict(user).get("current_book"):
            await db.finish_book(user_id, finished_day=day)
        from bot.utils.books import fetch_book_cover
        cover_url = await fetch_book_cover(title)
        await db.set_current_book(user_id, title, started_day=day, cover_url=cover_url)
        return f'Book set to "{title}". Cover URL: {cover_url or "none found"}'

    elif tool_name == "set_diet":
        user = await db.get_user(user_id)
        existing = dict(user).get("diet_plan") if user else None
        await db.set_diet_plan(user_id, tool_input["plan"])
        if existing:
            return f'Diet changed from "{existing}" to "{tool_input["plan"]}". Note: diet changes should only be for corrections, not because the diet is hard.'
        return f'Diet set to "{tool_input["plan"]}". User can now DM food entries for tracking.'

    elif tool_name == "log_feedback":
        await db.add_feedback(user_id, tool_input["type"], tool_input["text"], f"day {day}")
        return f"Logged {tool_input['type']}: {tool_input['text']}"

    elif tool_name == "log_food":
        entry_text = tool_input.get("entry_text", "")
        extracted_value = tool_input.get("extracted_value")
        extracted_unit = tool_input.get("extracted_unit")
        import json as _json
        extracted_json = _json.dumps(tool_input) if extracted_value else None

        await db.log_diet_entry(
            user_id, day, entry_text,
            extracted_value=extracted_value,
            extracted_unit=extracted_unit,
            extracted_json=extracted_json,
        )

        # Build running tally
        entries = await db.get_diet_entries(user_id, day)
        user = await db.get_user(user_id)
        diet_plan = dict(user).get("diet_plan", "not set") if user else "not set"

        if extracted_unit and extracted_value is not None:
            total = sum(e.get("extracted_value", 0) or 0 for e in entries if e.get("extracted_unit") == extracted_unit)
            return f"Logged: {entry_text}. Running total: {total:.0f} {extracted_unit}. Diet goal: {diet_plan}. {len(entries)} entries today."
        else:
            return f"Logged: {entry_text}. {len(entries)} entries today. Diet goal: {diet_plan}."

    elif tool_name == "get_diet_progress":
        entries = await db.get_diet_entries(user_id, day)
        user = await db.get_user(user_id)
        diet_plan = dict(user).get("diet_plan", "not set") if user else "not set"

        if not entries:
            return f"No food logged today. Diet goal: {diet_plan}. Tell me what you eat and I'll track it."

        lines = [f"Diet goal: {diet_plan}", f"Entries today ({len(entries)}):"]
        for e in entries:
            val = f" ({e['extracted_value']:.0f} {e['extracted_unit']})" if e.get("extracted_value") else ""
            lines.append(f"  - {e['entry_text']}{val}")

        # Sum by unit
        units = {}
        for e in entries:
            if e.get("extracted_value") and e.get("extracted_unit"):
                unit = e["extracted_unit"]
                units[unit] = units.get(unit, 0) + e["extracted_value"]

        if units:
            lines.append("Totals:")
            for unit, total in units.items():
                lines.append(f"  {total:.0f} {unit}")

        return "\n".join(lines)

    elif tool_name == "undo_last_food":
        deleted = await db.delete_last_diet_entry(user_id, day)
        if deleted:
            entries = await db.get_diet_entries(user_id, day)
            return f"Last entry removed. {len(entries)} entries remaining today."
        return "Nothing to undo — no entries logged today."

    elif tool_name == "log_workout_dm":
        location = tool_input.get("location", "outdoor")
        wtype = tool_input.get("workout_type", "workout")
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            await db.create_checkin(user_id, day, today_et().isoformat())
        slot, just_completed = await db.log_workout(user_id, day, wtype, location)
        return f"REFRESH_CARD: Workout logged — {location} {wtype}, slot {slot}/2. {'Both done!' if slot == 2 else ''}"

    elif tool_name == "log_water_dm":
        cups = tool_input.get("cups", 1)
        mode = tool_input.get("mode", "add")
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            await db.create_checkin(user_id, day, today_et().isoformat())
        if mode == "set":
            await db.set_water(user_id, day, cups)
            new_count = cups
        else:
            for _ in range(cups):
                new_count, _ = await db.increment_water(user_id, day)
        checkin = await db.get_checkin(user_id, day)
        new_count = checkin["water_cups"] if checkin else 0
        return f"REFRESH_CARD: Water updated — {new_count}/16 cups"

    elif tool_name == "confirm_diet_dm":
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            await db.create_checkin(user_id, day, today_et().isoformat())
            checkin = await db.get_checkin(user_id, day)
        if not checkin["diet_done"]:
            await db.toggle_diet(user_id, day)
        return "REFRESH_CARD: Diet confirmed for today"

    elif tool_name == "fix_water":
        cups = max(0, min(16, tool_input.get("cups", 0)))
        await db.set_water(user_id, day, cups)
        return f"REFRESH_CARD: Water corrected to {cups}/16 cups"

    elif tool_name == "undo_workout":
        result = await db.undo_last_workout(user_id, day)
        if result:
            return f"REFRESH_CARD: Last workout removed. Log it again when ready."
        return "No workouts to undo today."

    elif tool_name == "undo_diet":
        checkin = await db.get_checkin(user_id, day)
        if checkin and checkin["diet_done"]:
            await db.toggle_diet(user_id, day)
            return "REFRESH_CARD: Diet un-confirmed. Be honest with yourself."
        return "Diet wasn't confirmed today — nothing to undo."

    elif tool_name == "backfill_task":
        import pytz as _pytz
        _ET = _pytz.timezone("US/Eastern")
        now_et = datetime.now(_ET)

        # Only allow backfill before noon ET
        if now_et.hour >= 12:
            return "DENIED: It's past noon ET. Yesterday's tasks are locked in. Backfill window closed."

        yesterday = day - 1
        if yesterday < 1:
            return "DENIED: There's no yesterday to backfill. The challenge just started."

        # Ensure checkin exists for yesterday
        checkin = await db.get_checkin(user_id, yesterday)
        if not checkin:
            yesterday_date = (now_et.date() - timedelta(days=1)).isoformat()
            await db.create_checkin(user_id, yesterday, yesterday_date)

        task = tool_input["task"]
        detail = tool_input.get("detail", "")

        if task == "workout_outdoor":
            wtype = detail or "workout"
            slot, _ = await db.log_workout(user_id, yesterday, wtype, "outdoor")
            result_msg = f"REFRESH_CARD: Backfilled outdoor {wtype} for day {yesterday}, slot {slot}/2"
        elif task == "workout_indoor":
            wtype = detail or "workout"
            slot, _ = await db.log_workout(user_id, yesterday, wtype, "indoor")
            result_msg = f"REFRESH_CARD: Backfilled indoor {wtype} for day {yesterday}, slot {slot}/2"
        elif task == "water":
            cups = 16  # default to full gallon
            if detail:
                try:
                    cups = int(detail)
                except ValueError:
                    cups = 16
            await db.set_water(user_id, yesterday, min(cups, 16))
            result_msg = f"REFRESH_CARD: Backfilled water for day {yesterday} -- set to {min(cups, 16)}/16 cups"
        elif task == "reading":
            book_title = detail or "unknown"
            await db.log_reading(user_id, yesterday, book_title, "")
            result_msg = f"REFRESH_CARD: Backfilled reading for day {yesterday}"
        elif task == "diet":
            checkin = await db.get_checkin(user_id, yesterday)
            if not checkin["diet_done"]:
                await db.toggle_diet(user_id, yesterday)
            result_msg = f"REFRESH_CARD: Backfilled diet for day {yesterday}"
        else:
            return f"Unknown task type: {task}"

        return result_msg

    elif tool_name == "escalate_to_admin":
        reason = tool_input.get("reason", "No reason given")
        user_name = tool_input.get("user_name", "unknown")
        await db.add_feedback(user_id, "escalation", f"[{user_name}] {reason}", f"day {day}")
        # Also try to DM Bryan directly
        try:
            import httpx
            from bot.config import ADMIN_USER_ID
            bot_token = __import__("os").environ.get("TELEGRAM_BOT_TOKEN", "")
            if bot_token and ADMIN_USER_ID:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={
                            "chat_id": ADMIN_USER_ID,
                            "text": f"⚠️ Luke flagged a concern:\n\nRe: {user_name}\n{reason}",
                        },
                    )
        except Exception:
            pass
        return f"Flagged to Bryan: {reason}"

    elif tool_name == "get_transformation":
        photos = await db.get_photo_file_ids(user_id)
        if len(photos) < 2:
            return "NOT_ENOUGH_PHOTOS: User needs at least 2 days of photos for a transformation."
        return "MEDIA:transformation"

    elif tool_name == "get_timelapse":
        photos = await db.get_photo_file_ids(user_id)
        if len(photos) < 2:
            return "NOT_ENOUGH_PHOTOS: User needs at least 2 days of photos for a timelapse."
        return "MEDIA:timelapse"

    return "Unknown tool."


# Per-user conversation history (in-memory, survives within a bot session)
_chat_history: dict[int, list[dict]] = {}
MAX_HISTORY = 10  # messages per user


def _get_history(user_id: int) -> list[dict]:
    return _chat_history.get(user_id, [])


def _add_to_history(user_id: int, role: str, content: str):
    if user_id not in _chat_history:
        _chat_history[user_id] = []
    _chat_history[user_id].append({"role": role, "content": content})
    # Keep only last MAX_HISTORY exchanges
    if len(_chat_history[user_id]) > MAX_HISTORY * 2:
        _chat_history[user_id] = _chat_history[user_id][-(MAX_HISTORY * 2):]


async def chat_with_luke(message: str, db, user_id: int) -> dict:
    """Have a conversation with Luke. Returns {"text": str, "cover_url": str|None, "media": str|None}.

    Maintains per-user conversation history so Luke remembers context.
    """
    if not ANTHROPIC_API_KEY:
        return {"text": "AI not configured.", "cover_url": None}

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        # Build messages with history for context
        history = _get_history(user_id)
        messages = history + [{"role": "user", "content": message}]

        start = time.monotonic()

        # First call — Claude may want to use tools
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=LUKE_CHAT_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )

        # Process tool calls (may need multiple rounds)
        cover_url = None
        media = None
        context_data = {"refresh_card": False, "refresh_days": set()}
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await _execute_tool(block.name, block.input, db, user_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                    # Capture cover URL if a book was set
                    if block.name == "set_book" and "Cover URL:" in result:
                        url = result.split("Cover URL: ")[1]
                        if url != "none found":
                            cover_url = url
                    # Capture media requests
                    if result.startswith("MEDIA:"):
                        media = result.split("MEDIA:")[1]
                    # Capture card refresh signals
                    if result.startswith("REFRESH_CARD:"):
                        context_data["refresh_card"] = True
                        # Extract day from backfill results like "...for day 5..."
                        if block.name == "backfill_task" and "for day " in result:
                            try:
                                backfill_day = int(result.split("for day ")[1].split(",")[0].split(" ")[0])
                                context_data["refresh_days"].add(backfill_day)
                            except (ValueError, IndexError):
                                pass

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system=LUKE_CHAT_SYSTEM,
                tools=TOOLS,
                messages=messages,
            )

        # Extract final text response
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        # Clean up
        text = text.replace("—", "-").replace("–", "-").strip('"').strip("'").strip()

        latency_ms = int((time.monotonic() - start) * 1000)
        await db.log_event(user_id, None, "ai_chat", f"msg_len={len(message)}", latency_ms=latency_ms)

        # Save to conversation history
        _add_to_history(user_id, "user", message)
        if text:
            _add_to_history(user_id, "assistant", text)

        return {
            "text": text,
            "cover_url": cover_url,
            "media": media,
            "refresh_card": context_data["refresh_card"],
            "refresh_days": context_data["refresh_days"],
        }

    except Exception as e:
        logger.error("Luke chat failed: %s", e)
        await db.log_event(user_id, None, "ai_chat", error=str(e))
        return {"text": "something went wrong. try a /command instead", "cover_url": None, "media": None, "refresh_card": False}
