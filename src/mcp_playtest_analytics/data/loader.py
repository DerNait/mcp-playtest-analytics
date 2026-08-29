"""Reading playtest sessions off disk.

Two layers, for one reason: a real session file is around a megabyte, almost all
of it position sampling, and a folder can hold hundreds of them.

* The **index** reads only the header and the computed ``summary`` of each file
  and keeps it in memory. Most questions -- which room was hardest, which weapon
  was preferred, how two builds compare -- are answered entirely from there,
  without opening a single timeline.
* The **full session** (the ``events`` array) is loaded on demand and cached, for
  the questions that genuinely need positions or raw events.

That split is what lets every tool return an aggregate instead of a file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

#: A run shorter than this never became a play session. The corpus is full of
#: half-second launches that are neither harness runs nor playtests.
MIN_PLAYTEST_SECONDS = 60.0

#: The synthetic enemy the automated test harness spawns. Its presence is the
#: reliable marker of a harness run -- file size is not: harness runs produce
#: the *largest* files in the corpus (thousands of events in eighteen seconds),
#: while the smallest files are aborted launches.
HARNESS_ENEMY = "TestHarness"


@dataclass
class SessionIndexEntry:
    """Everything known about a session without reading its timeline."""

    session_id: str
    path: Path
    player_id: str
    game_version: str
    level_version: str
    level: str
    start_time: str
    duration: float
    completed: bool
    final_result: str
    platform: str
    dropped_events: int
    feel_settings: str | None
    summary: dict[str, Any] = field(default_factory=dict)
    automated: bool = False
    size_bytes: int = 0

    @property
    def deaths(self) -> int:
        return int(self.summary.get("deaths", 0) or 0)

    @property
    def rooms_completed(self) -> int:
        return int(self.summary.get("roomsCompleted", 0) or 0)

    @property
    def is_playtest(self) -> bool:
        """A real play session, as opposed to a harness run or a stray launch."""
        if self.automated:
            return False
        return self.duration >= MIN_PLAYTEST_SECONDS and self.rooms_completed > 0

    def brief(self) -> dict[str, Any]:
        """The compact form `list_sessions` returns."""
        return {
            "sessionId": self.session_id,
            "playerId": self.player_id,
            "startTime": self.start_time,
            "duration": round(self.duration, 1),
            "gameVersion": self.game_version,
            "levelVersion": self.level_version,
            "result": self.final_result,
            "deaths": self.deaths,
            "roomsCompleted": self.rooms_completed,
            "automated": self.automated,
        }


class SessionStore:
    """An indexed folder of session files."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._index: dict[str, SessionIndexEntry] = {}
        self._full: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self.unreadable: list[tuple[str, str]] = []

    # ----------------------------------------------------------------- index

    def load_index(self, force: bool = False) -> None:
        """Read headers and summaries. Safe to call repeatedly."""
        if self._loaded and not force:
            return

        self._index.clear()
        self.unreadable.clear()

        if self.root.is_dir():
            for path in sorted(self.root.glob("*.json")):
                try:
                    entry = self._read_entry(path)
                except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                    # One malformed file must not blind the server to the rest.
                    self.unreadable.append((path.name, str(exc)))
                    continue
                self._index[entry.session_id] = entry

        self._loaded = True

    def _read_entry(self, path: Path) -> SessionIndexEntry:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)

        summary = document.get("summary") or {}
        return SessionIndexEntry(
            session_id=document.get("sessionId") or path.stem,
            path=path,
            player_id=document.get("playerId", ""),
            game_version=document.get("gameVersion", ""),
            level_version=document.get("levelVersion", ""),
            level=document.get("level", ""),
            start_time=document.get("startTime", ""),
            duration=float(document.get("duration", 0) or 0),
            completed=bool(document.get("completed", False)),
            final_result=document.get("finalResult", ""),
            platform=document.get("platform", ""),
            dropped_events=int(document.get("droppedEvents", 0) or 0),
            feel_settings=document.get("feelSettings"),
            summary=summary,
            automated=_is_harness_run(document, summary),
            size_bytes=path.stat().st_size,
        )

    # ---------------------------------------------------------------- access

    def __len__(self) -> int:
        self.load_index()
        return len(self._index)

    def entries(self, include_automated: bool = False) -> Iterator[SessionIndexEntry]:
        """Index entries, newest first.

        By default only real playtests: harness runs and half-second launches
        are excluded, because averaging them together is how a corpus of 130
        files pretends to say something it cannot.
        """
        self.load_index()
        chosen = [
            entry
            for entry in self._index.values()
            if include_automated or entry.is_playtest
        ]
        chosen.sort(key=lambda e: e.start_time, reverse=True)
        return iter(chosen)

    def get(self, session_id: str) -> SessionIndexEntry | None:
        self.load_index()
        entry = self._index.get(session_id)
        if entry is not None:
            return entry
        # Be forgiving: a model may pass the file name instead of the id.
        for candidate in self._index.values():
            if candidate.path.stem == session_id:
                return candidate
        return None

    def load_full(self, session_id: str) -> dict[str, Any] | None:
        """Read a whole session, including its timeline. Cached."""
        entry = self.get(session_id)
        if entry is None:
            return None
        if entry.session_id in self._full:
            return self._full[entry.session_id]

        try:
            with entry.path.open(encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

        self._full[entry.session_id] = document
        return document

    def events(self, session_id: str) -> list[dict[str, Any]]:
        document = self.load_full(session_id)
        return (document or {}).get("events", []) or []


def _is_harness_run(document: dict[str, Any], summary: dict[str, Any]) -> bool:
    """Detect an automated test-harness run.

    The cheap check first (a summary field), then a bounded scan of the timeline.
    The scan stops early: a harness run announces itself in its first events, so
    there is no reason to walk four thousand of them.
    """
    if summary.get("mostDangerousEnemy") == HARNESS_ENEMY:
        return True

    for enemy in summary.get("enemies", []) or []:
        if isinstance(enemy, dict) and enemy.get("enemyType") == HARNESS_ENEMY:
            return True

    for event in (document.get("events") or [])[:400]:
        data = event.get("data")
        if isinstance(data, dict) and HARNESS_ENEMY in (
            data.get("enemyType"),
            data.get("killerType"),
            data.get("sourceType"),
        ):
            return True

    return False
