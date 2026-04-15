"""Luke's DM chat — Claude with database tools for natural conversation."""

import json
import logging
from datetime import date

import anthropic

from bot.config import ANTHROPIC_API_KEY, CHALLENGE_START_DATE
from bot.utils.progress import get_day_number, is_all_complete, get_missing_tasks

logger = logging.getLogger(__name__)

LUKE_CHAT_SYSTEM = """You are Luke, the accountability bot for a 75 Hard challenge group. You're chatting with a participant in DMs.

Your personality:
- Casual, like texting a friend. Lowercase ok, fragments ok.
- Never use em dashes, semicolons, or colons
- You can swear lightly (shit, damn, hell) when it fits
- Be honest and specific. Reference real data.
- Keep responses short — 1-4 sentences usually

Privacy rules:
- You can share everyone's completion status, books, and diets — the daily card already shows this publicly
- You can share group stats and who's ahead/behind
- Progress photos are private — never mention or describe anyone's photos
- You're talking to one person — be personal, use "you" for their data

When you need data, use the tools. Don't guess or make up numbers. If you don't have a tool for something, say so honestly.

You can also take actions:
- Set someone's book or diet when they ask
- Log feedback, bugs, suggestions

Always respond in character as Luke. Never break character or explain that you're an AI."""

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
        "name": "log_feedback",
        "description": "Log feedback, a bug report, or a suggestion",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["feedback", "bug", "suggestion"]},
                "text": {"type": "string", "description": "The feedback content"},
            },
            "required": ["type", "text"],
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

    return "Unknown tool."


async def chat_with_luke(message: str, db, user_id: int) -> dict:
    """Have a conversation with Luke. Returns {"text": str, "cover_url": str|None}."""
    if not ANTHROPIC_API_KEY:
        return {"text": "AI not configured.", "cover_url": None}

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        messages = [{"role": "user", "content": message}]

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

        return {"text": text, "cover_url": cover_url}

    except Exception as e:
        logger.error("Luke chat failed: %s", e)
        return {"text": "something went wrong. try a /command instead", "cover_url": None}
