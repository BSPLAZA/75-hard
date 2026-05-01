"""Release notes — append-only log of deploys and what (if anything) to tell users.

Each entry is one deploy. The author of the deploy decides whether the change
is user-facing and what to say. Empty `user_facing` = silent (internal change,
nothing to announce).

The morning card job reads `CURRENT_VERSION` against the `last_announced_release_version`
setting in `bot_settings` and posts any unseen non-empty notes to the group chat.
"""

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
]

CURRENT_VERSION: int = max(r["version"] for r in RELEASES)


def build_announcement(last_seen: int | None, releases: list[dict] | None = None) -> str | None:
    """Return the message to post, or None if nothing to announce.

    `last_seen` is the highest version the group has already been told about
    (None means they've never been told about anything). Walks forward, picks
    up every version > last_seen with a non-empty `user_facing` string, and
    joins them with blank lines.
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
    return "\n\n".join(notes)
