"""Luke's DM chat — Claude with database tools for natural conversation."""

import json
import logging
import time
from datetime import date

import anthropic

from bot.config import ANTHROPIC_API_KEY, CHALLENGE_START_DATE
from bot.utils.progress import get_day_number, is_all_complete, get_missing_tasks

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
        "description": "Set or change the user's diet plan",
        "input_schema": {
            "type": "object",
            "properties": {"plan": {"type": "string", "description": "Diet plan description"}},
            "required": ["plan"],
        },
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
    day = max(get_day_number(CHALLENGE_START_DATE, date.today()), 1)

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
        await db.set_diet_plan(user_id, tool_input["plan"])
        return f'Diet updated to "{tool_input["plan"]}"'

    elif tool_name == "log_feedback":
        await db.add_feedback(user_id, tool_input["type"], tool_input["text"], f"day {day}")
        return f"Logged {tool_input['type']}: {tool_input['text']}"

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


async def chat_with_luke(message: str, db, user_id: int) -> dict:
    """Have a conversation with Luke. Returns {"text": str, "cover_url": str|None, "media": str|None}.

    media can be "transformation" or "timelapse" — caller handles generating and sending the actual media.
    """
    if not ANTHROPIC_API_KEY:
        return {"text": "AI not configured.", "cover_url": None}

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        messages = [{"role": "user", "content": message}]

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

        return {"text": text, "cover_url": cover_url, "media": media}

    except Exception as e:
        logger.error("Luke chat failed: %s", e)
        await db.log_event(user_id, None, "ai_chat", error=str(e))
        return {"text": "something went wrong. try a /command instead", "cover_url": None, "media": None}
