"""Group-arbitration poll wiring.

When Luke calls log_violation, a poll posts to the group with options
[pass, penance, fail]. Each squad member's vote is recorded in
arbitration_votes. The organizer renders final verdict via /admin_arbitrate.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, PollAnswerHandler

logger = logging.getLogger(__name__)

_OPTIONS = ["pass", "penance", "fail"]


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Persist an arbitration vote (or its retraction).

    Telegram's PollAnswer fires for ANY poll the bot tracks — we filter to
    only those with a matching penance row. Empty option_ids = vote retracted.
    """
    poll_answer = update.poll_answer
    if poll_answer is None:
        return
    db = context.bot_data.get("db")
    if db is None:
        return

    poll_id = str(poll_answer.poll_id)
    row = await db.get_penance_by_poll_id(poll_id)
    if row is None:
        # Not an arbitration poll (could be the group-call scheduling poll, etc.)
        return

    penance_id = dict(row)["id"]
    voter_id = poll_answer.user.id
    option_ids = list(poll_answer.option_ids or [])
    if not option_ids:
        choice = ""  # retraction → DELETE
    else:
        idx = option_ids[0]
        if 0 <= idx < len(_OPTIONS):
            choice = _OPTIONS[idx]
        else:
            logger.warning("arbitration: invalid option_id %d for poll %s", idx, poll_id)
            return

    try:
        await db.record_arbitration_vote(penance_id, voter_id, choice)
    except Exception as e:
        logger.warning("arbitration: failed to record vote (poll=%s voter=%d): %s", poll_id, voter_id, e)


def get_arbitration_poll_handler() -> PollAnswerHandler:
    return PollAnswerHandler(handle_poll_answer)
