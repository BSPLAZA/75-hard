"""Always-on agent daemon that triages feedback and reports to Bryan."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent dir so we can import bot modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from bot.database import Database
from bot.config import ADMIN_USER_ID, DATABASE_PATH, BOT_TOKEN
from agent.triage import triage_feedback, generate_patch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent.daemon")

POLL_INTERVAL = 300  # 5 minutes
PATCHES_DIR = Path(__file__).parent / "patches"

# Map feedback item IDs to their triage results so we can act on Bryan's replies
_pending_approvals: dict[int, tuple[dict, dict]] = {}  # msg_id -> (item, triage)


# ── Telegram helpers ──────────────────────────────────────────────────


async def send_telegram(text: str, *, reply_markup: dict | None = None) -> int | None:
    """Send a message to Bryan via the Telegram Bot API.

    Returns the message_id of the sent message, or None on failure.
    """
    if not BOT_TOKEN:
        logger.warning("No BOT_TOKEN set -- skipping Telegram send.")
        return None

    payload: dict = {
        "chat_id": ADMIN_USER_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("message_id")
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return None


async def get_updates(offset: int | None = None) -> list[dict]:
    """Poll for new Telegram updates (messages to the bot).

    Returns a list of update dicts.
    """
    if not BOT_TOKEN:
        return []

    params: dict = {"timeout": 5, "allowed_updates": '["message"]'}
    if offset is not None:
        params["offset"] = offset

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params=params,
            )
            resp.raise_for_status()
            return resp.json().get("result", [])
    except Exception as e:
        logger.error("Failed to get updates: %s", e)
        return []


# ── Triage reporting ──────────────────────────────────────────────────


SEVERITY_EMOJI = {
    "critical": "\U0001f534",    # red circle
    "important": "\U0001f7e0",   # orange circle
    "nice-to-have": "\U0001f7e2",  # green circle
}


async def send_triage_report(triage: dict, item: dict) -> int | None:
    """Send a triage report to Bryan via Telegram. Returns message_id."""
    emoji = SEVERITY_EMOJI.get(triage["severity"], "\u2753")

    parts = [
        f"{emoji} <b>New {item['type']}</b> (#{item['id']})",
        "",
        f"<i>\"{item['text']}\"</i>",
        f"Context: {item.get('context', 'none')}",
        "",
        f"<b>Severity:</b> {triage['severity']}",
        f"<b>Action:</b> {triage['action']}",
        f"<b>Summary:</b> {triage['summary']}",
        f"<b>Recommendation:</b> {triage['recommendation']}",
    ]

    if triage.get("files_to_change"):
        parts.append(f"<b>Files:</b> {', '.join(triage['files_to_change'])}")

    if triage["action"] == "fix_code":
        parts.append("")
        parts.append('Reply "approve" or "do it" to generate a patch.')

    text = "\n".join(parts)
    return await send_telegram(text)


# ── Patch generation ──────────────────────────────────────────────────


async def handle_approval(item: dict, triage: dict) -> None:
    """Generate a patch and report back to Bryan."""
    await send_telegram(f"\U0001f527 Generating patch for #{item['id']}...")

    patch_content = await generate_patch(item, triage)

    if not patch_content:
        await send_telegram(f"\u274c Failed to generate patch for #{item['id']}.")
        return

    # Save the patch
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    patch_path = PATCHES_DIR / f"fix_{item['id']}.patch"
    patch_path.write_text(patch_content)

    await send_telegram(
        f"\u2705 Patch ready for #{item['id']}:\n"
        f"<code>agent/patches/fix_{item['id']}.patch</code>\n\n"
        f"Review and apply:\n"
        f"<code>git apply agent/patches/fix_{item['id']}.patch</code>"
    )


# ── Main loop ─────────────────────────────────────────────────────────


async def poll_feedback(db: Database) -> None:
    """Check for new feedback and triage it."""
    try:
        new_items = await db.get_feedback(status="new")
    except Exception as e:
        logger.error("Failed to query feedback: %s", e)
        return

    if not new_items:
        return

    logger.info("Found %d new feedback item(s).", len(new_items))

    for row in new_items:
        item = dict(row)
        logger.info("Triaging feedback #%d: %s", item["id"], item["text"][:60])

        triage = await triage_feedback(item)
        msg_id = await send_triage_report(triage, item)

        # Track for approval if it's a code fix
        if msg_id and triage["action"] == "fix_code":
            _pending_approvals[msg_id] = (item, triage)

        # Mark as acknowledged
        try:
            await db.resolve_feedback(item["id"], status="acknowledged")
        except Exception as e:
            logger.error("Failed to mark feedback #%d as acknowledged: %s", item["id"], e)


async def poll_replies() -> None:
    """Check for Bryan's replies to triage reports."""
    if not _pending_approvals:
        return

    # We don't persist update_offset across restarts, so on first boot we
    # skip old messages by using offset=-1, then track from there.
    # For simplicity we use a module-level variable.
    global _update_offset

    updates = await get_updates(offset=_update_offset)

    for update in updates:
        _update_offset = update["update_id"] + 1

        message = update.get("message", {})
        # Only listen to Bryan
        if message.get("from", {}).get("id") != ADMIN_USER_ID:
            continue

        reply_to = message.get("reply_to_message", {})
        reply_msg_id = reply_to.get("message_id")

        if reply_msg_id and reply_msg_id in _pending_approvals:
            text = message.get("text", "").strip().lower()
            if text in ("approve", "do it", "yes", "go", "fix it"):
                item, triage = _pending_approvals.pop(reply_msg_id)
                await handle_approval(item, triage)


_update_offset: int | None = None


async def main() -> None:
    """Main daemon loop."""
    db = Database(DATABASE_PATH)
    await db.init()

    logger.info(
        "Agent daemon started. Polling every %ds. Admin ID: %s",
        POLL_INTERVAL,
        ADMIN_USER_ID,
    )

    # Skip any old updates on startup
    global _update_offset
    updates = await get_updates(offset=-1)
    if updates:
        _update_offset = updates[-1]["update_id"] + 1

    try:
        while True:
            await poll_feedback(db)
            await poll_replies()
            await asyncio.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")
    finally:
        await db.close()
        logger.info("Database closed. Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
