"""Tests for chat_with_luke source parameter + DM-history filter.

Pinned contracts:
  1. chat_with_luke writes conversation_log with the supplied source value
     (defaulting to 'dm' for backwards compatibility).
  2. _ensure_history_loaded filters in-memory _chat_history to source='dm'
     rows only, so group exchanges don't bleed into DM context.
"""

import inspect
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import pytest
import pytest_asyncio

from bot.database import Database
from bot.utils import luke_chat


def test_chat_with_luke_signature_has_source_param():
    sig = inspect.signature(luke_chat.chat_with_luke)
    assert "source" in sig.parameters
    assert sig.parameters["source"].default == "dm"


def test_add_conversation_log_uses_source_variable():
    """Source-level: source param must be threaded into add_conversation_log
    rather than hardcoded 'dm' at the call site."""
    src = inspect.getsource(luke_chat.chat_with_luke)
    # Must NOT have hardcoded source="dm" anywhere in the body
    assert 'source="dm"' not in src
    assert "source='dm'" not in src
    # Must use the variable
    assert "source=source" in src


@pytest_asyncio.fixture
async def db_with_history():
    """Seed conversation_log with mixed-source rows for one user."""
    database = Database(":memory:")
    await database.init()
    await database.add_user(111, "Bryan", tier=75)
    await database.save_card(day_number=18, date="2026-05-02", message_id=1, chat_id=-1)
    # 4 rows: 2 dm, 2 group
    await database.add_conversation_log(
        telegram_id=111, user_name="Bryan", source="dm",
        user_message="dm question 1", luke_response="dm answer 1",
    )
    await database.add_conversation_log(
        telegram_id=111, user_name="Bryan", source="group",
        user_message="group question 1", luke_response="group answer 1",
    )
    await database.add_conversation_log(
        telegram_id=111, user_name="Bryan", source="dm",
        user_message="dm question 2", luke_response="dm answer 2",
    )
    await database.add_conversation_log(
        telegram_id=111, user_name="Bryan", source="group",
        user_message="group question 2", luke_response="group answer 2",
    )
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_history_loader_filters_to_dm_only(db_with_history):
    """_ensure_history_loaded must skip source!='dm' rows."""
    # Reset the per-process cache so the loader actually runs
    luke_chat._chat_history.pop(111, None)
    luke_chat._history_loaded.discard(111)

    await luke_chat._ensure_history_loaded(111, db_with_history)
    history = luke_chat._chat_history.get(111, [])
    flat = "\n".join(m.get("content", "") for m in history)

    # DM content must appear
    assert "dm question 1" in flat
    assert "dm answer 1" in flat
    assert "dm question 2" in flat
    # Group content must NOT
    assert "group question" not in flat
    assert "group answer" not in flat
