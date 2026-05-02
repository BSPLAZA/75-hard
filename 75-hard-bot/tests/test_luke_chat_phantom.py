"""Regression tests for the v50 phantom-action defenses in luke_chat.

The v46 phrase detector caught lead-in narration ("logging now") with no tool
call. Claude evolved past it by skipping narration and emitting hallucinated
totals directly. v50 adds a state-claim detector independent of phrasing.
"""

import os

# Module load needs these env vars set
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("GROUP_CHAT_ID", "-1")

from bot.utils.luke_chat import (
    _check_state_claims,
    _looks_like_phantom_row,
)


class TestStateClaimDetector:
    """The output validator catches numerical state claims without a backing tool."""

    def test_diet_total_no_tool_is_phantom(self):
        # Real production case: Bryan Apr 22, "32 grams soy milk" → "135g, 35 to go"
        # but tools_called was empty.
        assert _check_state_claims("135g, 35 to go", []) is not None

    def test_diet_total_with_log_food_is_ok(self):
        assert _check_state_claims("135g, 35 to go", ["log_food"]) is None

    def test_diet_total_with_progress_read_is_ok(self):
        # Luke can quote totals from get_diet_progress without logging
        assert _check_state_claims("135g, 35 to go", ["get_diet_progress"]) is None

    def test_goal_hit_no_tool_is_phantom(self):
        # Real production case: "170g, goal hit. 🎯" but no log_food fired
        assert _check_state_claims("goal hit", []) is not None

    def test_day_at_g_format_is_phantom(self):
        # Real production case: "day 8 at 75g, 95 to go"
        assert _check_state_claims("day 8 at 75g, 95 to go", []) is not None

    def test_water_count_no_tool_is_phantom(self):
        # Bare cup count without a water tool
        assert _check_state_claims("15/16", []) is not None

    def test_water_count_with_log_water_is_ok(self):
        assert _check_state_claims("15/16", ["log_water_dm"]) is None

    def test_water_count_with_status_check_is_ok(self):
        # get_my_status returns water count in its payload
        assert _check_state_claims("15/16", ["get_my_status"]) is None

    def test_neutral_acknowledgment_is_ok(self):
        # "logged. nice job." — no number claimed, no phantom
        assert _check_state_claims("logged. nice job.", []) is None

    def test_question_with_cup_word_is_ok(self):
        # "one cup of water?" — clarifying question, not a state claim
        assert _check_state_claims("one cup of water?", []) is None

    def test_youre_at_phantom(self):
        # Variant phrasing: "youre at 16/16"
        assert _check_state_claims("youre at 16/16", []) is not None

    def test_classify_water_vs_diet(self):
        # Water claim should require a water tool, not a diet tool
        # Diet claim should require a diet tool, not a water tool
        diet_text = "135g, 35 to go"
        water_text = "15/16 cups"
        # Diet text + water tool only → still phantom
        assert _check_state_claims(diet_text, ["log_water_dm"]) is not None
        # Water text + diet tool only → still phantom
        assert _check_state_claims(water_text, ["log_food"]) is not None


class TestPhantomRowFilter:
    """The history hydration filter prunes likely-phantom rows so they don't
    seed future turns with lies."""

    def test_phantom_response_no_tools_is_filtered(self):
        # Same "135g, 35 to go" with tools_called=None → must be filtered
        assert _looks_like_phantom_row("135g, 35 to go", None) is True

    def test_phantom_response_with_tools_is_kept(self):
        # Same response BUT tool fired → trust the row
        assert _looks_like_phantom_row("135g, 35 to go", '["log_food"]') is False

    def test_clean_response_is_kept(self):
        # No state claim → keep
        assert _looks_like_phantom_row("logged. nice work.", None) is False

    def test_empty_response_not_filtered(self):
        # Don't crash on empty
        assert _looks_like_phantom_row("", None) is False
        assert _looks_like_phantom_row(None, None) is False

    def test_water_phantom_filtered(self):
        assert _looks_like_phantom_row("you're at 4 cups, 12 to go", None) is True


class TestPenancePhantom:
    """Penance state claims must be backed by a declare_penance call.

    These mirror the diet/water phantom detection — Luke saying 'your penance
    is set' without actually calling declare_penance is the same failure class
    as claiming '170g goal hit' without log_food.
    """

    def test_penance_set_no_tool_is_phantom(self):
        from bot.utils.luke_chat import _check_state_claims
        result = _check_state_claims("got it. your penance is set for water today.", [])
        assert result is not None
        claim_class, _snippet = result
        assert claim_class == "penance"

    def test_penance_set_with_declare_penance_is_legitimate(self):
        from bot.utils.luke_chat import _check_state_claims
        result = _check_state_claims(
            "got it. your penance is set for water today.",
            ["declare_penance"],
        )
        assert result is None

    def test_marked_as_penance_no_tool_is_phantom(self):
        from bot.utils.luke_chat import _check_state_claims
        result = _check_state_claims("ok, marked as penance — 2x today.", [])
        assert result is not None
        assert result[0] == "penance"

    def test_set_up_penance_no_tool_is_phantom(self):
        from bot.utils.luke_chat import _check_state_claims
        result = _check_state_claims("set up penance for the workout. crush it.", [])
        assert result is not None
        assert result[0] == "penance"

    def test_penance_phantom_filter_hides_from_history(self):
        # Phantom rows must be filtered out of history hydration so old lies
        # don't seed the next turn's chat memory.
        assert _looks_like_phantom_row("your penance is set for reading", None) is True

    def test_penance_with_tool_kept_in_history(self):
        import json as _json
        assert _looks_like_phantom_row(
            "your penance is set for reading",
            _json.dumps(["declare_penance"]),
        ) is False

    def test_water_tool_does_not_legitimize_penance_claim(self):
        """A penance claim must be backed by declare_penance specifically,
        not just any state-changing tool. Otherwise log_water_dm could
        cover a phantom 'penance is set' claim."""
        from bot.utils.luke_chat import _check_state_claims
        result = _check_state_claims(
            "your penance is set for water",
            ["log_water_dm"],
        )
        assert result is not None
        assert result[0] == "penance"
