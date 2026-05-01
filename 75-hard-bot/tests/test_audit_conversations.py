"""Smoke tests for the conversation audit script.

Pins the scoring contract so future regex changes don't silently
break the audit. Runs against an in-memory dict-of-rows — no real DB needed.
"""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

import json
import sys
from pathlib import Path

# Add scripts/ to import path (it's not a package).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_conversations import score_row, summarize


class TestScoreRow:
    def test_phantom_state_claim_no_tools(self):
        r = score_row("you're at 16/16 cups", None)
        assert r["phantom_state_claim"] is True
        assert r["phantom_state_class"] == "water"
        assert r["phantom_filter_hit"] is True

    def test_legitimate_state_with_tools(self):
        r = score_row("you're at 16/16 cups", json.dumps(["log_water_dm"]))
        assert r["phantom_state_claim"] is False
        assert r["phantom_filter_hit"] is False
        assert r["tools_count"] == 1

    def test_action_phrase_no_tool(self):
        r = score_row("logging now, give me a sec", None)
        assert r["action_phrase_no_tool"] is True
        # No state claim → phantom_state_claim should be False
        assert r["phantom_state_claim"] is False

    def test_clean_response(self):
        r = score_row("logged. nice work.", None)
        assert r["phantom_state_claim"] is False
        assert r["action_phrase_no_tool"] is False
        assert r["phantom_filter_hit"] is False

    def test_empty_response(self):
        r = score_row(None, None)
        assert r["phantom_state_claim"] is False
        assert r["action_phrase_no_tool"] is False

    def test_invalid_tools_json_handled(self):
        # Malformed tools_called shouldn't crash the audit.
        r = score_row("you're at 16/16 cups", "not-valid-json")
        # Treated as empty tools → phantom flagged
        assert r["phantom_state_claim"] is True


class TestSummarize:
    def test_summary_counts_and_ranking(self):
        rows = [
            # Suspect: phantom state claim, no tools
            {"id": 1, "user_message": "did i hit my goal?", "luke_response": "yeah, 170g goal hit",
             "tools_called": None, "user_name": "Alice", "timestamp": "2026-04-30 10:00"},
            # Legitimate: same claim, tool fired
            {"id": 2, "user_message": "log shake", "luke_response": "logged, 175g goal hit",
             "tools_called": json.dumps(["log_food"]), "user_name": "Alice",
             "timestamp": "2026-04-30 11:00"},
            # Suspect: action phrase no tool
            {"id": 3, "user_message": "log my run", "luke_response": "let me log that for you",
             "tools_called": None, "user_name": "Bob", "timestamp": "2026-04-30 12:00"},
            # Clean
            {"id": 4, "user_message": "thanks", "luke_response": "you got it",
             "tools_called": None, "user_name": "Bob", "timestamp": "2026-04-30 13:00"},
        ]

        summary = summarize(rows, top_n=10)

        assert summary["total_turns"] == 4
        assert summary["suspect_turns"] == 2  # ids 1 and 3
        assert summary["phantom_state_claims"] == 1
        assert summary["action_phrase_hits"] == 1

        # Top suspect should be the phantom-claim row (weighs more than action-phrase)
        assert summary["top_suspect"][0]["id"] == 1

        # Tool fire counts pick up legitimate row 2
        assert summary["tool_fires"]["log_food"] == 1
        assert summary["user_turns"]["Alice"] == 2
