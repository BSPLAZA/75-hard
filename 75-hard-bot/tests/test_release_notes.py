"""Tests for the release-notes announcement builder."""

from bot.release_notes import (
    CURRENT_VERSION,
    RELEASES,
    build_announcement,
)


def test_first_run_picks_up_only_non_empty_notes():
    fixtures = [
        {"version": 1, "user_facing": "first thing"},
        {"version": 2, "user_facing": ""},
        {"version": 3, "user_facing": "third thing"},
    ]
    msg = build_announcement(None, fixtures)
    # 2+ notes → bulleted header format
    assert "yo couple updates fr" in msg
    assert "first thing" in msg
    assert "third thing" in msg


def test_single_note_returns_verbatim_no_header():
    """One note shouldn't get the multi-update header — that'd read weird."""
    fixtures = [
        {"version": 1, "user_facing": "just one thing"},
    ]
    assert build_announcement(None, fixtures) == "just one thing"


def test_caught_up_returns_none():
    fixtures = [
        {"version": 50, "user_facing": "old"},
        {"version": 51, "user_facing": "newer"},
    ]
    assert build_announcement(51, fixtures) is None


def test_partial_catch_up_returns_only_unseen():
    fixtures = [
        {"version": 50, "user_facing": "fifty"},
        {"version": 51, "user_facing": "fifty-one"},
        {"version": 52, "user_facing": ""},
        {"version": 53, "user_facing": "fifty-three"},
    ]
    msg = build_announcement(50, fixtures)
    assert "yo couple updates fr" in msg
    assert "fifty-one" in msg
    assert "fifty-three" in msg
    assert "fifty\n" not in msg  # version 50 already seen, must NOT appear


def test_partial_catch_up_single_unseen_no_header():
    fixtures = [
        {"version": 50, "user_facing": "fifty"},
        {"version": 51, "user_facing": "fifty-one"},
    ]
    # Only v51 is new → single-note format, no header.
    assert build_announcement(50, fixtures) == "fifty-one"


def test_all_silent_returns_none():
    fixtures = [
        {"version": 1, "user_facing": ""},
        {"version": 2, "user_facing": ""},
    ]
    assert build_announcement(None, fixtures) is None


def test_empty_releases_returns_none():
    assert build_announcement(None, []) is None


def test_out_of_order_releases_still_ordered_by_version():
    fixtures = [
        {"version": 3, "user_facing": "third"},
        {"version": 1, "user_facing": "first"},
        {"version": 2, "user_facing": "second"},
    ]
    msg = build_announcement(None, fixtures)
    # Must appear in version order
    assert msg.index("first") < msg.index("second") < msg.index("third")


def test_multi_update_blocks_are_separated_by_blank_line():
    """Each note's text gets its own paragraph for visual separation."""
    fixtures = [
        {"version": 1, "user_facing": "alpha"},
        {"version": 2, "user_facing": "bravo"},
    ]
    msg = build_announcement(None, fixtures)
    assert "alpha\n\nbravo" in msg


def test_production_releases_have_v51_cutoff_note():
    """Smoke test: real RELEASES table includes the v51 cutoff message
    that's the whole reason this mechanism exists."""
    v51 = next((r for r in RELEASES if r["version"] == 51), None)
    assert v51 is not None
    assert "midnight" in v51["user_facing"].lower()


def test_production_first_announcement_includes_v51():
    """Smoke test: a fresh bot (last_seen=None) running today's code
    will retroactively announce the cutoff change."""
    msg = build_announcement(None)
    assert msg is not None
    assert "midnight" in msg.lower()


def test_production_caught_up_returns_none():
    """Smoke test: once the marker matches CURRENT_VERSION, no announcement."""
    assert build_announcement(CURRENT_VERSION) is None


def test_production_v54_announces_penance_grid_voice():
    """Smoke test: v54 carries the penance/grid/voice drop and uses bullets."""
    v54 = next((r for r in RELEASES if r["version"] == 54), None)
    assert v54 is not None, "v54 must exist (penance/grid/tone drop)"
    text = v54["user_facing"]
    assert "•" in text or "penance" in text.lower()
    # All three new feature areas must be mentioned in the announcement
    assert "penance" in text.lower()
    assert "grid" in text.lower()
    assert "voice" in text.lower() or "talk to me like" in text.lower()


def test_production_first_run_has_multi_update_header():
    """First-run announcement (v51 + v54 unseen) goes through the bulleted
    header path because there are 2 notes."""
    msg = build_announcement(None)
    assert msg is not None
    assert "yo couple updates fr" in msg.lower() or "couple updates" in msg.lower()
