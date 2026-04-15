"""Tests for the agent daemon and triage modules."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from bot.database import Database
from agent.triage import triage_feedback, generate_patch
from agent import daemon


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """Provide an in-memory database for each test."""
    database = Database(":memory:")
    await database.init()
    yield database
    await database.close()


def _make_feedback_item(**overrides) -> dict:
    """Create a sample feedback item dict."""
    base = {
        "id": 1,
        "telegram_id": 111,
        "type": "bug",
        "text": "The water button doesn't work after 10 cups",
        "context": "day 5",
        "status": "new",
        "created_at": "2026-04-20T10:00:00",
    }
    base.update(overrides)
    return base


# ── triage_feedback tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_triage_no_api_key():
    """Without an API key, triage returns a default result."""
    item = _make_feedback_item()
    with patch("agent.triage.ANTHROPIC_API_KEY", ""):
        result = await triage_feedback(item)

    assert result["severity"] == "important"
    assert result["action"] == "defer"
    assert "No API key" in result["summary"]
    assert isinstance(result["files_to_change"], list)


@pytest.mark.asyncio
async def test_triage_successful_classification():
    """With a valid API response, triage parses the JSON correctly."""
    mock_response = {
        "severity": "critical",
        "action": "fix_code",
        "summary": "Water tracking breaks after 10 cups",
        "recommendation": "Fix the cap in water handler",
        "files_to_change": ["bot/handlers/water.py"],
    }

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=json.dumps(mock_response))]
    mock_client.messages.create.return_value = mock_message

    item = _make_feedback_item()

    with patch("agent.triage.ANTHROPIC_API_KEY", "test-key"), \
         patch("agent.triage.anthropic.Anthropic", return_value=mock_client):
        result = await triage_feedback(item)

    assert result["severity"] == "critical"
    assert result["action"] == "fix_code"
    assert "Water" in result["summary"]
    assert result["files_to_change"] == ["bot/handlers/water.py"]


@pytest.mark.asyncio
async def test_triage_handles_bad_json():
    """If Claude returns non-JSON, triage returns a fallback."""
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Sorry, I can't help with that.")]
    mock_client.messages.create.return_value = mock_message

    item = _make_feedback_item()

    with patch("agent.triage.ANTHROPIC_API_KEY", "test-key"), \
         patch("agent.triage.anthropic.Anthropic", return_value=mock_client):
        result = await triage_feedback(item)

    assert result["severity"] == "important"
    assert result["action"] == "defer"
    assert "Parse error" in result["summary"]


@pytest.mark.asyncio
async def test_triage_handles_api_error():
    """If the API call raises, triage returns a fallback."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API down")

    item = _make_feedback_item()

    with patch("agent.triage.ANTHROPIC_API_KEY", "test-key"), \
         patch("agent.triage.anthropic.Anthropic", return_value=mock_client):
        result = await triage_feedback(item)

    assert result["severity"] == "important"
    assert result["action"] == "defer"
    assert "Error" in result["summary"]


@pytest.mark.asyncio
async def test_triage_fills_missing_keys():
    """If Claude omits keys, triage fills them with defaults."""
    partial_response = {
        "severity": "nice-to-have",
        "action": "defer",
    }

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=json.dumps(partial_response))]
    mock_client.messages.create.return_value = mock_message

    item = _make_feedback_item()

    with patch("agent.triage.ANTHROPIC_API_KEY", "test-key"), \
         patch("agent.triage.anthropic.Anthropic", return_value=mock_client):
        result = await triage_feedback(item)

    assert result["severity"] == "nice-to-have"
    assert result["action"] == "defer"
    assert "summary" in result
    assert "recommendation" in result
    assert isinstance(result["files_to_change"], list)


# ── generate_patch tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_patch_no_api_key():
    """Without an API key, generate_patch returns None."""
    item = _make_feedback_item()
    triage = {"summary": "test", "recommendation": "fix it", "files_to_change": []}

    with patch("agent.triage.ANTHROPIC_API_KEY", ""):
        result = await generate_patch(item, triage)

    assert result is None


@pytest.mark.asyncio
async def test_generate_patch_returns_content():
    """With a valid API response, generate_patch returns the patch text."""
    patch_text = "--- a/bot/handlers/water.py\n+++ b/bot/handlers/water.py\n@@ -1 +1 @@\n-old\n+new"

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=patch_text)]
    mock_client.messages.create.return_value = mock_message

    item = _make_feedback_item()
    triage = {
        "summary": "Water tracking bug",
        "recommendation": "Fix the cap",
        "files_to_change": ["bot/handlers/water.py"],
    }

    with patch("agent.triage.ANTHROPIC_API_KEY", "test-key"), \
         patch("agent.triage.anthropic.Anthropic", return_value=mock_client):
        result = await generate_patch(item, triage)

    assert result is not None
    assert "water.py" in result


# ── daemon.send_triage_report tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_send_triage_report_formats_message():
    """Verify the triage report includes key fields."""
    item = _make_feedback_item()
    triage = {
        "severity": "critical",
        "action": "fix_code",
        "summary": "Water button broken",
        "recommendation": "Fix the handler",
        "files_to_change": ["bot/handlers/water.py"],
    }

    with patch.object(daemon, "send_telegram", new_callable=AsyncMock, return_value=42) as mock_send:
        msg_id = await daemon.send_triage_report(triage, item)

    assert msg_id == 42
    sent_text = mock_send.call_args[0][0]
    assert "bug" in sent_text
    assert "critical" in sent_text
    assert "fix_code" in sent_text
    assert "water.py" in sent_text
    assert "approve" in sent_text.lower()


@pytest.mark.asyncio
async def test_send_triage_report_no_approve_for_defer():
    """Non-fix_code actions should not prompt for approval."""
    item = _make_feedback_item(type="suggest")
    triage = {
        "severity": "nice-to-have",
        "action": "defer",
        "summary": "Feature request",
        "recommendation": "Consider later",
        "files_to_change": [],
    }

    with patch.object(daemon, "send_telegram", new_callable=AsyncMock, return_value=43) as mock_send:
        await daemon.send_triage_report(triage, item)

    sent_text = mock_send.call_args[0][0]
    assert "approve" not in sent_text.lower()


# ── daemon.poll_feedback tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_feedback_triages_and_acknowledges(db):
    """poll_feedback should triage new items and mark them acknowledged."""
    # Insert a feedback item
    fb_id = await db.add_feedback(111, "bug", "Something broke", "day 3")

    mock_triage = {
        "severity": "important",
        "action": "respond_to_user",
        "summary": "User confused",
        "recommendation": "Reply to user",
        "files_to_change": [],
    }

    with patch("agent.daemon.triage_feedback", new_callable=AsyncMock, return_value=mock_triage), \
         patch("agent.daemon.send_triage_report", new_callable=AsyncMock, return_value=99):
        await daemon.poll_feedback(db)

    # Verify the item is now acknowledged
    remaining = await db.get_feedback(status="new")
    assert len(remaining) == 0

    acknowledged = await db.get_feedback(status="acknowledged")
    assert len(acknowledged) == 1
    assert dict(acknowledged[0])["id"] == fb_id


@pytest.mark.asyncio
async def test_poll_feedback_empty_is_noop(db):
    """poll_feedback should do nothing when there are no new items."""
    with patch("agent.daemon.triage_feedback", new_callable=AsyncMock) as mock_triage:
        await daemon.poll_feedback(db)

    mock_triage.assert_not_called()


@pytest.mark.asyncio
async def test_poll_feedback_tracks_fix_code_approvals(db):
    """fix_code items should be tracked for approval."""
    await db.add_feedback(111, "bug", "Crash on photo upload", "day 7")

    mock_triage = {
        "severity": "critical",
        "action": "fix_code",
        "summary": "Photo crash",
        "recommendation": "Fix the handler",
        "files_to_change": ["bot/handlers/photo.py"],
    }

    # Clear any prior state
    daemon._pending_approvals.clear()

    with patch("agent.daemon.triage_feedback", new_callable=AsyncMock, return_value=mock_triage), \
         patch("agent.daemon.send_triage_report", new_callable=AsyncMock, return_value=55):
        await daemon.poll_feedback(db)

    assert 55 in daemon._pending_approvals
    item, triage = daemon._pending_approvals[55]
    assert triage["action"] == "fix_code"

    # Clean up
    daemon._pending_approvals.clear()


# ── daemon.handle_approval tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_approval_saves_patch(tmp_path):
    """handle_approval should generate and save a patch file."""
    item = _make_feedback_item(id=42)
    triage = {
        "severity": "critical",
        "action": "fix_code",
        "summary": "Water bug",
        "recommendation": "Fix it",
        "files_to_change": ["bot/handlers/water.py"],
    }
    patch_content = "--- a/bot/handlers/water.py\n+++ b/bot/handlers/water.py\n"

    with patch("agent.daemon.generate_patch", new_callable=AsyncMock, return_value=patch_content), \
         patch("agent.daemon.send_telegram", new_callable=AsyncMock) as mock_send, \
         patch("agent.daemon.PATCHES_DIR", tmp_path):
        await daemon.handle_approval(item, triage)

    # Verify patch file was created
    patch_file = tmp_path / "fix_42.patch"
    assert patch_file.exists()
    assert patch_file.read_text() == patch_content

    # Verify success message was sent
    calls = mock_send.call_args_list
    assert len(calls) == 2  # "Generating..." + "Patch ready..."
    assert "fix_42.patch" in calls[1][0][0]


@pytest.mark.asyncio
async def test_handle_approval_reports_failure():
    """handle_approval should report when patch generation fails."""
    item = _make_feedback_item(id=99)
    triage = {
        "severity": "important",
        "action": "fix_code",
        "summary": "Something",
        "recommendation": "Fix",
        "files_to_change": [],
    }

    with patch("agent.daemon.generate_patch", new_callable=AsyncMock, return_value=None), \
         patch("agent.daemon.send_telegram", new_callable=AsyncMock) as mock_send:
        await daemon.handle_approval(item, triage)

    calls = mock_send.call_args_list
    assert len(calls) == 2
    assert "Failed" in calls[1][0][0]
