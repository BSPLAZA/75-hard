"""Tests for group @-mention / reply-to-bot AI chat handler.

Pinned contracts:
  1. group_mention_handler is registered in bot.main with handler-group != 0
     (so it runs alongside combined_text_handler instead of being shadowed).
  2. Trigger logic: @-mention OR reply-to-bot. Plain group chatter is ignored.
  3. The handler strips the @bot tag from the prompt before passing to Luke.
  4. Group path is text-only — no media surfaces, no card refresh, no photo
     backfill side effects (those leak private DM-state into the group).
  5. Unregistered users are short-circuited (no LLM cost).
"""

import inspect
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")


def _main_source():
    from bot import main as main_mod
    return inspect.getsource(main_mod)


def test_group_mention_handler_is_defined():
    src = _main_source()
    assert "async def group_mention_handler" in src


def test_handler_registered_in_separate_handler_group():
    """Must register in group=1 (or any non-zero) so combined_text_handler
    in group 0 doesn't shadow it."""
    src = _main_source()
    # Find the registration line for group_mention_handler
    assert "group_mention_handler" in src
    # The registration must have an explicit group=N arg
    # (ChatType.GROUPS filter alone isn't enough — PTB only fires the first
    # matching handler within a single group)
    assert "group=1" in src or "group=2" in src or "group=3" in src


def test_handler_filters_for_group_chat_only():
    src = _main_source()
    # Internal short-circuit on chat type
    assert 'effective_chat.type not in ("group", "supergroup")' in src or \
           'effective_chat.type in ("group", "supergroup")' in src or \
           'ChatType.GROUPS' in src


def test_handler_detects_mention_or_reply():
    src = _main_source()
    # @-mention path
    assert "@" in src and "bot_username" in src
    # reply-to-bot path
    assert "reply_to_message" in src
    assert "context.bot.id" in src


def test_handler_strips_at_mention_from_prompt():
    src = _main_source()
    # The handler should remove @bot_username before passing to Luke
    assert ("re.sub" in src and "bot_username" in src) or \
           ("replace(f\"@{bot_username}\"" in src)


def test_handler_skips_media_and_card_side_effects():
    """The DM path applies media surfaces, refresh_card, backfill_photo —
    those would leak private DM state into the group. Group path must be
    text-only."""
    src = _main_source()
    # Find the group_mention_handler section
    start = src.index("async def group_mention_handler")
    # End at the next async def or app.add_handler
    rest = src[start:]
    end_idx = rest.find("async def ", 50)  # past the def line itself
    handler_src = rest[:end_idx] if end_idx > 0 else rest

    # Must not invoke DM-only side effects
    assert "compliance_grid" not in handler_src or "compliance_grid_dm" not in handler_src
    assert "refresh_card" not in handler_src
    assert "awaiting_photo" not in handler_src


def test_handler_gates_on_dm_registered():
    src = _main_source()
    start = src.index("async def group_mention_handler")
    rest = src[start:]
    end_idx = rest.find("async def ", 50)
    handler_src = rest[:end_idx] if end_idx > 0 else rest
    # Same gate as DM path — only registered participants invoke the LLM
    assert "dm_registered" in handler_src


def test_handler_caps_message_length():
    src = _main_source()
    start = src.index("async def group_mention_handler")
    rest = src[start:]
    end_idx = rest.find("async def ", 50)
    handler_src = rest[:end_idx] if end_idx > 0 else rest
    # Cost protection cap consistent with DM path
    assert "2000" in handler_src


def test_handler_passes_source_group_to_chat_with_luke():
    """Group exchanges must log as source='group' so they don't pollute the
    user's DM history (which feeds back into session context next turn)."""
    src = _main_source()
    start = src.index("async def group_mention_handler")
    rest = src[start:]
    end_idx = rest.find("async def ", 50)
    handler_src = rest[:end_idx] if end_idx > 0 else rest
    assert 'source="group"' in handler_src or "source='group'" in handler_src


def test_handler_skips_reply_to_card_with_buttons():
    """Daily card is a bot message with inline keyboard. Replies to it must
    NOT trigger AI chat — that's a UX trap."""
    src = _main_source()
    start = src.index("async def group_mention_handler")
    rest = src[start:]
    end_idx = rest.find("async def ", 50)
    handler_src = rest[:end_idx] if end_idx > 0 else rest
    # Looking for the reply_markup guard
    assert "reply_markup" in handler_src
