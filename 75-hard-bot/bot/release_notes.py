"""Release notes — append-only log of deploys and what (if anything) to tell users.

Each entry is one deploy. The author of the deploy decides whether the change
is user-facing and what to say. Empty `user_facing` = silent (internal change,
nothing to announce).

Announcement triggers (in order of precedence):
  1. DEPLOY-TIME: maybe_announce_release runs ~5s after every bot startup.
     If there's pending content AND it's been > DEBOUNCE_MINUTES since the
     last announce, post + advance marker. Otherwise hold.
  2. MORNING CARD FALLBACK: morning_card_job at 7am ET also calls
     build_announcement and posts anything still pending. Catches the case
     where deploy-time was held by debounce and no further deploy happened.

The debounce batches a flurry of deploys into one announcement: if v54 is
announced at 6pm and v55 ships at 6:30pm, v55 is held; if v56 ships at 7pm,
both v55+v56 batch into the next allowed window.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# How long after a deploy-time announcement we suppress further announcements.
# Bryan: "we don't want to overwhelm the message if we're doing like five
# production releases in one day". 60 min = generous but not all-day silent.
RELEASE_ANNOUNCE_DEBOUNCE_MINUTES = 60

RELEASES: list[dict] = [
    {
        "version": 51,
        "user_facing": (
            "yo y'all quick update. log ain't lockin at noon no more. "
            "you got till midnight pacific to fix yesterday's stuff. "
            "forgot somethin? hit my dms i got you fr."
        ),
    },
    {
        "version": 52,
        "user_facing": "",  # Phoenix instrumentation — no behavior change for users
    },
    {
        "version": 53,
        "user_facing": "",  # Release-notes mechanism itself — meta, no user-facing change
    },
    {
        "version": 54,
        "user_facing": (
            "big drop today, here's what's new\n"
            "• penance system live. miss somethin yesterday? do it 2x today and stay in. "
            "just dm me ('i missed water yesterday' etc) and i'll set it up.\n"
            "• diet violations go to group vote. wine, cheat meals → squad decides pass, "
            "penance, or fail.\n"
            "• compliance grid. dm 'show my grid' to see your whole 75 days at once.\n"
            "• custom voice. dm 'talk to me like X' if cardi ain't your vibe."
        ),
    },
    {
        "version": 55,
        "user_facing": (
            "fix: midnight cutoff was creating penance for everyone whether you missed it or "
            "just forgot to log. that ain't right. now i wait for the 9am ET nudge to ask you "
            "first — did you do it, or miss it? you tell me, i log it accordingly. tonight's "
            "phantom penances rolled back."
        ),
    },
]

CURRENT_VERSION: int = max(r["version"] for r in RELEASES)


def build_announcement(last_seen: int | None, releases: list[dict] | None = None) -> str | None:
    """Return the message to post, or None if nothing to announce.

    `last_seen` is the highest version the group has already been told about
    (None means they've never been told about anything). Walks forward, picks
    up every version > last_seen with a non-empty `user_facing` string.

    Format: when there's exactly ONE note, return its text verbatim (single-update
    case looks weird with a list header). When 2+, prepend a brief header and
    concatenate each note as its own block. Each release author is responsible
    for bulleting sub-points within their own note (Bryan's feedback: free-text
    multi-update paragraphs make people miss items).
    """
    rels = releases if releases is not None else RELEASES
    threshold = last_seen if last_seen is not None else -1
    notes = [
        r["user_facing"]
        for r in sorted(rels, key=lambda r: r["version"])
        if r["version"] > threshold and r["user_facing"]
    ]
    if not notes:
        return None
    if len(notes) == 1:
        return notes[0]
    # Multiple notes: header + each note as its own block. Two newlines between
    # blocks for visual breathing room.
    return "yo couple updates fr\n\n" + "\n\n".join(notes)


def _is_within_debounce(last_announce_iso: str | None, now: datetime | None = None) -> bool:
    """True if a previous announcement landed within the debounce window.

    Pure helper — no DB I/O — so it's trivially testable. now defaulted to
    real UTC clock; tests pass an explicit datetime.
    """
    if not last_announce_iso:
        return False
    try:
        last_at = datetime.fromisoformat(last_announce_iso)
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - last_at) < timedelta(minutes=RELEASE_ANNOUNCE_DEBOUNCE_MINUTES)


async def maybe_announce_release(application) -> bool:
    """Deploy-time release announcement with debounce.

    Returns True iff an announcement was posted this call. False otherwise
    (no pending content, debounce active, no group chat configured, or
    send failed). Side effects on success: post to the group, advance the
    last_announced_release_version marker, write last_release_announce_at.
    """
    db = application.bot_data.get("db")
    chat_id = application.bot_data.get("group_chat_id")
    if db is None or not chat_id:
        return False

    raw_marker = await db.get_setting("last_announced_release_version")
    last_seen = int(raw_marker) if raw_marker is not None else None
    msg = build_announcement(last_seen)
    if not msg:
        return False  # nothing to say

    last_at_iso = await db.get_setting("last_release_announce_at")
    if _is_within_debounce(last_at_iso):
        logger.info(
            "release-notes: deploy-time send debounced (last announced %s, "
            "window %d min). morning card will pick it up if no later deploy.",
            last_at_iso, RELEASE_ANNOUNCE_DEBOUNCE_MINUTES,
        )
        return False

    try:
        await application.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.warning("release-notes: deploy-time announcement send failed (%s)", e)
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    await db.set_setting("last_announced_release_version", str(CURRENT_VERSION))
    await db.set_setting("last_release_announce_at", now_iso)
    logger.info(
        "release-notes: deploy-time announcement posted, marker → v%d at %s",
        CURRENT_VERSION, now_iso,
    )
    return True
