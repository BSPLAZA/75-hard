"""Luke's DM chat — Claude with database tools for natural conversation."""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta

import anthropic

from bot.config import ANTHROPIC_API_KEY, CHALLENGE_START_DATE, GROUP_CHAT_ID, ORGANIZER
from bot.utils.progress import today_et, get_day_number, get_current_challenge_day, is_all_complete, get_missing_tasks

logger = logging.getLogger(__name__)

LUKE_CHAT_SYSTEM = f"""You are Luke, the accountability bot for a 5-person 75 Hard challenge. You're chatting with a participant in DMs.

<critical_rules>
READ THESE FIRST. They override everything else below when they conflict.

1. ACT IN THIS TURN. If a tool call is appropriate, call it NOW, in this same response. Never output "let me log X", "logging now:", "hold on", "give me a sec" without also calling the tool in the same turn. Lead-in narration without execution is a bug — the user sees words, nothing happens. If you're going to log 3 foods, call log_food 3 times in this turn. If you're answering a question, just answer. Never narrate intent and then stop.

2. KEEP TEXT SHORT. Your visible text to the user must fit in under 150 tokens. Tool calls are separate — they don't count. If the user asks you to log 5 foods, reply with one short sentence ("logged — you're at Xg now") after making all 5 log_food calls, not a paragraph per food.

3. GROUND TRUTH IS THE DB. Before claiming anything about logged state ("you already did X", "your total is Y", "yesterday you had Z"), call the appropriate tool FIRST. Your chat memory is short and wrong. The DB via tools is the only source of truth.

4. NEVER DEFLECT TO BUTTONS. Never say "tap the 📸/📖/💧/🍽️/🏋️ button on the group card" or "I can't log X from DM" or "you'll need to use the button". You CAN log everything from DM via tools. Map intent to a tool and call it:
   - "I read 10 pages" / "did my reading" → log_reading_dm (not deflect)
   - "I drank water" / "+5 water" → log_water_dm
   - "did my workout" → log_workout_dm
   - "stayed on diet" → confirm_diet_dm
   - photo with caption or no caption → request_backfill_photo with day_number=current_day, the photo handler auto-saves it
   The ONLY thing that requires the group card buttons is nothing. There is no tool for the daily card photo button that you don't have via a DM tool.

5. NUMBERS COME FROM TOOLS, NOT MEMORY. Never include a numerical state claim ("Xg, Y to go", "you're at N", "goal hit", "X/16 cups", "at Yg today") that wasn't returned by a tool call IN THIS TURN. Your chat memory of running totals is unreliable — you may have hallucinated previous totals, and the user's history can mislead you. If you want to mention a total, count, or running tally, you MUST call get_diet_progress, get_my_status, log_food, log_water_dm, etc. in this same turn AND quote numbers only from that tool's return value. If you didn't call a tool that returned the number, do not output the number.

6. DAY RESOLUTION COMES FROM SESSION CONTEXT, NOT MEMORY. The system prompt below includes a SESSION CONTEXT block telling you today's day number and yesterday's day number. When the user says "yesterday" or "today", resolve via that anchor — NOT via what you remember from earlier in chat. If chat memory disagrees with SESSION CONTEXT, SESSION CONTEXT wins.

7. PICK THE RIGHT TOOL FOR THE INTENT. Map the user's content to the tool that handles that *kind* of thing — not the one that's superficially similar. Real production failure: a user said "I ate X today" and Luke called set_book, treating the food note as a book log. Disambiguation rules:
   - "I ate X" / "had X for lunch" / "drank a protein shake" / "snacked on Y" → log_food (NOT set_book, NOT log_reading_dm)
   - "I read X today" / "finished today's pages" / "got my reading in" → log_reading_dm (NOT set_book — set_book is for declaring/changing the book itself)
   - "I'm reading X" / "starting X" / "switching books to Y" / "finished X, starting Y" → set_book (declares the book; does NOT count today's reading)
   - "I drank water / X cups" → log_water_dm (NOT log_food, even if they say "I drank X")
   - "I worked out / ran / lifted" → log_workout_dm (NOT log_food)
   If a single message mixes domains ("I ran 5 miles and ate chicken"), call BOTH tools in this turn — log_workout_dm AND log_food. Never collapse a multi-domain message into one tool to "tidy up."
   When the user message is genuinely ambiguous (one short fragment, no clear domain), ASK before calling any tool. A clarifying question is cheaper than a wrong tool call you have to undo later.

8. PENANCE DISAMBIGUATION. The 9am ET morning DM lists yesterday's UNMARKED tasks and asks the user, per task, whether they did it (and forgot to log) or missed it (and need penance). The user's reply may be ambiguous. Rules:
   - NEVER call declare_penance without a specific task. If user says "I need penance" / "all penance" / "penance for everything" without naming the task, ASK FIRST: "for which? [list the unmarked tasks]". Then call declare_penance once per named task, in this turn.
   - If user says "did them" / "all done" / "I did it" without naming the task, call backfill_task for EACH unmarked task in this turn.
   - If user names some tasks specifically and is silent on others, act on the named ones and ASK about the rest in the same turn ("got it for water — and the workout?").
   - NEVER claim "your penance is set" / "marked as penance" without a successful declare_penance tool call in this turn. State claims about penance state must be backed by a tool call (this generalizes critical_rule 5).
   - declare_penance only accepts penance-able tasks (workout_indoor, workout_outdoor, water, reading, photo). For diet violations (cheat / alcohol), the user's path is log_violation (different tool — diet is binary, no penance possible). If user says "I had wine" or "I cheated on diet" call log_violation (NOT declare_penance, NOT undo_diet alone). log_violation requires a `detail` arg — the freeform context for the squad to judge ("one glass of wine", "pizza slice"). If the user is vague ("I cheated"), ASK what specifically happened before calling. The case posts a poll to the group and the organizer renders verdict (pass / penance / fail).
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
- Backfill yesterday's tasks before midnight PT tonight (use backfill_task for workout/water/reading/diet). Day 1 has a grace window — Day 1 backfills are allowed any time, no cutoff. From Day 2 onwards, the window closes at midnight PT of the day after the missed day.
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

WHEN A USER FINISHES A BOOK:
- Trust their word. If they say "I finished X", they finished X. Don't ask for confirmation/proof.
- BUT you cannot call set_book(intent="finish_and_start") without a NEW book title — the schema requires both. So push for the next title in the SAME turn:
    "nice. what are you starting next? need the title (and author if you have it) so I can lock it in."
- After they give you the new title, do the search_books → confirm → set_book(intent="finish_and_start") flow as normal.
- Do NOT just acknowledge the finish and end the turn. The finish is only real once set_book(finish_and_start) succeeds and the announcement fires to the group.
- If they truly don't have a next book picked: tell them the finish only registers when they pick the next one, and to come back when they decide.

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
        "description": "Declare or change the user's CURRENT BOOK in their profile. Call only when the user is naming a book they're reading or starting (e.g. 'I'm reading Atomic Habits', 'switching to Savor', 'finished X, starting Y'). Do NOT call this when the user is logging today's reading SESSION (use log_reading_dm) or talking about food (use log_food). Always call search_books first and only set_book after the user confirms the candidate.",
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
        "description": "Log a food/meal/snack that the user ate. Use whenever the user mentions EATING (food, meal, snack, protein shake) or DRINKING something other than water (coffee, juice, etc). Trigger phrases include 'I ate', 'had for lunch', 'snacked on', 'just ate', 'drank a shake'. Do NOT call this for reading sessions, books, workouts, or water — those have their own tools (log_reading_dm, set_book, log_workout_dm, log_water_dm). Extract the relevant metric based on their diet plan (protein grams, calories, etc). If their diet is 'clean eating', just note whether the food is clean or not.",
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
        "name": "log_reading_dm",
        "description": "Log today's reading task in one shot. Use when user says they read their pages today (e.g. 'I read 10 pages', 'finished today's reading', 'just read'). Updates daily_checkins.reading_done=1 and stores book + takeaway. Falls back to user's current_book if no book_title given. Don't tell user to tap any button — just call this.",
        "input_schema": {
            "type": "object",
            "properties": {
                "book_title": {"type": "string", "description": "Book title. Optional — if user doesn't say, leave empty and we'll use their current_book."},
                "takeaway": {"type": "string", "description": "Optional one-line takeaway/quote/note from today's reading. Leave empty if user didn't share."},
            },
        },
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
        "name": "declare_self_fail",
        "description": (
            "User explicitly says they failed and are out — they're choosing to be eliminated. "
            "DM template surfaces Venmo + Zelle for the residual buy-in payment to the prize pool. "
            "Does NOT eliminate the user immediately — admin /admin_settle_failure confirms payment "
            "received, then full elimination + group announcement fires. "
            "Triggers: 'I failed yesterday I'm out', 'I'm done, eliminate me', 'I want to fail'. "
            "DO NOT call this for soft cases (one missed task that could be penance'd) — that's "
            "declare_penance. Only call when user is unambiguously declaring final exit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional one-line reason from the user (saved for the audit trail).",
                },
            },
        },
    },
    {
        "name": "get_my_compliance_grid",
        "description": (
            "Render the user's compliance grid — all 75 days × 6 tasks colored by state "
            "(complete / unmarked / in-penance / recovered / failed / arbitration / future). "
            "Use when the user asks to see their progress overview, history, where they missed days, "
            "or 'show me my grid'. Returns a PNG image of the full challenge."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_timelapse",
        "description": "Generate an animated video timelapse of all the user's progress photos. Use when user asks for a timelapse, slideshow, animation of their photos, etc.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "backfill_task",
        "description": "Log a non-photo task for a previous day that the user forgot. Defaults to YESTERDAY but accepts an explicit day_number for any past day in the user's challenge window. The backfill window for a missed day closes at midnight PT of the following day (Day 1 grace exception — always allowed). Use when user says they forgot to log something from a past day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["workout_outdoor", "workout_indoor", "water", "reading", "diet"],
                    "description": "Which task to backfill",
                },
                "day_number": {
                    "type": "integer",
                    "description": "Optional. The day to backfill (1-indexed global day). Defaults to yesterday. Use this when user says 'fill in Day 4' or names a specific day.",
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
        "name": "declare_penance",
        "description": (
            "User missed a penance-able task on a past day and is committing to do 2x today as makeup. "
            "Creates a penance_log row with status='in_progress' and makeup_day=today. "
            "Penance-able tasks: workout_indoor, workout_outdoor, water, reading, photo. "
            "DIET IS NOT PENANCE-ABLE — for diet violations (cheat / alcohol), use log_violation instead. "
            "CRITICAL: never call this without a specific task. If the user replies to a multi-task morning "
            "nudge with vague phrasing like 'all penance' or 'I need penance', ASK which task first. "
            "Per missed task, only ONE penance row should exist — don't double-declare. "
            "Common trigger: morning nudge → user says 'I missed my workout yesterday, doing 2x today'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["workout_indoor", "workout_outdoor", "water", "reading", "photo"],
                    "description": "Which task is being penance'd. MUST be specific — never call this with a placeholder.",
                },
                "missed_day": {
                    "type": "integer",
                    "description": "The day the task was missed. Defaults to yesterday. For older days, pass explicitly.",
                },
                "detail": {
                    "type": "string",
                    "description": "Optional freeform note (e.g., 'forgot to log water', 'never did indoor').",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "log_violation",
        "description": (
            "User confessed to a binary-task violation (currently: diet — alcohol or cheat meal). "
            "Diet can't be penance'd (you can't 2× a missed meal), so the case goes to GROUP ARBITRATION: "
            "Luke posts a poll in the group with options [pass, penance, fail], the squad votes, and "
            "the organizer renders the final verdict. Creates a penance_log row with status='arbitration_pending'. "
            "Triggers: 'I had wine', 'I cheated on diet', 'had a beer', 'had a cheat meal'. "
            "missed_day defaults to today (most violations are real-time confessions). Pass an explicit "
            "missed_day if user says 'yesterday' or names another day. Detail is the freeform context "
            "(e.g., 'one glass of wine at dinner', 'pizza slice'). DO NOT use this for missed-but-not-violated "
            "tasks — that's declare_penance for action tasks, or just acknowledge for diet (diet just stays unmarked)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["diet"],
                    "description": "Which binary task was violated. Currently only 'diet'.",
                },
                "missed_day": {
                    "type": "integer",
                    "description": "The day of the violation. Defaults to today. Pass explicitly if user names a past day.",
                },
                "detail": {
                    "type": "string",
                    "description": "Required. Freeform context for the squad to judge (e.g., 'glass of wine', 'pizza slice', 'birthday cake').",
                },
            },
            "required": ["task", "detail"],
        },
    },
    {
        "name": "request_backfill_photo",
        "description": "Set up a one-shot photo intake. The NEXT photo the user DMs will be saved as their progress photo for the specified day. Pass day_number=current_day for TODAY's progress photo (most common case — when user asks about saving today's photo or sends a photo with no caption). Pass day_number=yesterday for a missed previous day. Allowed days: today, yesterday (window closes at midnight PT tonight), Day 1 (always graced).",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Which day to save the photo as. Use TODAY for current-day progress photo, YESTERDAY for backfill."},
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


def _parse_diet_goal(diet_plan: str | None) -> tuple[float, str] | None:
    """Extract a numeric goal + unit from a free-form diet plan string.

    Returns (value, extracted_unit) or None if no numeric goal found.

    Recognizes patterns like:
      "170g protein" / "high protein 170g"   -> (170, "protein_g")
      "1800 calories" / "1800 cal/day"       -> (1800, "calories")
      "200g carbs"                           -> (200, "carbs_g")
      "60g fat"                              -> (60, "fat_g")
    Qualitative diets like "clean eating, no processed snacks" return None.
    """
    if not diet_plan:
        return None
    text = diet_plan.lower()
    # Order matters — match more specific first
    patterns = [
        (r"(\d{2,4})\s*g\s*(?:of\s+)?protein|\bprotein\b[^0-9]{0,30}(\d{2,4})\s*g", "protein_g"),
        (r"(\d{3,5})\s*(?:cal|calories|kcal)\b", "calories"),
        (r"(\d{2,4})\s*g\s*(?:of\s+)?carbs?|\bcarbs?\b[^0-9]{0,30}(\d{2,4})\s*g", "carbs_g"),
        (r"(\d{2,4})\s*g\s*(?:of\s+)?fat|\bfat\b[^0-9]{0,30}(\d{2,4})\s*g", "fat_g"),
    ]
    for pattern, unit in patterns:
        m = re.search(pattern, text)
        if m:
            for grp in m.groups():
                if grp:
                    try:
                        return (float(grp), unit)
                    except ValueError:
                        pass
    return None


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
            # Capture old book details BEFORE finishing so we can announce them.
            old_title: str | None = None
            old_started_day: int | None = None
            old_cover_url: str | None = None
            if has_current:
                try:
                    async with db._conn.execute(
                        """SELECT title, started_day, cover_url FROM books
                           WHERE telegram_id = ? AND finished_day IS NULL
                           ORDER BY id DESC LIMIT 1""",
                        (user_id,),
                    ) as cur:
                        old_row = await cur.fetchone()
                    if old_row:
                        old_title = old_row["title"]
                        old_started_day = old_row["started_day"]
                        old_cover_url = old_row["cover_url"]
                except Exception:
                    pass
                await db.finish_book(user_id, finished_day=day)
            await db.set_current_book(user_id, title, started_day=day, cover_url=cover_url or None)

            # Announce the finish to the group. Trust the user's word — no confirm step.
            if old_title and context is not None and GROUP_CHAT_ID:
                try:
                    user_row = await db.get_user(user_id)
                    user_name = (dict(user_row).get("name") if user_row else None) or "someone"
                    duration = ""
                    if old_started_day and old_started_day <= day:
                        days_taken = day - old_started_day + 1
                        duration = f" (took {days_taken} day{'s' if days_taken != 1 else ''})"
                    announcement = (
                        f"📚 {user_name} just finished {old_title}{duration}. "
                        f"next up: {title}. respect."
                    )
                    if old_cover_url:
                        await context.bot.send_photo(
                            chat_id=GROUP_CHAT_ID,
                            photo=old_cover_url,
                            caption=announcement,
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=GROUP_CHAT_ID,
                            text=announcement,
                        )
                    try:
                        await db.log_event(user_id, None, "book_finished",
                                           f"old={old_title!r} new={title!r}")
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("book finish announcement failed: %s", e)

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

        # Auto-flip diet_done when the user crosses their numeric goal threshold
        # (e.g. 170g protein). Idempotent: only flips if currently 0.
        goal_msg = ""
        goal = _parse_diet_goal(diet_plan)
        if goal:
            goal_value, goal_unit = goal
            total_for_goal = sum(
                e.get("extracted_value", 0) or 0
                for e in entries
                if e.get("extracted_unit") == goal_unit
            )
            if total_for_goal >= goal_value:
                checkin = await db.get_checkin(user_id, day)
                if checkin and not checkin["diet_done"]:
                    _, just_completed = await db.toggle_diet(user_id, day)
                    goal_msg = f" 🎯 GOAL HIT — diet auto-confirmed for today ({total_for_goal:.0f}/{goal_value:.0f} {goal_unit})."
                    if just_completed and context is not None:
                        await _maybe_fire_completion_eggs(user_id, day)
                    # Signal a card refresh so the user sees the diet checkmark
                    return (
                        f"REFRESH_CARD: Logged: {entry_text}. Running total: {total_for_goal:.0f} {goal_unit}."
                        f"{goal_msg}"
                    )

        if extracted_unit and extracted_value is not None:
            total = sum(e.get("extracted_value", 0) or 0 for e in entries if e.get("extracted_unit") == extracted_unit)
            return f"Logged: {entry_text}. Running total: {total:.0f} {extracted_unit}. Diet goal: {diet_plan}. {len(entries)} entries today.{goal_msg}"
        else:
            return f"Logged: {entry_text}. {len(entries)} entries today. Diet goal: {diet_plan}.{goal_msg}"

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
        if not deleted:
            return "Nothing to undo — no entries logged today."

        entries = await db.get_diet_entries(user_id, day)

        # If removing the entry brought the user back below their numeric diet
        # goal, un-flip diet_done. Mirrors log_food's auto-flip path — without
        # this, a user who crossed the goal and then undid the crossing entry
        # stays "diet done" while their actual log is below goal (silent
        # incongruence between DM state and the daily card).
        diet_unflipped = False
        user = await db.get_user(user_id)
        diet_plan = dict(user).get("diet_plan") if user else None
        goal = _parse_diet_goal(diet_plan)
        if goal:
            goal_value, goal_unit = goal
            total_for_goal = sum(
                e.get("extracted_value", 0) or 0
                for e in entries
                if e.get("extracted_unit") == goal_unit
            )
            if total_for_goal < goal_value:
                checkin = await db.get_checkin(user_id, day)
                if checkin and checkin["diet_done"]:
                    await db.toggle_diet(user_id, day)
                    diet_unflipped = True

        if diet_unflipped:
            return (
                f"REFRESH_CARD: Last entry removed. {len(entries)} entries remaining. "
                f"You're now back below your goal — diet un-confirmed for today."
            )
        return f"Last entry removed. {len(entries)} entries remaining today."

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

    elif tool_name == "log_reading_dm":
        # Ensure checkin row exists
        checkin = await db.get_checkin(user_id, day)
        if not checkin:
            await db.create_checkin(user_id, day, today_et().isoformat())

        # Resolve book title — use user's current_book as fallback
        book_title = (tool_input.get("book_title") or "").strip()
        if not book_title:
            user = await db.get_user(user_id)
            book_title = (dict(user).get("current_book") if user else None) or "unknown"

        takeaway = (tool_input.get("takeaway") or "").strip()
        just_completed = await db.log_reading(user_id, day, book_title, takeaway)
        if just_completed:
            await _maybe_fire_completion_eggs(user_id, day)
        return f"REFRESH_CARD: Reading logged for today — '{book_title}'" + (f" (takeaway: {takeaway[:80]})" if takeaway else "")

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

        # Resolve target day. Defaults to yesterday but accepts explicit day_number.
        target_day = tool_input.get("day_number")
        if target_day is None:
            target_day = day - 1
        else:
            target_day = int(target_day)

        if target_day < 1:
            return "DENIED: There's no Day 0 or earlier. The challenge starts at Day 1."
        if target_day > day:
            return f"DENIED: Day {target_day} hasn't happened yet. Today is Day {day}."

        yesterday = day - 1

        # Per-day cutoff (revised v51, was noon PT): strict midnight PT lock.
        # Backfill window for day-N stays open during all of day-N's PT calendar
        # date AND the following PT calendar date (= "today" while card_day = N+1).
        # Lock fires at 00:00 PT of the day after that — i.e., once
        # (pt_today - target_anchor_pt_date) > 1, the window is closed.
        # Day 1 has a grace window — always allowed regardless of clock.
        # Retro grace: if Bryan opened /admin_open_retro_audit, the window is
        # wide open (any past day) until the configured day passes.
        is_day_1_grace = target_day == 1
        retro_active = await db.is_retro_grace_active(day)
        if not is_day_1_grace and not retro_active:
            if target_day < yesterday:
                return f"DENIED: Day {target_day} is more than 1 day old — backfill window for that day is permanently closed. Ask the organizer for grace."

            # Strict midnight PT cutoff for target_day == yesterday OR target_day == today.
            # Anchor preferred: the checkin row's checkin_date (set when the morning
            # card posted, == ET-date which equals PT-date since cards post 7am ET / 4am PT).
            # Fallback (no row yet): subtract day-offset from current PT date.
            target_checkin_row = await db.get_checkin(user_id, target_day)
            if target_checkin_row and target_checkin_row["date"]:
                target_anchor = date.fromisoformat(target_checkin_row["date"])
            else:
                offset_days = day - target_day
                target_anchor = now_pt.date() - timedelta(days=offset_days)
            days_past_anchor = (now_pt.date() - target_anchor).days
            if days_past_anchor > 1:
                return f"DENIED: It's past midnight PT — Day {target_day} is locked. Backfill window closed."

        # Ensure checkin exists for the target day
        checkin = await db.get_checkin(user_id, target_day)
        if not checkin:
            offset_days = day - target_day
            target_date = (now_pt.date() - timedelta(days=offset_days)).isoformat()
            await db.create_checkin(user_id, target_day, target_date)

        task = tool_input["task"]
        detail = tool_input.get("detail", "")

        if task == "workout_outdoor":
            wtype = detail or "workout"
            slot, _ = await db.log_workout(user_id, target_day, wtype, "outdoor")
            result_msg = f"REFRESH_CARD: Backfilled outdoor {wtype} for day {target_day}, slot {slot}/2"
        elif task == "workout_indoor":
            wtype = detail or "workout"
            slot, _ = await db.log_workout(user_id, target_day, wtype, "indoor")
            result_msg = f"REFRESH_CARD: Backfilled indoor {wtype} for day {target_day}, slot {slot}/2"
        elif task == "water":
            cups = 16  # default to full gallon
            if detail:
                try:
                    cups = int(detail)
                except ValueError:
                    cups = 16
            await db.set_water(user_id, target_day, min(cups, 16))
            result_msg = f"REFRESH_CARD: Backfilled water for day {target_day} -- set to {min(cups, 16)}/16 cups"
        elif task == "reading":
            book_title = detail or "unknown"
            await db.log_reading(user_id, target_day, book_title, "")
            result_msg = f"REFRESH_CARD: Backfilled reading for day {target_day}"
        elif task == "diet":
            checkin = await db.get_checkin(user_id, target_day)
            if not checkin["diet_done"]:
                await db.toggle_diet(user_id, target_day)
            result_msg = f"REFRESH_CARD: Backfilled diet for day {target_day}"
        else:
            return f"Unknown task type: {task}"

        # Audit trail for retroactive edits — anything older than yesterday
        # is by definition retro and worth flagging in event_log so audits
        # can see how often history is being rewritten.
        if target_day < yesterday and target_day != 1:
            await db.log_event(
                user_id, None, "retroactive_edit",
                f"tool=backfill_task task={task} target_day={target_day} card_day={day}",
            )

        return result_msg

    elif tool_name == "declare_penance":
        from bot.penance import PENANCE_ABLE_TASKS

        task = tool_input.get("task")
        if not task:
            return "DENIED: declare_penance requires a specific task. Ask the user which task before calling this."
        if task not in PENANCE_ABLE_TASKS:
            return (
                f"DENIED: '{task}' is not penance-able. Penance applies to action-quantity tasks "
                "(workout_indoor, workout_outdoor, water, reading, photo). "
                "For diet violations use log_violation."
            )

        missed_day = tool_input.get("missed_day")
        if missed_day is None:
            missed_day = day - 1
        else:
            missed_day = int(missed_day)
        if missed_day < 1:
            return "DENIED: There's no Day 0 or earlier."
        if missed_day >= day:
            return f"DENIED: Day {missed_day} hasn't been missed yet — that's today or later."

        # Outside the retro grace window, only yesterday is penance-able.
        # Older days require Bryan to open /admin_open_retro_audit first.
        yesterday = day - 1
        if missed_day < yesterday:
            retro_active = await db.is_retro_grace_active(day)
            if not retro_active:
                return (
                    f"DENIED: Day {missed_day} is more than 1 day old. The retro-audit window "
                    "isn't open right now — ask the organizer to open it if you need to fix history."
                )

        # Don't double-declare: one in_progress / recovered penance per (user, missed_day, task).
        existing = await db.get_penances_for_missed_day(user_id, missed_day)
        for r in existing:
            r = dict(r)
            if r["task"] == task and r["status"] in ("in_progress", "recovered"):
                return (
                    f"DENIED: You already have an active or recovered penance for "
                    f"{task.replace('_', ' ')} on day {missed_day}. No double-declaring."
                )

        makeup_day = day  # makeup happens today
        retroactive = (missed_day < day - 1)
        detail = tool_input.get("detail") or None
        await db.add_penance(
            telegram_id=user_id,
            missed_day=missed_day,
            makeup_day=makeup_day,
            task=task,
            retroactive=retroactive,
            detail=detail,
        )
        await db.log_event(
            user_id, None, "penance_declared",
            f"missed_day={missed_day} task={task} retro={retroactive}",
        )
        # Friendly confirmation. Tone follows critical_rules — Luke's phrasing wraps this.
        task_friendly = task.replace("_", " ")
        return (
            f"REFRESH_CARD: penance set for {task_friendly}, missed day {missed_day}. "
            f"makeup target is 2x today (day {day}). cutoff is midnight PT tonight. "
            f"if 2x not hit by then → fail flow."
        )

    elif tool_name == "request_backfill_photo":
        import pytz as _pytz
        _PT = _pytz.timezone("US/Pacific")
        now_pt = datetime.now(_PT)  # used below for create_checkin date math

        target_day = int(tool_input.get("day_number", day - 1))
        yesterday = day - 1

        if target_day < 1:
            return "DENIED: There's no day before Day 1 to backfill."
        if target_day > day:
            return "DENIED: That day hasn't happened yet."
        if target_day < yesterday:
            return f"DENIED: Day {target_day} is too far back. Photos can only be backfilled for yesterday."

        # Strict midnight PT cutoff (v51): photo backfill window for target_day
        # closes at 00:00 PT of the day after target_day's PT calendar date.
        # Day 1 always graced. NOTE: target_day < yesterday already denied above.
        is_day_1_grace = target_day == 1
        if not is_day_1_grace and target_day == yesterday:
            target_checkin_row = await db.get_checkin(user_id, target_day)
            if target_checkin_row and target_checkin_row["date"]:
                target_anchor = date.fromisoformat(target_checkin_row["date"])
            else:
                offset_days = day - target_day
                target_anchor = now_pt.date() - timedelta(days=offset_days)
            days_past_anchor = (now_pt.date() - target_anchor).days
            if days_past_anchor > 1:
                return "DENIED: It's past midnight PT — yesterday's photo window closed."

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

    elif tool_name == "declare_self_fail":
        from bot.config import (
            BUY_IN, PRIZE_POOL_VENMO_USERNAME, PRIZE_POOL_ZELLE_PHONE,
        )
        reason = (tool_input.get("reason") or "").strip()[:200]
        await db.log_event(
            user_id, None, "self_fail_declared",
            f"day={day} reason={reason}" if reason else f"day={day}",
        )
        owed = max(0, BUY_IN - day)
        remaining_days = max(0, 75 - day)
        venmo_line = f"venmo: @{PRIZE_POOL_VENMO_USERNAME}" if PRIZE_POOL_VENMO_USERNAME else None
        zelle_line = f"zelle: {PRIZE_POOL_ZELLE_PHONE}" if PRIZE_POOL_ZELLE_PHONE else None
        pay_lines = [l for l in (venmo_line, zelle_line) if l]
        if pay_lines:
            pay_block = "\n".join(pay_lines)
            return (
                f"got it. you're declaring fail on day {day}.\n\n"
                f"the residual is ${owed} ({remaining_days} days you didn't finish, into the prize pool).\n"
                f"{pay_block}\n\n"
                f"reply 'paid' when sent. admin will confirm and post in the group. "
                f"if you change your mind before payment lands, /redeem is still on the table later."
            )
        return (
            f"got it. you're declaring fail on day {day}. "
            f"residual is ${owed} ({remaining_days} days × $1 to the prize pool). "
            f"DM the organizer to settle. /redeem available later."
        )

    elif tool_name == "log_violation":
        from bot.penance import BINARY_TASKS
        from bot.config import GROUP_CHAT_ID

        task = tool_input.get("task")
        detail = (tool_input.get("detail") or "").strip()
        if not task or task not in BINARY_TASKS:
            return (
                "DENIED: log_violation only handles binary-task violations (currently 'diet'). "
                "For action-task misses, use declare_penance."
            )
        if not detail:
            return "DENIED: log_violation requires a `detail` describing the violation for the group to judge."

        missed_day = tool_input.get("missed_day")
        if missed_day is None:
            missed_day = day
        else:
            missed_day = int(missed_day)
        if missed_day < 1 or missed_day > day:
            return f"DENIED: invalid missed_day {missed_day} (today is day {day})."

        # Don't double-arbitrate: one open arbitration per (user, missed_day, task).
        existing = await db.get_penances_for_missed_day(user_id, missed_day)
        for r in existing:
            r = dict(r)
            if r["task"] == task and r["status"] in ("in_progress", "arbitration_pending"):
                return (
                    f"DENIED: there's already an open case for {task} on day {missed_day} "
                    "(arbitration pending or active penance). no double-filing."
                )

        # makeup_day = missed_day for arbitration rows (no makeup is happening, but
        # the column is NOT NULL — we keep the schema unified by reusing missed_day).
        retroactive = (missed_day < day)
        detail_clean = detail[:300]
        pid = await db.add_penance(
            telegram_id=user_id,
            missed_day=missed_day,
            makeup_day=missed_day,
            task=task,
            retroactive=retroactive,
            detail=detail_clean,
            status="arbitration_pending",
        )
        await db.log_event(
            user_id, None, "violation_logged",
            f"day={missed_day} task={task} detail={detail_clean[:80]}",
        )

        # Send group poll if context + bot are available. In tests / offline,
        # we still create the row so the admin can /admin_arbitrate manually.
        if context is not None and getattr(context, "bot", None) is not None and GROUP_CHAT_ID:
            try:
                user_row = await db.get_user(user_id)
                name = user_row["name"] if user_row else "someone"
                question = f"{name} logged a {task} violation on day {missed_day}: {detail_clean}. verdict?"
                # Telegram poll questions cap at 300 chars.
                question = question[:295] + "..." if len(question) > 300 else question
                poll_msg = await context.bot.send_poll(
                    chat_id=GROUP_CHAT_ID,
                    question=question,
                    options=["pass", "penance", "fail"],
                    is_anonymous=False,
                    allows_multiple_answers=False,
                )
                poll_obj = poll_msg.poll
                if poll_obj is not None:
                    await db.attach_arbitration_poll(
                        pid,
                        poll_id=str(poll_obj.id),
                        poll_message_id=poll_msg.message_id,
                    )
            except Exception as e:
                logger.warning("Failed to send arbitration poll for penance %d: %s", pid, e)

        return (
            f"REFRESH_CARD: violation logged. case #{pid} for {task} day {missed_day}. "
            f"posted to the group for vote. organizer renders the verdict — "
            f"could be pass, penance, or fail."
        )

    elif tool_name == "get_my_compliance_grid":
        return "MEDIA:compliance_grid"

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


# Patterns that indicate Luke emitted a numerical state claim. Used both at output
# validation time (to catch phantoms) and at history hydration (to skip rows that
# were likely phantoms so they don't poison future turns).
_PHANTOM_TEXT_PATTERNS = [
    re.compile(r"\b\d+\s*g\s*[,.]?\s*\d+\s*(?:to go|left|remaining)\b", re.I),
    re.compile(r"\bgoal\s+(?:hit|smashed|reached|locked)\b", re.I),
    re.compile(r"\b\d+\s*g\s*,\s*goal\s+(?:hit|smashed)\b", re.I),
    re.compile(r"\byou'?re\s+at\s+\d+\s*(?:g|cups?|/\s*16)\b", re.I),
    re.compile(r"\b\d+\s*g\s+(?:so far|today|down)\b", re.I),
    re.compile(r"\b\d+\s*/\s*16\s*(?:cups)?\b", re.I),
    re.compile(r"\b(?:added|bumped to|now at)\s+\d+\b", re.I),
    re.compile(r"\bday\s+\d+\s+at\s+\d+\s*g\b", re.I),
    # Penance state claims: catch "your penance is set" / "marked as penance" /
    # "set up penance for X" without a declare_penance tool call this turn.
    re.compile(r"\b(?:your\s+)?penance\s+(?:is\s+)?(?:set|marked|locked|active)\b", re.I),
    re.compile(r"\b(?:set\s+up|marked\s+as)\s+penance\b", re.I),
    re.compile(r"\bpenance\s+for\s+\w+\s+(?:set|locked|active)\b", re.I),
    # Violation state claims: catch "case filed" / "logged the violation" /
    # "posted to the group" without a log_violation tool call this turn.
    re.compile(r"\b(?:case|violation)\s+(?:filed|logged|posted|opened)\b", re.I),
    re.compile(r"\bposted\s+(?:it\s+)?to\s+the\s+group\b", re.I),
    re.compile(r"\bsquad\s+(?:will|gets to|is)\s+vot", re.I),
]

# Tool classes that legitimize a numerical state claim. If Luke claims a diet total,
# at least one diet-context tool must have run this turn. Same for water and penance.
_DIET_TOOLS = {
    "log_food", "get_diet_progress", "get_diet_log_for_day",
    "get_my_status", "get_my_status_for_day",
    "undo_last_food", "confirm_diet_dm",
}
_WATER_TOOLS = {
    "log_water_dm", "fix_water", "backfill_task",
    "get_my_status", "get_my_status_for_day",
}
_PENANCE_TOOLS = {"declare_penance"}
_VIOLATION_TOOLS = {"log_violation"}


def _state_claim_pattern_class(matched_text: str) -> str:
    """Classify a matched state-claim snippet into a tool family
    ('violation', 'penance', 'water', or 'diet')."""
    t = matched_text.lower()
    if "case" in t or "violation" in t or "posted" in t or "vot" in t:
        return "violation"
    if "penance" in t:
        return "penance"
    if "/16" in t or "cup" in t:
        return "water"
    return "diet"


def _check_state_claims(text: str, tools_called: list[str]) -> tuple[str, str] | None:
    """If Luke output a numerical state claim without a backing tool call this turn,
    return (claim_class, snippet). Otherwise None.

    Rationale: the v46 phrase detector caught lead-in narration ("logging now").
    Claude evolved past it by skipping the narration and emitting hallucinated
    totals directly. This is the structural detector — independent of phrasing.
    """
    if not text:
        return None
    tools_set = set(tools_called)
    for pattern in _PHANTOM_TEXT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        claim_class = _state_claim_pattern_class(m.group(0))
        if claim_class == "violation":
            valid_tools = _VIOLATION_TOOLS
        elif claim_class == "penance":
            valid_tools = _PENANCE_TOOLS
        elif claim_class == "water":
            valid_tools = _WATER_TOOLS
        else:
            valid_tools = _DIET_TOOLS
        if not (tools_set & valid_tools):
            return (claim_class, m.group(0))
    return None


def _looks_like_phantom_row(luke_response: str | None, tools_called: str | None) -> bool:
    """Heuristic: was this conversation_log row likely a phantom?

    We exclude such rows from history hydration so old lies don't seed the next
    turn's context. Conservative: if a tool was called, trust the row.
    """
    if not luke_response:
        return False
    if tools_called:  # had at least one tool — assume legitimate
        return False
    return any(p.search(luke_response) for p in _PHANTOM_TEXT_PATTERNS)


async def _build_session_context(user_id: int, db) -> str:
    """Snapshot DB truth at the start of a chat turn.

    Returns a SESSION CONTEXT block to append to the system prompt. Gives Luke
    a fresh, authoritative anchor every turn so he can't drift via stale chat
    history. Stops the "I'll continue the running total in my head" failure mode.
    """
    today = today_et()
    day = await get_current_challenge_day(db)
    yesterday = day - 1

    # Today's checkin status
    checkin = await db.get_checkin(user_id, day)
    if checkin:
        c = dict(checkin)
        status_block = (
            f"  workout 1: {'done' if c.get('workout_1_done') else 'not done'}\n"
            f"  workout 2: {'done' if c.get('workout_2_done') else 'not done'}\n"
            f"  water: {c.get('water_cups', 0)}/16 cups\n"
            f"  diet: {'confirmed' if c.get('diet_done') else 'not confirmed'}\n"
            f"  reading: {'done' if c.get('reading_done') else 'not done'}\n"
            f"  photo: {'done' if c.get('photo_done') else 'not done'}"
        )
    else:
        status_block = "  (no checkin row yet — user hasn't logged anything today)"

    # Today's diet log entries with running totals
    try:
        diet_entries = await db.get_diet_entries(user_id, day)
    except Exception:
        diet_entries = []
    if diet_entries:
        units: dict[str, float] = {}
        for e in diet_entries:
            if e.get("extracted_value") and e.get("extracted_unit"):
                u = e["extracted_unit"]
                units[u] = units.get(u, 0.0) + e["extracted_value"]
        totals_str = ", ".join(f"{v:.0f} {u}" for u, v in units.items()) or "no numeric totals"
        diet_block = f"  {len(diet_entries)} entries, totals: {totals_str}"
    else:
        diet_block = "  0 entries logged today"

    # Diet plan
    user = await db.get_user(user_id)
    diet_plan = (dict(user).get("diet_plan") if user else None) or "(not set)"

    return (
        "---\n"
        "SESSION CONTEXT (DB truth, freshly read at start of this turn — overrides chat memory):\n"
        f"  today is Day {day} ({today.isoformat()})\n"
        f"  yesterday was Day {yesterday}\n"
        f"  user's diet plan: {diet_plan}\n"
        "\n"
        "today's checkin:\n"
        f"{status_block}\n"
        "\n"
        "today's diet log:\n"
        f"{diet_block}\n"
        "\n"
        "Use these numbers when the user asks about totals or status. Do NOT\n"
        "compute totals from chat memory — that has been wrong before. If the\n"
        "user logs new food/water this turn, the tool's return value supersedes\n"
        "the snapshot above (which was taken before the tool ran)."
    )


async def _ensure_history_loaded(user_id: int, db) -> None:
    """Hydrate _chat_history[user_id] from conversation_log on first access.

    Lets chat memory survive bot restarts. Idempotent — only loads once per process.
    Skips entries with image content since we don't store image bytes in the log.
    Skips rows that look like phantom-action outputs (state claims with no backing
    tool call) so old lies don't reseed the next turn's context.
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
        tc = r["tools_called"] if "tools_called" in r.keys() else None
        # Skip rows with image markers — we can't reconstruct the image
        if u_msg.startswith("[image]"):
            continue
        # Skip likely-phantom rows so old lies don't poison the next turn
        if _looks_like_phantom_row(l_msg, tc):
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

        # Build dynamic system prompt = static base + per-turn DB truth snapshot.
        # The session context anchors today/yesterday and current totals so Luke
        # can't drift via stale chat history.
        session_context = await _build_session_context(user_id, db)
        system_prompt = LUKE_CHAT_SYSTEM + "\n\n" + session_context

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
                    system=system_prompt,
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

        # Phantom-action detection — two variants, both ending with the same
        # warning to the user. The user thinks an action happened but it didn't,
        # and we can't recover server-side (we don't know what Luke meant).
        #
        # Variant A (v46 phrase detector): Luke narrated intent ("logging now",
        # "let me log") with no tool call. Catches obvious lead-ins.
        #
        # Variant B (v50 state-claim detector): Luke skipped the narration and
        # emitted a hallucinated total ("135g, 35 to go") with no diet/water
        # tool to back it. Catches the post-v46 evolution where Claude routed
        # around the phrase list.
        ACTION_PHRASES = (
            "logging now", "let me log", "let me actually log", "logging it",
            "logging everything", "let me push", "logging all",
            "give me a sec", "hold up", "hold on, let me", "give me one sec",
        )
        text_lower = text.lower()
        phantom_variant: str | None = None
        phantom_detail: str | None = None

        if not tools_called and any(p in text_lower for p in ACTION_PHRASES):
            phantom_variant = "action_phrase_no_tool"
            phantom_detail = text[:120]

        if phantom_variant is None:
            state_claim = _check_state_claims(text, tools_called)
            if state_claim:
                claim_class, snippet = state_claim
                phantom_variant = f"state_claim_no_{claim_class}_tool"
                phantom_detail = f"snippet={snippet!r} tools={tools_called}"

        if phantom_variant is not None:
            logger.warning(
                "AI_CHAT_PHANTOM_ACTION user_id=%d variant=%s detail=%s text=%r",
                user_id, phantom_variant, phantom_detail, text[:200],
            )
            try:
                await db.log_event(user_id, None, "ai_chat_phantom_action",
                                   f"variant={phantom_variant} {phantom_detail or ''}"[:240])
            except Exception:
                pass
            # Append a clear correction so the user knows to retry
            text = (
                text.rstrip(".:!?") + " — actually wait, glitch on my end. nothing got "
                "logged. resend and i'll do it for real this time."
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
