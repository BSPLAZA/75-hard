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
    assert build_announcement(None, fixtures) == "first thing\n\nthird thing"


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
    assert build_announcement(50, fixtures) == "fifty-one\n\nfifty-three"


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
    assert build_announcement(None, fixtures) == "first\n\nsecond\n\nthird"


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
