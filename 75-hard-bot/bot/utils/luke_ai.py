"""Luke's AI personality — Claude-powered contextual messages."""

import anthropic
import logging
import time

from bot.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

LUKE_SYSTEM_PROMPT = """You are Luke, the accountability bot in a 75 Hard group chat with 5 friends. You write the morning briefing every day before the daily tracker card drops.

Your morning briefing has two parts:
1. A quick honest take on yesterday (who showed up, who didn't, any standout moments)
2. A line or two setting the tone for today

How you write:
- Like a real person texting in a group chat. Lowercase ok. Fragments ok.
- You can say shit, damn, hell when it fits. Don't force it.
- NEVER use em dashes, semicolons, or colons
- NEVER use words like "journey", "grind", "crushing it", "built different", "let's go", "let's get it", "let's get after it"
- No motivational speaker energy. No LinkedIn vibes. No newsletter format.
- Call out specific people when something happened. "kat finished first again" or "gaurav still hasn't logged water in 2 days"
- Be honest. If someone's slacking, say it without being mean. If everyone killed it, acknowledge it without being cringe.
- Vary your energy day to day. Some days are hype, some days are chill, some days are serious.

Format:
- 4-8 lines total
- First line: day number + a quick take
- Middle: yesterday's highlights (specific names, specific events)
- Last line: something about today
- Use line breaks between thoughts
- The daily tracker card drops right after your message, so don't describe how to use it

Examples:

day 7. one week and everyone's still here

yesterday 2/4 finished everything. kat was first done for the third time this week. yumna and gaurav both missed their photos

most people quit in week one. today's about proving that's not us

---

day 22

perfect day yesterday. all 5 finished. that hasn't happened since day 3

bryan's deep into atomic habits. dev started a new book. the reading corner's getting interesting

---

day 45. sixty percent done

rough one yesterday. only bryan and kat got everything in. dev missed two workouts. yumna forgot to log water but says she drank it

we've come too far to start slipping now
"""


def _clean_output(text: str) -> str:
    """Strip AI artifacts from generated text."""
    # Kill em dashes
    text = text.replace("—", "-").replace("–", "-")
    # Kill quotes around the whole message
    text = text.strip('"').strip("'")
    return text.strip()


async def generate_morning_message(
    day_number: int,
    active_count: int,
    total_count: int,
    yesterday_summary: dict | None = None,
) -> str | None:
    """Generate a context-aware morning message using Claude."""
    if not ANTHROPIC_API_KEY:
        return None

    context_parts = [f"Day {day_number} of 75. {active_count} of {total_count} still in."]

    if yesterday_summary:
        completed = yesterday_summary.get("completed", [])
        incomplete = yesterday_summary.get("incomplete", [])
        first = yesterday_summary.get("first_finisher")
        books = yesterday_summary.get("books", [])

        if completed:
            context_parts.append(f"Yesterday {len(completed)}/{active_count} finished: {', '.join(completed)}")
        if incomplete:
            behind = [f"{name} missed {', '.join(m)}" for name, m in incomplete]
            context_parts.append(f"Didn't finish: {'; '.join(behind)}")
        if first:
            context_parts.append(f"First done yesterday: {first}")
        if books:
            context_parts.append(f"Reading: {', '.join(f'{n} - {b}' for n, b in books)}")
    elif day_number == 1:
        context_parts.append("First day. Nobody has started yet.")

    if day_number == 69:
        return "nice"

    context = "\n".join(context_parts)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=120,
            system=LUKE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a SHORT morning greeting for today. 2-3 sentences max.\n"
                        "A recap image of yesterday will follow your message, so DO NOT list "
                        "who finished or didn't — just briefly nod to yesterday and welcome today.\n"
                        "Keep it tight and human.\n\n"
                        f"{context}"
                    ),
                }
            ],
        )
        return _clean_output(response.content[0].text)
    except Exception as e:
        logger.error("Failed to generate morning message: %s", e)
        return None


WEEKLY_REFLECTION_SYSTEM = """You are Luke, the accountability bot in a 75 Hard group chat. You write the weekly Sunday reflection after the weekly digest image.

Write 3-5 sentences reflecting on the group's week. You're honest, specific, and casual.

How you write:
- Like a real person texting in a group chat. Lowercase ok. Fragments ok.
- NEVER use em dashes, semicolons, or colons
- NEVER use words like "journey", "grind", "crushing it", "built different", "let's go", "let's get it", "let's get after it"
- No motivational speaker energy. No LinkedIn vibes.
- Call out specific people by name when something stands out
- Be honest about slumps and wins without being mean or cringe
- Mention what people are reading if that data is available

Format:
- 3-5 sentences
- No bullet points or lists
- Plain text, conversational
"""


async def generate_weekly_reflection(
    week_number: int,
    user_stats: list[dict],
    reading_log: list[dict],
) -> str | None:
    """Generate a weekly reflection using Claude.

    user_stats: list of {name, days_complete, total_days}
    reading_log: list of {name, books: [{title, days}]}
    """
    if not ANTHROPIC_API_KEY:
        return None

    context_parts = [f"Week {week_number} summary."]

    # Completion stats
    for u in user_stats:
        context_parts.append(f"{u['name']}: {u['days_complete']}/{u['total_days']} days completed")

    # Best and worst
    sorted_by_completion = sorted(user_stats, key=lambda u: u["days_complete"], reverse=True)
    if sorted_by_completion:
        best = sorted_by_completion[0]
        context_parts.append(f"Most consistent: {best['name']} ({best['days_complete']}/{best['total_days']})")
        worst = sorted_by_completion[-1]
        if worst["days_complete"] < best["days_complete"]:
            context_parts.append(f"Struggled most: {worst['name']} ({worst['days_complete']}/{worst['total_days']})")

    # Reading
    if reading_log:
        for entry in reading_log:
            books_str = ", ".join(f"{b['title']} ({b['days']} days)" for b in entry["books"])
            context_parts.append(f"{entry['name']} read: {books_str}")

    context = "\n".join(context_parts)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=200,
            system=WEEKLY_REFLECTION_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"Write a weekly reflection for the group.\n\n{context}",
                }
            ],
        )
        return _clean_output(response.content[0].text)
    except Exception as e:
        logger.error("Failed to generate weekly reflection: %s", e)
        return None


SPICY_MOMENT_SYSTEM = """You are Luke, the accountability bot for a 5-person 75 Hard challenge group chat.

Your job: scan today's data and find ONE spicy moment worth highlighting. Spicy = surprising, funny, badass, dramatic, or shows real character. Examples:

- someone did their workout at 5am
- someone has a 3-day streak of being last to finish
- someone crushed their protein goal by 100g
- two people both ran outside in bad weather
- someone backfilled at 11:58pm with seconds to spare
- someone went from missing yesterday's water to nailing all 6 today
- someone's been reading the same book for 14 days (slow reader callout)

DO NOT highlight:
- Routine completions ("X finished today!")
- Anything you'd see in a normal status update
- Fake-positive cheerleading

Output rules:
- ONE sentence. Maximum 25 words.
- Casual, lowercase, text style. No em dashes. No semicolons. No corporate language.
- Light swearing ok if it fits.
- If nothing actually spicy stands out, output exactly: NONE"""


async def generate_spicy_moment(
    day_number: int,
    today_checkins: list[dict],
    yesterday_checkins: list[dict] | None,
    food_log_summary: str = "",
) -> str | None:
    """Daily spicy-moment generator. Returns a single sentence or None."""
    if not ANTHROPIC_API_KEY:
        return None

    from bot.utils.progress import is_all_complete, get_missing_tasks

    def _format_day(day_num, ckins):
        if not ckins:
            return f"Day {day_num}: no data"
        lines = [f"Day {day_num}:"]
        for c in ckins:
            tasks_done = []
            if c.get("workout_1_done"):
                tasks_done.append(f"workout1={c.get('workout_1_location','?')} {c.get('workout_1_type','?')}")
            if c.get("workout_2_done"):
                tasks_done.append(f"workout2={c.get('workout_2_location','?')} {c.get('workout_2_type','?')}")
            tasks_done.append(f"water={c.get('water_cups',0)}/16")
            if c.get("diet_done"):
                tasks_done.append("diet=ok")
            if c.get("reading_done"):
                tasks_done.append(f"read='{c.get('book_title','?')}'")
            if c.get("photo_done"):
                tasks_done.append("photo=ok")
            done_at = c.get("completed_at") or "incomplete"
            lines.append(f"  {c.get('name','?')}: {', '.join(tasks_done)} | finished_at={done_at}")
        return "\n".join(lines)

    ctx_parts = [
        _format_day(day_number, today_checkins),
    ]
    if yesterday_checkins:
        ctx_parts.append(_format_day(day_number - 1, yesterday_checkins))
    if food_log_summary:
        ctx_parts.append(f"\nFood logs today:\n{food_log_summary}")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=80,
            system=SPICY_MOMENT_SYSTEM,
            messages=[{"role": "user", "content": "\n\n".join(ctx_parts)}],
        )
        text = _clean_output(response.content[0].text).strip()
        if text.upper() == "NONE" or len(text) < 5:
            return None
        return text
    except Exception as e:
        logger.error("Failed to generate spicy moment: %s", e)
        return None


async def generate_recap_caption(
    day_number: int,
    checkins: list[dict],
    challenge_days: int = 75,
) -> str | None:
    """Generate a brief AI caption for the evening recap image."""
    if not ANTHROPIC_API_KEY:
        return None

    from bot.utils.progress import is_all_complete

    completed = [c["name"] for c in checkins if is_all_complete(c)]
    total = len(checkins)

    context = f"Day {day_number}. {len(completed)}/{total} finished. Names: {', '.join(completed) if completed else 'nobody'}."

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=60,
            system="You're Luke, a bot in a group chat. Write ONE short sentence reacting to today's results. Text style, no em dashes, no motivational cliches.",
            messages=[{"role": "user", "content": context}],
        )
        return _clean_output(response.content[0].text)
    except Exception as e:
        logger.error("Failed to generate recap caption: %s", e)
        return None
