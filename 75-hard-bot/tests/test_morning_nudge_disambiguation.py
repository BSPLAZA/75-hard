"""Regression tests for the morning-nudge disambiguation flow (step 2).

Pins three contracts:
  1. The declare_penance tool definition exists and uses the right enum.
  2. critical_rule #8 (penance disambiguation) is in LUKE_CHAT_SYSTEM.
  3. The morning_after_reminder_job is wired to log its outbound DM into
     conversation_log so Luke's history hydrate sees it.

These don't test the live LLM behavior (impossible without mocking Anthropic),
but they pin the structural pieces that make the disambiguation possible.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import inspect

from bot.utils import luke_chat
from bot.jobs import scheduler
from bot.penance import PENANCE_ABLE_TASKS


# ── tool definition ────────────────────────────────────────────────────

def _find_tool(name: str) -> dict | None:
    for t in luke_chat.TOOLS:
        if t["name"] == name:
            return t
    return None


def test_declare_penance_tool_registered():
    tool = _find_tool("declare_penance")
    assert tool is not None, "declare_penance tool must be registered in luke_chat.TOOLS"


def test_declare_penance_enum_matches_penance_able_set():
    tool = _find_tool("declare_penance")
    enum = tool["input_schema"]["properties"]["task"]["enum"]
    assert set(enum) == PENANCE_ABLE_TASKS


def test_declare_penance_excludes_diet():
    tool = _find_tool("declare_penance")
    enum = tool["input_schema"]["properties"]["task"]["enum"]
    assert "diet" not in enum, "diet must not be penance-able — binary task only"


def test_declare_penance_requires_task():
    tool = _find_tool("declare_penance")
    assert tool["input_schema"]["required"] == ["task"]


def test_declare_penance_description_warns_against_vague_calls():
    """The tool description must explicitly tell the LLM not to call this
    without a specific task — that's the structural anti-phantom guard."""
    tool = _find_tool("declare_penance")
    desc = tool["description"].lower()
    assert "never call this without a specific task" in desc
    assert "ask" in desc  # tells the LLM to ask first when ambiguous


# ── system prompt critical rule 8 ──────────────────────────────────────

def test_critical_rule_8_present():
    """Rule 8 (penance disambiguation) must be in LUKE_CHAT_SYSTEM."""
    assert "8. PENANCE DISAMBIGUATION" in luke_chat.LUKE_CHAT_SYSTEM


def test_critical_rule_8_says_never_call_without_task():
    sys = luke_chat.LUKE_CHAT_SYSTEM
    assert "NEVER call declare_penance without a specific task" in sys


def test_critical_rule_8_routes_diet_violations_away_from_penance():
    sys = luke_chat.LUKE_CHAT_SYSTEM
    # The rule must point users with diet violations to log_violation, not declare_penance
    assert "log_violation" in sys
    assert "diet is binary, no penance possible" in sys


def test_critical_rule_8_blocks_phantom_penance_claims():
    sys = luke_chat.LUKE_CHAT_SYSTEM
    # State-claim-no-tool guard generalized to penance state
    assert (
        'NEVER claim "your penance is set"' in sys
        or "NEVER claim" in sys and "penance is set" in sys
    )


# ── morning nudge wiring ───────────────────────────────────────────────

def test_morning_after_reminder_job_logs_outbound_to_conversation_log():
    """The nudge must persist itself to conversation_log so Luke's history
    hydrate picks it up. Without this, the user's reply lands in Luke's chat
    in a vacuum — no prior context — and disambiguation fails."""
    src = inspect.getsource(scheduler.morning_after_reminder_job)
    assert "add_conversation_log" in src, (
        "morning_after_reminder_job must call db.add_conversation_log so "
        "Luke's history hydrate sees the outbound nudge"
    )
    assert "[bot_nudge:morning_after]" in src, (
        "the conversation_log row should be tagged so audits can identify "
        "bot-originated nudges vs real user turns"
    )


def test_morning_after_reminder_uses_disambiguation_phrasing():
    """The nudge text must invite the per-task backfill-or-penance disambiguation
    Luke is set up to handle. Pin the contract — copy can drift, structure can't."""
    src = inspect.getsource(scheduler.morning_after_reminder_job)
    text_block = src.lower()
    assert "did you do it and forget" in text_block or "forget to log" in text_block
    assert "penance" in text_block
    assert "midnight pacific" in text_block
