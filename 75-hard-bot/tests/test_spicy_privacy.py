"""Tests for the spicy-moment privacy guard.

Pinned contracts:
  1. SPICY_MOMENT_SYSTEM prompt forbids enumerating specific food items/counts.
  2. The food summary fed into the prompt is AGGREGATE only (entry count +
     total grams), not raw entry_text.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import inspect

from bot.utils import luke_ai
from bot.jobs import scheduler


def test_spicy_system_forbids_food_enumeration():
    sys = luke_ai.SPICY_MOMENT_SYSTEM.lower()
    # Must explicitly call out food privacy
    assert "private" in sys
    # Must contain an example / instruction against item-by-item food recap
    assert "enumerate" in sys or "itemized" in sys or "item-by-item" in sys
    # The "BAD" example phrasing should be present so the model has a clear anti-pattern
    assert "bad" in sys and "good" in sys


def test_spicy_system_calls_out_specific_violations():
    """The fix landed because Luke posted 'four eggs, chicken breast, 12 nuggets'.
    The prompt must include a concrete BAD example so future drift is unlikely."""
    sys = luke_ai.SPICY_MOMENT_SYSTEM
    # Either an explicit anti-pattern or a 'volume framing only' rule must exist
    assert "volume" in sys.lower() or "bodybuilder" in sys.lower()


def test_spicy_input_aggregates_food_not_itemizes():
    """The scheduler.spicy_moment_job builds the food_summary string. It should
    no longer concatenate raw entry_text values from diet_log; instead, only
    the count of entries and total grams should be passed."""
    src = inspect.getsource(scheduler.spicy_moment_job)
    # Old behavior we removed: short = [f'{e.get("entry_text",...)[:40]}' for e in entries]
    assert "entry_text" not in src, (
        "spicy_moment_job must not pass raw entry_text into the prompt"
    )
    # New behavior: aggregate counts + grams
    assert "entries" in src
    assert "g protein" in src or "extracted_value" in src
