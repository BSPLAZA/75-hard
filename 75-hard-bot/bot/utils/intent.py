"""Natural language intent classifier using Claude Haiku."""

import json
import logging

import anthropic

from bot.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

CLASSIFIER_PROMPT = """You classify user messages in a 75 Hard challenge bot. Return JSON only.

Available intents:
- set_book: user wants to change/set their book. Extract the book title.
- set_diet: user wants to change/set their diet plan. Extract the plan.
- feedback: user has general feedback about the bot.
- bug: user is reporting something broken.
- suggestion: user has an idea for improvement.
- status: user wants to know their progress or stats.
- help: user needs help or doesn't know what to do.
- chat: general conversation, greeting, or anything that doesn't match above.

Return JSON with exactly these keys:
{"intent": "one_of_above", "params": {"key": "value"}, "confidence": 0.0-1.0}

For set_book, params should have "title".
For set_diet, params should have "plan".
For feedback/bug/suggestion, params should have "text" (the content).
For others, params can be empty {}.

Examples:
"I just started reading Atomic Habits" -> {"intent": "set_book", "params": {"title": "Atomic Habits"}, "confidence": 0.95}
"can I switch my diet to keto" -> {"intent": "set_diet", "params": {"plan": "keto"}, "confidence": 0.9}
"the water button isn't working" -> {"intent": "bug", "params": {"text": "water button isn't working"}, "confidence": 0.9}
"hey luke" -> {"intent": "chat", "params": {}, "confidence": 0.8}
"how am I doing" -> {"intent": "status", "params": {}, "confidence": 0.85}
"""


async def classify_intent(message: str) -> dict | None:
    """Classify a natural language message into an intent.

    Returns dict with keys: intent, params, confidence. Or None on failure.
    """
    if not ANTHROPIC_API_KEY:
        return None

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system=CLASSIFIER_PROMPT,
            messages=[{"role": "user", "content": message}],
        )
        text = response.content[0].text.strip()

        # Parse JSON — handle cases where model wraps in markdown
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        result = json.loads(text)
        return result
    except Exception as e:
        logger.warning("Intent classification failed: %s", e)
        return None
