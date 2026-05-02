"""Tests for handle_new_group idempotency.

Pinned contracts:
  1. First my_chat_member event for a chat_id sends welcome + FAQ + creates invite.
  2. Subsequent events for the SAME chat_id (role change, supergroup migration,
     etc.) DO NOT re-send. Stays idempotent via bot_settings welcomed_chat:<id>.
  3. bot_data['group_chat_id'] still updates on every event so subsequent sends
     target the right chat (especially after supergroup migration where the
     chat_id changes).
"""

import inspect
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")
os.environ.setdefault("ADMIN_USER_ID", "999")

import pytest
import pytest_asyncio
from types import SimpleNamespace

from bot.database import Database
from bot import main as main_mod


def test_handler_source_uses_idempotency_key():
    """Source-level: handle_new_group must check + set a per-chat welcomed flag."""
    src = inspect.getsource(main_mod.handle_new_group)
    assert "welcomed_chat:" in src, "must store a per-chat welcomed flag"
    assert "already_welcomed" in src or "get_setting" in src
    assert "set_setting" in src


class _StubBot:
    def __init__(self, bot_id=999, username="lukebot"):
        self.id = bot_id
        self.username = username
        self.sent: list[dict] = []
        self.pinned: list[dict] = []
        self.invites_created = 0

    async def send_message(self, *, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=len(self.sent))

    async def pin_chat_message(self, *, chat_id, message_id, **kwargs):
        self.pinned.append({"chat_id": chat_id, "message_id": message_id})

    async def create_chat_invite_link(self, *, chat_id, name=None, **kwargs):
        self.invites_created += 1
        return SimpleNamespace(invite_link=f"https://t.me/+invite{self.invites_created}")

    async def get_me(self):
        return SimpleNamespace(username=self.username)


def _update(chat_id, status="member"):
    return SimpleNamespace(
        my_chat_member=SimpleNamespace(
            new_chat_member=SimpleNamespace(status=status),
            chat=SimpleNamespace(id=chat_id, title="75Hard"),
        ),
    )


@pytest_asyncio.fixture
async def db():
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_first_event_sends_welcome_and_faq(db):
    bot = _StubBot()
    ctx = SimpleNamespace(bot=bot, bot_data={"db": db})
    await main_mod.handle_new_group(_update(-100123), ctx)

    # Welcome + FAQ both sent (2 messages)
    assert len(bot.sent) == 2
    assert bot.invites_created == 1
    assert ctx.bot_data["group_chat_id"] == -100123
    assert ctx.bot_data["group_invite_link"] is not None
    # Flag stored
    assert (await db.get_setting("welcomed_chat:-100123")) == "1"


@pytest.mark.asyncio
async def test_second_event_for_same_chat_skips_send(db):
    """The bug Bryan saw: bot was welcomed once, then promoted/migrated, and
    welcome fired again. Test that doesn't happen."""
    bot = _StubBot()
    ctx = SimpleNamespace(bot=bot, bot_data={"db": db})
    await main_mod.handle_new_group(_update(-100123), ctx)
    initial_sent = len(bot.sent)
    initial_invites = bot.invites_created

    # Second event — e.g., role promoted to admin, or supergroup migration
    await main_mod.handle_new_group(_update(-100123, status="administrator"), ctx)

    assert len(bot.sent) == initial_sent, "must not re-send welcome/FAQ"
    assert bot.invites_created == initial_invites, "must not re-create invite link"
    # bot_data still updated so future sends target the right chat
    assert ctx.bot_data["group_chat_id"] == -100123


@pytest.mark.asyncio
async def test_different_chat_id_still_welcomes(db):
    """Each distinct chat_id gets its own welcome (e.g., legitimate re-add to
    a different group)."""
    bot = _StubBot()
    ctx = SimpleNamespace(bot=bot, bot_data={"db": db})
    await main_mod.handle_new_group(_update(-100123), ctx)
    sent_after_first = len(bot.sent)

    # Different chat_id
    await main_mod.handle_new_group(_update(-100456), ctx)
    assert len(bot.sent) > sent_after_first
    # Both flags stored
    assert (await db.get_setting("welcomed_chat:-100123")) == "1"
    assert (await db.get_setting("welcomed_chat:-100456")) == "1"


@pytest.mark.asyncio
async def test_left_status_does_not_reset_flag(db):
    """If the bot is removed from a group (status=left) and re-added, this
    test currently doesn't reset the flag — re-adding same chat_id won't
    re-welcome. That's a deliberate trade-off: re-adds should be rare and
    a duplicate welcome is louder than a silent re-add."""
    bot = _StubBot()
    ctx = SimpleNamespace(bot=bot, bot_data={"db": db})
    await main_mod.handle_new_group(_update(-100123), ctx)
    initial_sent = len(bot.sent)

    # Status: left (handler ignores — only fires on member/administrator)
    await main_mod.handle_new_group(_update(-100123, status="left"), ctx)
    assert len(bot.sent) == initial_sent

    # Re-add (member again) — flag still set, so no re-welcome
    await main_mod.handle_new_group(_update(-100123, status="member"), ctx)
    assert len(bot.sent) == initial_sent
