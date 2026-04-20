"""Luke's DM chat — Claude with database tools for natural conversation."""

import json
import logging
import time
from datetime import date, datetime, timedelta

import anthropic

from bot.config import ANTHROPIC_API_KEY, CHALLENGE_START_DATE, ORGANIZER
from bot.utils.progress import today_et, get_day_number, get_current_challenge_day, is_all_complete, get_missing_tasks

logger = logging.getLogger(__name__)

LUKE_CHAT_SYSTEM = f"""You are Luke, the accountability bot for a 5-person 75 Hard challenge. You're chatting with a participant in DMs.

<critical_rules>
READ THESE FIRST. They override everything else below when they conflict.

1. ACT IN THIS TURN. If a tool call is appropriate, call it NOW, in this same response. Never output "let me log X", "logging now:", "hold on", "give me a sec" without also calling the tool in the same turn. Lead-in narration without execution is a bug — the user sees words, nothing happens. If you're going to log 3 foods, call log_food 3 times in this turn. If you're answering a question, just answer. Never narrate intent and then stop.

2. KEEP TEXT SHORT. Your visible text to the user must fit in under 150 tokens. Tool calls are separate — they don't count. If the user asks you to log 5 foods, reply with one short sentence ("logged — you're at Xg now") after making all 5 log_food calls, not a paragraph per food.

3. GROUND TRUTH IS THE DB. Before claiming anything about logged state ("you already did X", "your total is Y", "yesterday you had Z"), call the appropriate tool FIRST. Your chat memory is short and wrong. The DB via tools is the only source of truth.
</critical_rules>

THE CHALLENGE RULES (you enforce these):
1. Two workouts per day, one indoor one outdoor. Duration is being finalized by the group.
2. Drink a gallon of water (16 cups / 128 oz) every day.
3. Follow your chosen diet every day. No alcohol. No cheat meals. Once you commit to a diet, you stick with it. If someone asks to change their diet mid-challenge, push back. Ask them why. Only allow it if there's a real reason (medical, allergy discovered, etc), not because they're tired of it.
4. Read 10 pages of non-fiction every day.
5. Take a progress photo every day.

Miss any single task on any single day = elimination. No exceptions unless {ORGANIZER} (the organizer) grants grace for bot issues.

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

ESCALATION - flag these to {ORGANIZER} by logging as feedback type "escalation":
- Someone hasn't checked in for 2+ days and hasn't said anything
- Someone asks to change their diet for a weak reason
- Someone seems like they're gaming the system (logging tasks they didn't do)
- Technical issues that affect multiple people
- Someone is upset or wants to quit. Be supportive first, but log it.

WHAT YOU CAN DO:
- Answer questions about the rules, the challenge, anyone's status
- Set/correct books (search-confirm-save flow — see BOOKS below)
- Set diets (push back on changes after the challenge starts)
- Set the user's timezone (via set_user_timezone) when they mention where they live or what TZ they're in. Map free-form answers ("east coast", "I'm in Chicago", "Mountain", "PT") to one of: US/Eastern, US/Central, US/Mountain, US/Pacific. This controls when their 10pm same-day reminder DM fires.
- Log feedback, bugs, suggestions
- Generate transformation photos and timelapses
- Give honest assessments of how someone or the group is doing
- Backfill yesterday's tasks before noon PT (use backfill_task for workout/water/reading/diet). Day 1 has a grace window — Day 1 backfills are allowed any time, no cutoff. From Day 2 onwards, the noon PT cutoff applies.
- Backfill yesterday's PHOTO with request_backfill_photo. Use when the user says they forgot to send their progress photo for a previous day. The tool will prompt them to send the photo; the next photo they DM will be saved to that day. Same Day 1 grace applies.

WHICH DAY IS THIS FOR (CRITICAL — read this carefully):
Before logging anything, you must know whether the user means today or a previous day. The tools assume today unless you explicitly use a backfill tool.
- If the user says "today", "just now", "did X today" → log for today (log_workout_dm, log_water_dm, etc.)
- If the user says "yesterday" or a specific past day → use backfill_task (or request_backfill_photo for photos)
- If the user says "I did X" or "completed X" with NO time reference AND it's evening/night → ASSUME TODAY but confirm in your reply ("got it, logged for today") so they can correct if wrong
- If the user says "I did X" or "completed X" with NO time reference AND it's early morning → ASK: "today or yesterday?" before calling any tool
- When in doubt: ASK. It's much better to ask one clarifying question than to put a workout on the wrong day and confuse the tally later.

DO IT, DON'T NARRATE (CRITICAL):
If you decide to take an action, take it IN THIS TURN by calling the tool. Do not output text like "let me log those", "hold up let me X first", "give me a sec" without ALSO calling the tool in the same response. Saying you'll do something and then stopping leaves the user hanging — they get the narration but the action never happens. Either call the tool, or just answer the question. Never both narrate intent AND fail to act.

GROUND TRUTH, NOT MEMORY (CRITICAL):
Your chat memory is SHORT and can be WRONG. The database is the only source of truth.
- Before you claim ANYTHING about a user's logged state ("you already did X", "your indoor workout is Y", "yesterday you had both workouts"), you MUST call the appropriate tool FIRST.
- Questions about TODAY → call get_my_status
- Questions about YESTERDAY or any past day → call get_my_status_for_day with the day_number
- NEVER answer "what did I log for day N" from chat memory alone. You will get it wrong because your memory doesn't reliably persist across sessions/days.
- If a user says "you logged that wrong, it was yesterday not today" → call get_my_status_for_day for both the past day and today to see the actual state, then fix with undo_workout + backfill_task as needed.
- When making a correction that spans multiple days, after you're done call get_my_status_for_day for each affected day and read the state back to the user so they can verify.

BOOKS (search-confirm-save):
- ALWAYS call search_books FIRST. Never call set_book without searching first.
- search_books returns up to 3 candidates with title, author, cover_url.
- Show the user the TOP candidate ("looks like Savor by Thich Nhat Hanh — that's it?"). If the top match is clearly correct (rare typos, exact author match), still confirm before saving.
- If the user says "no" or you see multiple plausible matches, list 2-3 options and ask which one. If none match, ask for the author and search again.
- WHEN SEARCH RETURNS NOTHING (or nothing matches even after retrying with the author): the book may not be on Apple Books (this happens for self-published or niche titles like Andy Frisella's "75 Hard"). Don't keep saying "I can't find it." Instead, OFFER to save the book without a cover image: "I couldn't find that one in our catalog — want me to save it as '<title>' anyway, just without a thumbnail? you can always swap the cover later." If they confirm, call set_book with the user's exact title and cover_url="".
- Only call set_book AFTER the user confirms.
- Pass the EXACT title and cover_url from the chosen candidate to set_book (or empty string if no cover).
- Pick intent carefully:
    intent="new" — user has no current book yet
    intent="correct" — user is fixing a typo on their CURRENT book (does NOT mark previous as finished)
    intent="finish_and_start" — user finished their previous book and is starting a new one
- If get_my_profile shows the user already has a current_book and they're trying to set another, ASK them: "did you finish [current_book] or just want to fix the title?"

WHAT YOU CAN'T DO:
- Eliminate or redeem people (they use /fail and /redeem)
- Change the rules (only {ORGANIZER} can)
- Access or share other people's progress photos

IMAGES:
- You CAN see images the user sends you. Look carefully and describe what you see.
- For food photos, READ THE INTENT before you act:
  * Question only ("how much protein is in this", "what's in this") → ANSWER with a breakdown. Do NOT call log_food. End by asking "want me to log it?"
  * Clear log intent ("logging this", "just ate this", "log this for me") → call log_food with your estimate.
  * Ambiguous → assume question, show the breakdown, ask if they want to log.
- When you estimate from a photo, ALWAYS show your work item-by-item. Example:
    "looks like ~6oz grilled chicken (~40g), 1 cup brown rice (~5g), 1 cup broccoli (~3g) = ~48g protein total. estimate, not exact."
  Use plus-or-minus language. Never present an estimate as a precise measurement.
- If it's a workout/screen/random image: describe or answer their question. Don't log anything.
- If they want to save it as their progress photo: tell them to tap the 📸 button on today's group card (or use request_backfill_photo if it's for yesterday). The image they DM with no opt-in does NOT auto-save as a progress photo.

When you need data, use the tools. Don't guess or make up numbers (except when explicitly estimating from a photo, with appropriate caveats). If you don't have a tool for something, say so honestly."""

TOOLS = [
    {
        "name": "get_my_status",
        "description": "Get the current user's checkin status for today",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_my_status_for_day",
        "description": "Get the user's checkin status for a SPECIFIC day_number (e.g., yesterday, Day 1). Use this whenever the user asks about a past day ('what did I log yesterday', 'what was my Day 1 workout'). NEVER answer questions about past days from your chat memory — always call this tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "The day to look up (1-indexed)"},
            },
            "required": ["day_number"],
        },
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
        "name": "search_books",
        "description": "Search for book candidates by title (and optionally author). Returns top 3 matches with title, author, and cover URL — does NOT save anything. ALWAYS call this BEFORE set_book so you can verify you have the right book. Especially important for short or common titles like 'Savor' or 'Atomic'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms — include author name when you know it for better matches"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_book",
        "description": "Save the user's book to the DB. Call this only AFTER search_books and after the user confirms the chosen candidate. Pass the EXACT title and cover_url from the chosen search_books candidate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Book title — use the title from the chosen search_books candidate"},
                "cover_url": {"type": "string", "description": "Cover URL from the chosen search_books candidate (empty string if none found)"},
                "intent": {
                    "type": "string",
                    "enum": ["new", "correct", "finish_and_start"],
                    "description": "new = first book or no current book exists; correct = fixing a typo on current book (does NOT mark old as finished); finish_and_start = user finished previous book and is starting this one",
                },
            },
            "required": ["title", "intent"],
        },
    },
    {
        "name": "set_user_timezone",
        "description": "Set the user's timezone so the 10pm same-day reminder DM fires at the right local time. Use when the user mentions where they live or what timezone they're in (e.g. 'I'm in NYC', 'I'm Pacific', 'central time', 'denver', 'PT', etc). Map their answer to one of: US/Eastern, US/Central, US/Mountain, US/Pacific. After setting, briefly confirm you got it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "enum": ["US/Eastern", "US/Central", "US/Mountain", "US/Pacific"],
                    "description": "IANA timezone — exactly one of US/Eastern, US/Central, US/Mountain, US/Pacific",
                },
            },
            "required": ["timezone"],
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
        "name": "get_diet_log_for_day",
        "description": "Get the user's diet log entries for a SPECIFIC past day_number with running totals. Use when user asks 'what did I eat yesterday', 'show me my food on day 2', etc. For today, use get_diet_progress instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "The day to look up (1-indexed)"},
            },
            "required": ["day_number"],
        },
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
        "description": "Log a non-photo task for YESTERDAY that the user forgot. Only works before noon PT (with a one-time grace window for Day 1). Use when user says they forgot to log something from yesterday.",
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
        "name": "request_backfill_photo",
        "description": "Use when the user says they forgot to send a progress photo for a previous day and want to upload it now. This sets up a one-shot photo intake for the specified day; the next photo the user DMs will be saved to that day. Only allow yesterday (with Day 1 grace).",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "The day this photo should be saved to (typically yesterday)"},
            },
            "required": ["day_number"],
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
        "description": "Flag a concern to the organizer. Use when: someone hasn't checked in for 2+ days, someone wants to change diet for a weak reason, suspected gaming, someone is upset/wants to quit, or technical issues affecting multiple people.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why this needs the organizer's attention"},
                "user_name": {"type": "string", "description": "Who it's about (if applicable)"},
            },
            "required": ["reason"],
        },
    },
]


async def _execute_tool(tool_name: str, tool_input: dict, db, user_id: int, context=None) -> str:
    """Execute a tool call and return the result as a string.

    `context` is the python-telegram-bot ContextTypes.DEFAULT_TYPE, optional. When
    present, tool handlers can fire group-chat easter eggs (squad complete, streaks,
    etc) after a DB write. Optional so unit tests / scripts can call without the bot.
    """
    day = await get_current_challenge_day(db)

    async def _maybe_fire_completion_eggs(this_user_id: int, this_day: int):
        """Fire shared easter eggs if context is available. Defensive — never raises."""
        if context is None:
            return
        try:
            from bot.utils.easter_eggs import fire_completion_easter_eggs
            user_row = await db.get_user(this_user_id)
            name = user_row["name"] if user_row else "someone"
            await fire_completion_easter_eggs(context, db, this_user_id, name, this_day)
        except Exception as e:
            logger.warning("Failed to fire completion easter eggs from DM tool: %s", e)

    async def _maybe_record_workout(this_user_id: int, this_day: int):
        """Track workout time for the simultaneous-workout easter egg."""
        if context is None:
            return
        try:
            from bot.utils.easter_eggs import record_workout_time, check_simultaneous_workout
            record_workout_time(this_user_id, this_day)
            user_row = await db.get_user(this_user_id)
            name = user_row["name"] if user_row else "someone"
            await check_simultaneous_workout(context, this_user_id, name, this_day)
        except Exception as e:
            logger.warning("Failed to fire workout easter eggs from DM tool: %s", e)

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

    elif tool_name == "get_my_status_for_day":
        target_day = int(tool_input["day_number"])
        if target_day < 1 or target_day > day:
            return f"Invalid day {target_day}. Today is day {day}."
        checkin = await db.get_checkin(user_id, target_day)
        if not checkin:
            return f"No checkin row exists for day {target_day}."
        c = dict(checkin)
        parts = [f"Day {target_day}:"]
        if c.get("workout_1_done"):
            parts.append(f"workout 1 = {c.get('workout_1_location', '?')} {c.get('workout_1_type', '?')}")
        else:
            parts.append("workout 1 NOT done")
        if c.get("workout_2_done"):
            parts.append(f"workout 2 = {c.get('workout_2_location', '?')} {c.get('workout_2_type', '?')}")
        else:
            parts.append("workout 2 NOT done")
        parts.append(f"water {c.get('water_cups', 0)}/16")
        parts.append(f"diet {'done' if c.get('diet_done') else 'NOT done'}")
        if c.get("reading_done"):
            parts.append(f"read '{c.get('book_title', '?')}'")
        else:
            parts.append("reading NOT done")
        parts.append(f"photo {'done' if c.get('photo_done') else 'NOT done'}")
        parts.append(f"overall: {'COMPLETE' if is_all_complete(c) else 'incomplete'}")
        return ". ".join(parts)

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

    elif tool_name == "search_books":
        from bot.utils.books import search_books as _search
        import json as _json
        candidates = await _search(tool_input["query"], limit=3)
        if not candidates:
            return _json.dumps({"results": [], "note": "No candidates found. Ask user for more detail (author, year)."})
        return _json.dumps({"results": candidates})

    elif tool_name == "set_book":
        title = tool_input["title"]
        cover_url = tool_input.get("cover_url") or ""
        intent = tool_input.get("intent", "new")

        # Fall back to a fresh search if no cover_url was passed (legacy callers)
        if not cover_url:
            from bot.utils.books import fetch_book_cover
            cover_url = await fetch_book_cover(title) or ""

        user = await db.get_user(user_id)
        has_current = bool(user and dict(user).get("current_book"))

        if intent == "correct":
            if not has_current:
                return "DENIED: no current book to correct. Use intent='new' instead."
            ok = await db.correct_current_book(user_id, title, cover_url or None)
            if not ok:
                return "Could not update current book."
            return f'Book corrected to "{title}". Cover URL: {cover_url or "none found"}'

        if intent == "finish_and_start":
            if has_current:
                await db.finish_book(user_id, finished_day=day)
            await db.set_current_book(user_id, title, started_day=day, cover_url=cover_url or None)
            return f'Previous book finished. New book set to "{title}". Cover URL: {cover_url or "none found"}'

        # intent == "new"
        if has_current:
            return "DENIED: you already have a current book. Ask user whether they finished it (intent='finish_and_start') or this is a typo correction (intent='correct')."
        await db.set_current_book(user_id, title, started_day=day, cover_url=cover_url or None)
        return f'Book set to "{title}". Cover URL: {cover_url or "none found"}'

    elif tool_name == "set_user_timezone":
        tz = tool_input.get("timezone", "")
        if tz not in ("US/Eastern", "US/Central", "US/Mountain", "US/Pacific"):
            return f"DENIED: invalid timezone '{tz}'. Must be one of US/Eastern, US/Central, US/Mountain, US/Pacific."
        await db.set_user_timezone(user_id, tz)
        # Pretty short label for confirmation messages
        labels = {"US/Eastern": "ET", "US/Central": "CT", "US/Mountain": "MT", "US/Pacific": "PT"}
        return f"Timezone set to {tz} ({labels[tz]}). The 10pm same-day reminder will fire at 10pm {labels[tz]} from now on."

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

    elif tool_name == "get_diet_log_for_day":
        target_day = int(tool_input["day_number"])
        if target_day < 1 or target_day > day:
            return f"Invalid day {target_day}. Today is day {day}."
        entries = await db.get_diet_entries(user_id, target_day)
        if not entries:
            return f"No food logged for day {target_day}."
        lines = [f"Day {target_day} entries ({len(entries)}):"]
        for e in entries:
            val = f" ({e['extracted_value']:.0f} {e['extracted_unit']})" if e.get("extracted_value") else ""
            lines.append(f"  - {e['entry_text']}{val}")
        units = {}
        for e in entries:
            if e.get("extracted_value") and e.get("extracted_unit"):
                u = e["extracted_unit"]
                units[u] = units.get(u, 0) + e["extracted_value"]
        if units:
            lines.append("Totals:")
            for u, total in units.items():
                lines.append(f"  {total:.0f} {u}")
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
        await _maybe_record_workout(user_id, day)
        if just_completed:
            await _maybe_fire_completion_eggs(user_id, day)
        return f"REFRESH_CARD: Workout logged — {location} {wtype}, slot {slot}/2. {'Both done!' if slot == 2 else ''}"

    elif tool_name == "log_water_dm":
        cups = tool_input.get("cups", 1)
        mode = tool_input.get("mode", "add")
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            await db.create_checkin(user_id, day, today_et().isoformat())
        just_completed_any = False
        if mode == "set":
            jc = await db.set_water(user_id, day, cups)
            just_completed_any = just_completed_any or jc
            new_count = cups
        else:
            for _ in range(cups):
                new_count, jc = await db.increment_water(user_id, day)
                just_completed_any = just_completed_any or jc
        checkin = await db.get_checkin(user_id, day)
        new_count = checkin["water_cups"] if checkin else 0
        if just_completed_any:
            await _maybe_fire_completion_eggs(user_id, day)
        return f"REFRESH_CARD: Water updated — {new_count}/16 cups"

    elif tool_name == "confirm_diet_dm":
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            await db.create_checkin(user_id, day, today_et().isoformat())
            checkin = await db.get_checkin(user_id, day)
        just_completed = False
        if not checkin["diet_done"]:
            _, just_completed = await db.toggle_diet(user_id, day)
        if just_completed:
            await _maybe_fire_completion_eggs(user_id, day)
        return "REFRESH_CARD: Diet confirmed for today"

    elif tool_name == "fix_water":
        cups = max(0, min(16, tool_input.get("cups", 0)))
        just_completed = await db.set_water(user_id, day, cups)
        if just_completed:
            await _maybe_fire_completion_eggs(user_id, day)
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
        _PT = _pytz.timezone("US/Pacific")
        now_pt = datetime.now(_PT)

        yesterday = day - 1
        if yesterday < 1:
            return "DENIED: There's no yesterday to backfill. The challenge just started."

        # Day 1 grace: first day is for figuring out the system, no noon PT cutoff.
        # From Day 2 onward, normal 12pm PT lock applies.
        is_day_1_grace = yesterday == 1
        if not is_day_1_grace and now_pt.hour >= 12:
            return "DENIED: It's past 12pm PT / 3pm ET. Yesterday's tasks are locked in. Backfill window closed."

        # Ensure checkin exists for yesterday
        checkin = await db.get_checkin(user_id, yesterday)
        if not checkin:
            yesterday_date = (now_pt.date() - timedelta(days=1)).isoformat()
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

    elif tool_name == "request_backfill_photo":
        import pytz as _pytz
        _PT = _pytz.timezone("US/Pacific")
        now_pt = datetime.now(_PT)

        target_day = int(tool_input.get("day_number", day - 1))
        yesterday = day - 1

        if target_day < 1:
            return "DENIED: There's no day before Day 1 to backfill."
        if target_day > day:
            return "DENIED: That day hasn't happened yet."
        if target_day < yesterday:
            return f"DENIED: Day {target_day} is too far back. Photos can only be backfilled for yesterday."

        # Day 1 grace: skip noon PT cutoff for Day 1 specifically.
        is_day_1_grace = target_day == 1
        if not is_day_1_grace and target_day == yesterday and now_pt.hour >= 12:
            return "DENIED: It's past 12pm PT / 3pm ET. Yesterday's photo window closed."

        # Ensure the checkin row exists so log_photo will succeed.
        checkin = await db.get_checkin(user_id, target_day)
        if not checkin:
            from datetime import timedelta as _td
            checkin_date = (now_pt.date() - _td(days=(day - target_day))).isoformat()
            await db.create_checkin(user_id, target_day, checkin_date)

        # Signal the wrapper to set photo_day so the next photo lands on target_day.
        return f"BACKFILL_PHOTO:{target_day}"

    elif tool_name == "escalate_to_admin":
        reason = tool_input.get("reason", "No reason given")
        user_name = tool_input.get("user_name", "unknown")
        await db.add_feedback(user_id, "escalation", f"[{user_name}] {reason}", f"day {day}")
        # Also try to DM the admin directly
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
        return f"Flagged to {ORGANIZER}: {reason}"

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


# Per-user conversation history (in-memory, lazy-loaded from conversation_log on first access)
_chat_history: dict[int, list[dict]] = {}
_history_loaded: set[int] = set()  # users we've already lazy-loaded from DB
MAX_HISTORY = 10  # messages per user


async def _ensure_history_loaded(user_id: int, db) -> None:
    """Hydrate _chat_history[user_id] from conversation_log on first access.

    Lets chat memory survive bot restarts. Idempotent — only loads once per process.
    Skips entries with image content since we don't store image bytes in the log.
    """
    if user_id in _history_loaded:
        return
    _history_loaded.add(user_id)
    try:
        rows = await db.get_recent_conversations(limit=MAX_HISTORY, telegram_id=user_id)
    except Exception:
        return
    # rows are newest-first; reverse to chronological
    msgs: list[dict] = []
    for r in reversed(list(rows)):
        u_msg = r["user_message"] or ""
        l_msg = r["luke_response"] or ""
        # Skip rows with image markers — we can't reconstruct the image
        if u_msg.startswith("[image]"):
            continue
        if u_msg:
            msgs.append({"role": "user", "content": u_msg})
        if l_msg and not l_msg.startswith("[ERROR]"):
            msgs.append({"role": "assistant", "content": l_msg})
    if msgs:
        _chat_history[user_id] = msgs[-(MAX_HISTORY * 2):]


def _get_history(user_id: int) -> list[dict]:
    return _chat_history.get(user_id, [])


def _add_to_history(user_id: int, role: str, content: str):
    if user_id not in _chat_history:
        _chat_history[user_id] = []
    _chat_history[user_id].append({"role": role, "content": content})
    # Keep only last MAX_HISTORY exchanges
    if len(_chat_history[user_id]) > MAX_HISTORY * 2:
        _chat_history[user_id] = _chat_history[user_id][-(MAX_HISTORY * 2):]


async def chat_with_luke(
    message: str,
    db,
    user_id: int,
    image_b64: str | None = None,
    image_media_type: str = "image/jpeg",
    context=None,
) -> dict:
    """Have a conversation with Luke. Returns {"text": str, "cover_url": str|None, "media": str|None}.

    Maintains per-user conversation history so Luke remembers context.
    If image_b64 is provided, Claude sees the image alongside the text.
    """
    if not ANTHROPIC_API_KEY:
        return {"text": "AI not configured.", "cover_url": None}

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        # Lazy-hydrate per-user chat memory from the DB on first message after restart
        await _ensure_history_loaded(user_id, db)

        # Build messages with history for context
        history = _get_history(user_id)
        if image_b64:
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_media_type,
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": message or "what's in this photo?"},
            ]
        else:
            user_content = message
        messages = history + [{"role": "user", "content": user_content}]

        start = time.monotonic()

        async def _call_claude(msgs, label: str = "first"):
            """Call Claude with truncation detection + auto-retry on max_tokens.

            Returns the response. Logs an ai_chat_truncated event if the model
            hit max_tokens (truncated mid-thought). Auto-retries once with
            doubled cap so the user doesn't see the truncation as silence.
            """
            CAPS = [1024, 2048]  # primary + retry
            last_resp = None
            for attempt, cap in enumerate(CAPS):
                last_resp = client.messages.create(
                    model="claude-opus-4-7",
                    max_tokens=cap,
                    system=LUKE_CHAT_SYSTEM,
                    tools=TOOLS,
                    messages=msgs,
                )
                if last_resp.stop_reason != "max_tokens":
                    return last_resp
                # Truncated. Log loudly and retry (or give up after last attempt).
                logger.warning(
                    "AI_CHAT_TRUNCATED user_id=%d label=%s attempt=%d cap=%d",
                    user_id, label, attempt + 1, cap,
                )
                try:
                    await db.log_event(user_id, None, "ai_chat_truncated",
                                       f"label={label} cap={cap} attempt={attempt+1}")
                except Exception:
                    pass
            return last_resp  # truncated even after retry — caller handles

        # First call — Claude may want to use tools
        response = await _call_claude(messages, label="first")

        # Process tool calls (may need multiple rounds)
        cover_url = None
        media = None
        backfill_photo_day: int | None = None
        tools_called: list[str] = []
        context_data = {"refresh_card": False, "refresh_days": set()}
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tools_called.append(block.name)
                    result = await _execute_tool(block.name, block.input, db, user_id, context=context)
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
                    # Capture photo-backfill signal so the wrapper can set photo_day
                    if result.startswith("BACKFILL_PHOTO:"):
                        try:
                            backfill_photo_day = int(result.split("BACKFILL_PHOTO:")[1])
                        except (ValueError, IndexError):
                            pass
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

            response = await _call_claude(messages, label="tool_loop")

        # Extract final text response
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        # Clean up
        text = text.replace("—", "-").replace("–", "-").strip('"').strip("'").strip()

        # Empty-text fallback: if Claude finished without producing any visible text
        # (happens when model only emits tool_use or thinking blocks and we're past
        # the tool loop), surface a hiccup message instead of silence so the user
        # knows something went wrong and can retry.
        if not text:
            text = "hmm, my brain hiccuped. try sending that again?"
            logger.warning(
                "EMPTY_TEXT_FALLBACK user_id=%d stop_reason=%s tools_called=%s",
                user_id, getattr(response, "stop_reason", "?"), tools_called,
            )
            try:
                await db.log_event(user_id, None, "ai_chat_empty_text",
                                   f"stop={getattr(response,'stop_reason','?')} tools={tools_called}")
            except Exception:
                pass

        # Phantom-action detection: Luke said he was about to do something but
        # never called any tool. This is a defect — the user thinks an action
        # happened but it didn't. We can't recover the action server-side
        # (we don't know what Luke meant), so warn the user explicitly and log
        # the event so we can review patterns and fix prompts.
        ACTION_PHRASES = (
            "logging now", "let me log", "let me actually log", "logging it",
            "logging everything", "let me push", "logging all",
            "give me a sec", "hold up", "hold on, let me", "give me one sec",
        )
        text_lower = text.lower()
        if not tools_called and any(p in text_lower for p in ACTION_PHRASES):
            logger.warning(
                "AI_CHAT_PHANTOM_ACTION user_id=%d text=%r",
                user_id, text[:200],
            )
            try:
                await db.log_event(user_id, None, "ai_chat_phantom_action",
                                   f"text={text[:120]!r}")
            except Exception:
                pass
            # Append a clear correction so the user knows to retry
            text = (
                text.rstrip(".:!?") + " — actually wait, glitch on my end. nothing got "
                "logged. resend and I'll do it for real this time."
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        await db.log_event(user_id, None, "ai_chat", f"msg_len={len(message)}", latency_ms=latency_ms)

        # Save to conversation history
        _add_to_history(user_id, "user", message)
        if text:
            _add_to_history(user_id, "assistant", text)

        # Persist the exchange for later review and improvement
        try:
            user = await db.get_user(user_id)
            user_name = dict(user)["name"] if user else None
        except Exception:
            user_name = None
        await db.add_conversation_log(
            telegram_id=user_id,
            user_name=user_name,
            source="dm",
            user_message=("[image] " + message) if image_b64 else message,
            luke_response=text,
            tools_called=json.dumps(tools_called) if tools_called else None,
        )

        return {
            "text": text,
            "cover_url": cover_url,
            "media": media,
            "refresh_card": context_data["refresh_card"],
            "refresh_days": context_data["refresh_days"],
            "backfill_photo_day": backfill_photo_day,
        }

    except Exception as e:
        logger.error("Luke chat failed: %s", e)
        await db.log_event(user_id, None, "ai_chat", error=str(e))
        # Log the failed exchange too so we can spot patterns of breakage
        try:
            await db.add_conversation_log(
                telegram_id=user_id,
                user_name=None,
                source="dm",
                user_message=message,
                luke_response=f"[ERROR] {e}",
                tools_called=None,
            )
        except Exception:
            pass
        return {"text": "something went wrong. try a /command instead", "cover_url": None, "media": None, "refresh_card": False}
