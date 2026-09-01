"""Tests for the part of this server that is easiest to get wrong.

Telling an automated harness run from a real playtest is the single most
consequential piece of logic here: of the 130 sessions this was built against,
only 4 are playtests. Get it wrong and every statistic the server reports is
confidently about nothing.

It is also the piece where the obvious heuristic is backwards, which is exactly
why it is worth pinning down.

Run with:  PYTHONPATH=src python -m pytest tests/ -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_playtest_analytics.data.loader import (
    HARNESS_ENEMY,
    MIN_PLAYTEST_SECONDS,
    SessionStore,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "sessions"


def write_session(folder: Path, name: str, **overrides) -> Path:
    document = {
        "sessionId": name,
        "playerId": "Player_00",
        "gameVersion": "0.3.0",
        "levelVersion": "Gauntlet01_v2",
        "level": "Gauntlet01",
        "startTime": "2026-08-27T10:00:00Z",
        "duration": 200.0,
        "completed": True,
        "finalResult": "Completed",
        "platform": "WindowsPlayer",
        "droppedEvents": 0,
        "summary": {"deaths": 0, "roomsCompleted": 8},
        "events": [],
    }
    document.update(overrides)
    path = folder / (name + ".json")
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def store_entry(folder: Path, session_id: str):
    return SessionStore(folder).get(session_id)


class TestHarnessDetection:
    def test_marker_enemy_in_summary_marks_a_run_automated(self, tmp_path: Path):
        write_session(
            tmp_path,
            "harness",
            summary={"deaths": 0, "roomsCompleted": 8,
                     "mostDangerousEnemy": HARNESS_ENEMY},
        )
        assert store_entry(tmp_path, "harness").automated is True

    def test_marker_enemy_in_the_timeline_is_also_found(self, tmp_path: Path):
        write_session(
            tmp_path,
            "harness",
            events=[{"eventType": "EnemySpawned",
                     "data": {"enemyType": HARNESS_ENEMY}}],
        )
        assert store_entry(tmp_path, "harness").automated is True

    def test_a_plain_session_is_not_automated(self, tmp_path: Path):
        write_session(
            tmp_path,
            "human",
            events=[{"eventType": "EnemySpawned",
                     "data": {"enemyType": "MeleeEnemy"}}],
        )
        assert store_entry(tmp_path, "human").automated is False

    def test_file_size_does_not_decide(self, tmp_path: Path):
        """The trap this module exists to avoid.

        Harness runs produce the *largest* files in the corpus -- thousands of
        events in eighteen seconds -- while a real playtest can be smaller. A
        size-based heuristic gets it exactly backwards.
        """
        write_session(
            tmp_path,
            "big_harness",
            duration=18.0,
            summary={"deaths": 0, "roomsCompleted": 0,
                     "mostDangerousEnemy": HARNESS_ENEMY},
            events=[{"eventType": "AttackStarted", "data": {}}] * 4000,
        )
        write_session(tmp_path, "small_human", duration=200.0, events=[])

        store = SessionStore(tmp_path)
        big, small = store.get("big_harness"), store.get("small_human")

        assert big.size_bytes > small.size_bytes
        assert big.automated is True
        assert small.automated is False


class TestPlaytestFilter:
    def test_short_runs_are_not_playtests(self, tmp_path: Path):
        write_session(tmp_path, "blink", duration=0.5)
        assert store_entry(tmp_path, "blink").is_playtest is False

    def test_a_run_with_no_rooms_is_not_a_playtest(self, tmp_path: Path):
        write_session(tmp_path, "quit_early", duration=120.0,
                      summary={"deaths": 0, "roomsCompleted": 0})
        assert store_entry(tmp_path, "quit_early").is_playtest is False

    def test_a_real_run_is_a_playtest(self, tmp_path: Path):
        write_session(tmp_path, "real", duration=MIN_PLAYTEST_SECONDS + 1)
        assert store_entry(tmp_path, "real").is_playtest is True

    def test_automated_runs_are_excluded_however_long(self, tmp_path: Path):
        write_session(tmp_path, "long_harness", duration=600.0,
                      summary={"deaths": 0, "roomsCompleted": 10,
                               "mostDangerousEnemy": HARNESS_ENEMY})
        assert store_entry(tmp_path, "long_harness").is_playtest is False

    def test_entries_hides_non_playtests_by_default(self, tmp_path: Path):
        write_session(tmp_path, "real", duration=200.0)
        write_session(tmp_path, "blink", duration=0.4)

        store = SessionStore(tmp_path)
        assert [e.session_id for e in store.entries()] == ["real"]
        assert len(list(store.entries(include_automated=True))) == 2


class TestRobustness:
    def test_a_broken_file_does_not_hide_the_good_ones(self, tmp_path: Path):
        write_session(tmp_path, "good", duration=200.0)
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

        store = SessionStore(tmp_path)
        assert [e.session_id for e in store.entries()] == ["good"]
        assert [name for name, _ in store.unreadable] == ["broken.json"]

    def test_a_missing_folder_is_empty_not_fatal(self, tmp_path: Path):
        assert len(SessionStore(tmp_path / "nope")) == 0

    def test_a_session_can_be_found_by_file_name(self, tmp_path: Path):
        """Models pass the file name instead of the id often enough to matter."""
        write_session(tmp_path, "by_name", sessionId="session_20260827_1")
        assert SessionStore(tmp_path).get("by_name") is not None


@pytest.mark.skipif(not EXAMPLES.is_dir(), reason="bundled examples missing")
class TestBundledExamples:
    """The examples ship with the repository, so they are part of its contract."""

    def test_none_of_them_is_a_harness_run(self):
        """The contract is that every bundled example is a real playtest.

        Not that there are exactly N of them: the count grows whenever someone
        plays, and a test that pins it just fails on good news. What must hold
        is that nothing from the automated harness slipped in, since the whole
        corpus is what the README's numbers are computed from.
        """
        entries = list(SessionStore(EXAMPLES).entries())
        assert entries, "the repository ships with no examples"
        assert [e.session_id for e in entries if e.automated] == []

    def test_they_cover_the_versions_the_readme_compares(self):
        versions = {entry.game_version for entry in SessionStore(EXAMPLES).entries()}
        assert {"0.2.0", "0.3.0"} <= versions

    def test_nothing_is_unreadable(self):
        store = SessionStore(EXAMPLES)
        store.load_index()
        assert store.unreadable == []
