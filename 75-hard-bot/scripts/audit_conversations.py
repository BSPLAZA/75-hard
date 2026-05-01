"""Read-only audit of conversation_log to surface "weird" Luke turns.

Born from the V3b group meeting: the operator literally asked us to
"audit the conversations and see where things have been going wrong."
This is the smallest possible vertical slice of what Phoenix would do
at scale — pull rows, score them with the existing phantom regex +
empty-tool heuristic, and rank the most suspicious ones.

USAGE:
    python -m scripts.audit_conversations
    python -m scripts.audit_conversations --days 14 --top 30
    python -m scripts.audit_conversations --db data/75hard.db --json

OUTPUT: human-readable summary to stdout. With --json, structured JSON
ready to pipe into a Phoenix dataset uploader.

DESIGN: read-only. Never writes to the DB. Reuses Luke's own regex
helpers (luke_chat._check_state_claims, _looks_like_phantom_row) so the
audit's notion of "phantom" matches what the live bot guards against.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# Module load needs these env vars set; the audit doesn't actually call any APIs.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

# Path manipulation so `python scripts/audit_conversations.py` works as well
# as `python -m scripts.audit_conversations`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.utils.luke_chat import (  # noqa: E402
    _PHANTOM_TEXT_PATTERNS,
    _check_state_claims,
    _looks_like_phantom_row,
)


# Action-phrase list mirrors luke_chat's variant-A phantom detector. We don't
# import it (it's an inline tuple in chat_with_luke); duplicate here for now and
# fold up later if Bryan extracts it to module scope.
ACTION_PHRASES = (
    "logging now", "let me log", "let me actually log", "logging it",
    "logging everything", "let me push", "logging all",
    "give me a sec", "hold up", "hold on, let me", "give me one sec",
)


def score_row(luke_response: str | None, tools_called: str | None) -> dict:
    """Compute audit features for one conversation_log row."""
    if not luke_response:
        return {
            "phantom_state_claim": False,
            "action_phrase_no_tool": False,
            "phantom_filter_hit": False,
            "tools_count": 0,
        }

    tools_list: list[str] = []
    if tools_called:
        try:
            parsed = json.loads(tools_called)
            if isinstance(parsed, list):
                tools_list = [str(t) for t in parsed]
        except json.JSONDecodeError:
            pass

    state_claim = _check_state_claims(luke_response, tools_list)
    text_lower = luke_response.lower()
    action_phrase = (
        not tools_list
        and any(p in text_lower for p in ACTION_PHRASES)
    )

    return {
        "phantom_state_claim": state_claim is not None,
        "phantom_state_class": state_claim[0] if state_claim else None,
        "phantom_state_snippet": state_claim[1] if state_claim else None,
        "action_phrase_no_tool": action_phrase,
        "phantom_filter_hit": _looks_like_phantom_row(luke_response, tools_called),
        "tools_count": len(tools_list),
        "tools_list": tools_list,
    }


def fetch_rows(db_path: str, days: int) -> list[dict]:
    """Pull conversation_log rows newer than `days` ago. Read-only connection."""
    db_path = str(Path(db_path).expanduser().resolve())
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    # mode=ro guarantees the audit cannot mutate the DB even by accident.
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, timestamp, telegram_id, user_name, source, "
            "user_message, luke_response, tools_called "
            "FROM conversation_log "
            "WHERE timestamp >= ? "
            "ORDER BY id ASC",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def summarize(rows: list[dict], top_n: int) -> dict:
    """Aggregate audit features across the windowed corpus."""
    enriched = []
    tool_fires = Counter()
    user_turns = Counter()
    by_source = Counter()

    for r in rows:
        score = score_row(r.get("luke_response"), r.get("tools_called"))
        enriched.append({**r, **score})
        for t in score["tools_list"]:
            tool_fires[t] += 1
        if r.get("user_name"):
            user_turns[r["user_name"]] += 1
        by_source[r.get("source") or "(unknown)"] += 1

    suspect = [
        e for e in enriched
        if e["phantom_state_claim"] or e["action_phrase_no_tool"] or e["phantom_filter_hit"]
    ]
    # Rank: phantom_state_claim weighs more (a hard hit) than action_phrase
    # (which can have false positives on questions).
    def weight(e: dict) -> int:
        return (
            (3 if e["phantom_state_claim"] else 0)
            + (1 if e["action_phrase_no_tool"] else 0)
            + (1 if e["phantom_filter_hit"] else 0)
        )
    suspect.sort(key=weight, reverse=True)

    return {
        "total_turns": len(enriched),
        "suspect_turns": len(suspect),
        "phantom_state_claims": sum(1 for e in enriched if e["phantom_state_claim"]),
        "action_phrase_hits": sum(1 for e in enriched if e["action_phrase_no_tool"]),
        "phantom_filter_hits": sum(1 for e in enriched if e["phantom_filter_hit"]),
        "tool_fires": dict(tool_fires.most_common()),
        "user_turns": dict(user_turns.most_common()),
        "by_source": dict(by_source.most_common()),
        "top_suspect": suspect[:top_n],
    }


def render_text(summary: dict) -> str:
    """Format the summary for human reading."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("LUKE CONVERSATION AUDIT")
    lines.append("=" * 60)
    lines.append(f"Total turns:         {summary['total_turns']}")
    if summary.get("by_source"):
        parts = ", ".join(f"{src}={n}" for src, n in summary["by_source"].items())
        lines.append(f"  by source:         {parts}")
    lines.append(f"Suspect turns:       {summary['suspect_turns']}")
    lines.append(f"  phantom claims:    {summary['phantom_state_claims']}")
    lines.append(f"  action-no-tool:    {summary['action_phrase_hits']}")
    lines.append(f"  phantom-filter:    {summary['phantom_filter_hits']}")

    if summary['total_turns']:
        rate = 100.0 * summary['suspect_turns'] / summary['total_turns']
        lines.append(f"Suspect rate:        {rate:.1f}%")

    lines.append("")
    lines.append("Tool fires (top):")
    for tool, n in list(summary["tool_fires"].items())[:15]:
        lines.append(f"  {n:5d}  {tool}")

    lines.append("")
    lines.append("User volume (top):")
    for user, n in list(summary["user_turns"].items())[:10]:
        lines.append(f"  {n:5d}  {user}")

    lines.append("")
    lines.append(f"Top {len(summary['top_suspect'])} suspect turns (highest weight first):")
    lines.append("-" * 60)
    for i, e in enumerate(summary["top_suspect"], 1):
        markers = []
        if e["phantom_state_claim"]:
            markers.append(f"PHANTOM-CLAIM[{e['phantom_state_class']}]")
        if e["action_phrase_no_tool"]:
            markers.append("ACTION-PHRASE")
        if e["phantom_filter_hit"]:
            markers.append("FILTER-HIT")
        lines.append(
            f"\n#{i}  id={e['id']}  {e.get('timestamp', '?')}  "
            f"user={e.get('user_name') or '?'}  [{' / '.join(markers)}]"
        )
        umsg = (e.get("user_message") or "")[:200].replace("\n", " ")
        lresp = (e.get("luke_response") or "")[:200].replace("\n", " ")
        tools = e.get("tools_called") or "(no tools)"
        lines.append(f"  USER: {umsg}")
        lines.append(f"  LUKE: {lresp}")
        lines.append(f"  TOOLS: {tools}")
        if e["phantom_state_snippet"]:
            lines.append(f"  SNIPPET: {e['phantom_state_snippet']!r}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Audit Luke's conversation log for suspect turns.")
    p.add_argument("--db", default=os.environ.get("DATABASE_PATH", "data/75hard.db"),
                   help="Path to SQLite db (default: data/75hard.db or $DATABASE_PATH)")
    p.add_argument("--days", type=int, default=7, help="Window in days (default: 7)")
    p.add_argument("--top", type=int, default=20, help="Top-N suspect turns to print (default: 20)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = p.parse_args(argv)

    rows = fetch_rows(args.db, args.days)
    summary = summarize(rows, args.top)

    if args.json:
        print(json.dumps(summary, default=str, indent=2))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
