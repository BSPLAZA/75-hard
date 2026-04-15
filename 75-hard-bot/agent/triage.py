"""Claude-powered feedback triage for the 75 Hard bot."""

from __future__ import annotations

import json
import logging
import os
import textwrap

import anthropic

from bot.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an engineering triage agent for a Telegram bot called "Luke" that runs \
    a 75 Hard fitness challenge for a small group of friends.

    Architecture overview:
    - Python bot built with python-telegram-bot (async, v20+)
    - SQLite database via aiosqlite (bot/database.py)
    - Handlers in bot/handlers/: onboarding, daily_card, water, diet, workout, \
    reading, photo, feedback, admin, transformation
    - Scheduled jobs in bot/jobs/scheduler.py
    - Utilities in bot/utils/: card_renderer, image_generator, luke_ai (Claude \
    integration), luke_chat (conversational AI), books, easter_eggs, intent, \
    photo_transform, progress
    - Config in bot/config.py (env vars: BOT_TOKEN, ADMIN_USER_ID, GROUP_CHAT_ID, \
    CHALLENGE_START_DATE, DATABASE_PATH, ANTHROPIC_API_KEY)
    - Deployed on Fly.io with a Dockerfile

    The feedback comes from real users during an active 75 Hard challenge (5 \
    participants). Feedback types: "feedback" (general), "bug" (something broken), \
    "suggest" (feature request).

    Your job: classify each piece of feedback and recommend an action.

    Respond with ONLY valid JSON (no markdown fences, no extra text) in this format:
    {
        "severity": "critical" | "important" | "nice-to-have",
        "action": "fix_code" | "update_config" | "respond_to_user" | "defer",
        "summary": "1-2 sentence summary of the issue",
        "recommendation": "What to do about it, specific enough to act on",
        "files_to_change": ["bot/handlers/example.py"]
    }

    Severity guide:
    - critical: bot is broken, users can't complete daily tasks, data loss
    - important: UX issue that's frustrating but workaround exists, wrong behavior
    - nice-to-have: feature request, cosmetic issue, minor improvement

    Action guide:
    - fix_code: requires changing Python source files
    - update_config: can be fixed by changing env vars or config values
    - respond_to_user: user confusion or misunderstanding, just needs a reply
    - defer: not urgent, save for later

    For fix_code actions, list the specific files that likely need changes in \
    files_to_change. Be specific about what the fix should look like in your \
    recommendation.
""")


async def triage_feedback(item: dict) -> dict:
    """Triage a feedback item using Claude.

    Args:
        item: A dict (or Row) with keys: id, telegram_id, type, text, context,
              status, created_at.

    Returns:
        dict with keys:
            severity: "critical" | "important" | "nice-to-have"
            action: "fix_code" | "update_config" | "respond_to_user" | "defer"
            summary: str
            recommendation: str
            files_to_change: list[str]
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY set -- returning default triage.")
        return {
            "severity": "important",
            "action": "defer",
            "summary": f"[No API key] {item['type']}: {item['text'][:80]}",
            "recommendation": "Configure ANTHROPIC_API_KEY to enable AI triage.",
            "files_to_change": [],
        }

    user_message = (
        f"Feedback type: {item['type']}\n"
        f"Text: {item['text']}\n"
        f"Context: {item.get('context', 'none')}\n"
        f"Submitted: {item.get('created_at', 'unknown')}"
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=TRIAGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)

        # Validate expected keys
        expected = {"severity", "action", "summary", "recommendation", "files_to_change"}
        for key in expected:
            if key not in result:
                result[key] = "" if key != "files_to_change" else []

        return result

    except json.JSONDecodeError as e:
        logger.error("Triage returned non-JSON: %s", e)
        return {
            "severity": "important",
            "action": "defer",
            "summary": f"[Parse error] {item['type']}: {item['text'][:80]}",
            "recommendation": "Triage AI returned unparseable response. Review manually.",
            "files_to_change": [],
        }
    except Exception as e:
        logger.error("Triage failed: %s", e)
        return {
            "severity": "important",
            "action": "defer",
            "summary": f"[Error] {item['type']}: {item['text'][:80]}",
            "recommendation": f"Triage failed with error: {e}",
            "files_to_change": [],
        }


PATCH_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a senior Python engineer. You generate unified diff patches for a \
    Telegram bot codebase.

    Architecture overview:
    - Python bot built with python-telegram-bot (async, v20+)
    - SQLite database via aiosqlite (bot/database.py)
    - Handlers in bot/handlers/
    - Utilities in bot/utils/
    - Config in bot/config.py

    Generate a unified diff patch (git diff format) that fixes the described issue. \
    The patch must be valid and applicable with `git apply`.

    Output ONLY the raw patch content. No markdown fences, no explanation, no \
    commentary. Start directly with "diff --git" or "---".
""")


async def generate_patch(item: dict, triage: dict) -> str | None:
    """Generate a code patch for a triaged feedback item.

    Args:
        item: The original feedback dict.
        triage: The triage result dict.

    Returns:
        The patch content as a string, or None on failure.
    """
    if not ANTHROPIC_API_KEY:
        return None

    prompt = (
        f"Issue: {triage['summary']}\n"
        f"Original feedback: {item['text']}\n"
        f"Recommendation: {triage['recommendation']}\n"
        f"Files to change: {', '.join(triage.get('files_to_change', []))}\n\n"
        "Generate a unified diff patch to fix this."
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=PATCH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("Patch generation failed: %s", e)
        return None
