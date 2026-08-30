"""The rest of the catalogue: damage, stalls, easy rooms, repeated failures,
session comparison, and the raw-timeline escape hatch.

Same rule as everywhere else in this server: compute and return an aggregate,
never a file. The escape hatch is the one tool that touches raw events, and it
is deliberately hard to misuse -- an event-type filter is required and the
result count is capped.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from .data.loader import SessionStore
from .protocol import Server, ToolError
from .tools import _json, _positive_int, _require, _scope

#: Hard ceiling on raw events returned by `get_events`, whatever is asked for.
MAX_EVENTS = 100

#: How many times something has to fail before it counts as a pattern.
REPEAT_THRESHOLD = 2


def register_extra(server: Server, store: SessionStore) -> None:
    """Attach the second half of the catalogue."""

    # ---------------------------------------------------------------- damage

    @server.tool(
        "get_damage_locations",
        "Where the player took damage: world coordinates, room, how much, what "
        "dealt it and the health left afterwards. Pools every playtest unless a "
        "session is named. Feeds a heatmap of the places that hurt.",
        {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "One session. Omit to pool all playtests.",
                },
                "min_amount": {
                    "type": "number",
                    "description": "Ignore hits smaller than this. Default 0.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def get_damage_locations(args: dict[str, Any]) -> str:
        floor = float(args.get("min_amount") or 0)
        hits: list[dict[str, Any]] = []
        by_room: Counter[str] = Counter()

        for entry in _scope(store, args):
            for event in store.events(entry.session_id):
                if event.get("eventType") != "PlayerDamaged":
                    continue
                data = event.get("data") or {}
                amount = float(data.get("amount", 0) or 0)
                if amount < floor:
                    continue
                room = event.get("room") or "(outside a room)"
                by_room[room] += amount
                hits.append({
                    "sessionId": entry.session_id,
                    "timestamp": event.get("timestamp"),
                    "room": event.get("room"),
                    "position": event.get("position"),
                    "amount": amount,
                    "source": data.get("source"),
                    "sourceType": data.get("sourceType"),
                    "damageType": data.get("damageType"),
                    "healthAfter": data.get("healthAfter"),
                })

        return _json({
            "hits": hits,
            "count": len(hits),
            "damageByRoom": [
                {"room": room, "totalDamage": round(total, 1)}
                for room, total in by_room.most_common()
            ],
        })

    # ----------------------------------------------------------------- stalls

    @server.tool(
        "get_stuck_moments",
        "Moments where a player stopped making progress: where they were, for "
        "how long, and what the room was still waiting for. This is the only "
        "signal for what did NOT happen -- someone standing around confused "
        "generates no other events, so the absence is the finding.",
        {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "One session. Omit to pool all playtests.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def get_stuck_moments(args: dict[str, Any]) -> str:
        moments: list[dict[str, Any]] = []

        for entry in _scope(store, args):
            # The summary carries them when the run computed them; the timeline
            # is the fallback and also the source of position and report text.
            for event in store.events(entry.session_id):
                if event.get("eventType") != "PlayerAppearsStuck":
                    continue
                data = event.get("data") or {}
                moments.append({
                    "sessionId": entry.session_id,
                    "atSecond": event.get("timestamp"),
                    "room": event.get("room") or data.get("roomId"),
                    "position": event.get("position"),
                    "secondsWithoutProgress": data.get("secondsWithoutProgress"),
                    "outstanding": data.get("outstanding"),
                    "enemiesRemaining": data.get("enemiesRemaining"),
                    "report": data.get("report"),
                })

        payload: dict[str, Any] = {"stuckMoments": moments, "count": len(moments)}
        if not moments:
            payload["note"] = (
                "Nobody stalled long enough to be recorded. That is a good sign "
                "about readability, not missing data."
            )
        else:
            payload["byRoom"] = [
                {"room": room, "occurrences": count}
                for room, count in Counter(
                    m["room"] or "(unknown)" for m in moments
                ).most_common()
            ]
        return _json(payload)

    # ------------------------------------------------------------- easy rooms

    @server.tool(
        "detect_easy_rooms",
        "Rooms that gave nobody any trouble: no deaths, little damage, cleared "
        "quickly and on the first try. The opposite problem to difficulty, and "
        "the one almost nobody measures -- a room that costs nothing may not be "
        "earning its place in the level.",
        {
            "type": "object",
            "properties": {
                "game_version": {"type": "string", "description": "Only this game version."}
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def detect_easy_rooms(args: dict[str, Any]) -> str:
        rows: dict[str, dict[str, Any]] = {}

        for entry in store.entries():
            if args.get("game_version") and entry.game_version != args["game_version"]:
                continue
            for room in entry.summary.get("rooms", []) or []:
                room_id = room.get("roomId")
                if not room_id:
                    continue
                row = rows.setdefault(room_id, {
                    "roomId": room_id,
                    "roomLabel": room.get("roomLabel"),
                    "sessions": 0, "deaths": 0, "damageReceived": 0.0,
                    "timeSpent": 0.0, "retries": 0, "lowestHealthPct": 1.0,
                    "enemiesKilled": 0,
                })
                row["sessions"] += 1
                row["deaths"] += int(room.get("deaths", 0) or 0)
                row["damageReceived"] += float(room.get("damageReceived", 0) or 0)
                row["timeSpent"] += float(room.get("timeSpent", 0) or 0)
                row["retries"] += max(int(room.get("attempts", 1) or 1) - 1, 0)
                row["enemiesKilled"] += int(room.get("enemiesKilled", 0) or 0)
                low = room.get("lowestHealthPct")
                if isinstance(low, (int, float)):
                    row["lowestHealthPct"] = min(row["lowestHealthPct"], float(low))

        easy = []
        for row in rows.values():
            sessions = max(row["sessions"], 1)
            row["avgDamageReceived"] = round(row["damageReceived"] / sessions, 1)
            row["avgTimeSpent"] = round(row["timeSpent"] / sessions, 1)
            row["lowestHealthPct"] = round(row["lowestHealthPct"], 3)
            # Untouched: nobody died, nobody retried, health never moved much.
            row["untouched"] = (
                row["deaths"] == 0
                and row["retries"] == 0
                and row["lowestHealthPct"] > 0.9
            )
            del row["damageReceived"], row["timeSpent"]
            easy.append(row)

        # A room nobody spent time in was not easy, it was never played. Those
        # are different findings and must not share a list: otherwise the
        # never-visited rooms top the ranking every time and bury the real ones.
        never_visited = [r for r in easy if r["avgTimeSpent"] <= 0]
        easy = [r for r in easy if r["avgTimeSpent"] > 0]
        easy.sort(key=lambda r: (r["avgDamageReceived"], r["avgTimeSpent"]))

        return _json({
            "rooms": easy,
            "neverVisited": [r["roomId"] for r in never_visited],
            "criterion": (
                "'untouched' means no deaths, no retries and health never fell "
                "below 90% in any session. Rooms are sorted by how little they "
                "cost the player. Rooms nobody spent time in are listed "
                "separately under neverVisited: not reaching a room is a "
                "different finding from breezing through it."
            ),
        })

    # ------------------------------------------------------ repeated failures

    @server.tool(
        "detect_repeated_failures",
        "Things players failed more than once: gaps they could not clear, "
        "puzzles they got wrong, switch sequences they reset, timed gates that "
        "expired. Shows where the level teaches badly rather than where it is "
        "simply hard.",
        {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "One session. Omit to pool all playtests.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def detect_repeated_failures(args: dict[str, Any]) -> str:
        gaps: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"failures": 0, "clears": 0, "attemptsToClear": []}
        )
        puzzles: Counter[str] = Counter()
        sequences: dict[str, list[str]] = defaultdict(list)
        gates: Counter[str] = Counter()

        for entry in _scope(store, args):
            for event in store.events(entry.session_id):
                kind = event.get("eventType")
                data = event.get("data") or {}

                if kind == "GapFailed":
                    row = gaps[data.get("gapId", "?")]
                    row["failures"] += 1
                    row["requiredTechnique"] = data.get("requiredTechnique")
                    row["span"] = data.get("span")
                elif kind == "GapCleared":
                    row = gaps[data.get("gapId", "?")]
                    row["clears"] += 1
                    row["requiredTechnique"] = data.get("requiredTechnique")
                    row["span"] = data.get("span")
                    attempt = data.get("attempt")
                    if isinstance(attempt, int):
                        row["attemptsToClear"].append(attempt)
                elif kind == "PuzzleFailed":
                    puzzles[data.get("puzzleId", "?")] += 1
                elif kind == "SwitchSequenceReset":
                    sequences[data.get("sequenceId", "?")].append(
                        str(data.get("attempted", ""))
                    )
                elif kind == "TimedGateExpired":
                    gates[data.get("gateId", "?")] += 1

        repeated_gaps = [
            {"gapId": gap_id, **row,
             "avgAttemptsToClear": round(
                 sum(row["attemptsToClear"]) / len(row["attemptsToClear"]), 2
             ) if row["attemptsToClear"] else None}
            for gap_id, row in gaps.items()
            if row["failures"] >= REPEAT_THRESHOLD
        ]
        repeated_gaps.sort(key=lambda r: -r["failures"])

        payload = {
            "gaps": repeated_gaps,
            "puzzles": [
                {"puzzleId": p, "failures": n}
                for p, n in puzzles.most_common() if n >= REPEAT_THRESHOLD
            ],
            "switchSequences": [
                {"sequenceId": s, "resets": len(orders), "ordersTried": orders}
                for s, orders in sequences.items() if len(orders) >= REPEAT_THRESHOLD
            ],
            "timedGates": [
                {"gateId": g, "expirations": n}
                for g, n in gates.most_common() if n >= REPEAT_THRESHOLD
            ],
            "threshold": REPEAT_THRESHOLD,
        }
        if not any(payload[k] for k in ("gaps", "puzzles", "switchSequences", "timedGates")):
            payload["note"] = (
                f"Nothing failed {REPEAT_THRESHOLD} times or more. Single "
                "failures are recorded but are not a pattern."
            )
        return _json(payload)

    # ----------------------------------------------------- session comparison

    @server.tool(
        "compare_sessions",
        "Put two sessions side by side: duration, deaths, damage, accuracy, "
        "rooms completed, weapon preference and per-room time. Use it to see "
        "how differently two players handled the same level.",
        {
            "type": "object",
            "properties": {
                "session_a": {"type": "string", "description": "First session id."},
                "session_b": {"type": "string", "description": "Second session id."},
            },
            "required": ["session_a", "session_b"],
            "additionalProperties": False,
        },
    )
    def compare_sessions(args: dict[str, Any]) -> str:
        a = _require(store, {"session_id": args.get("session_a")})
        b = _require(store, {"session_id": args.get("session_b")})

        payload: dict[str, Any] = {
            "a": _session_profile(a),
            "b": _session_profile(b),
            "rooms": _room_deltas(a, b),
        }
        if a.level_version != b.level_version:
            payload["levelWarning"] = (
                f"Different level versions ({a.level_version} vs "
                f"{b.level_version}): room-by-room differences may be layout "
                "changes rather than player behaviour."
            )
        return _json(payload)

    # ------------------------------------------------------- the escape hatch

    @server.tool(
        "get_events",
        "Raw timeline events, for when an aggregate is not enough. An event "
        "type filter is REQUIRED and results are hard-capped: a session holds "
        "thousands of events and returning them whole is never the right move. "
        "Prefer the analysis tools; reach for this to inspect one specific "
        "moment.",
        {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id."},
                "event_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Event types to include, e.g. ['PlayerDamaged', "
                        "'EnemyKilled']. Required."
                    ),
                },
                "room": {"type": "string", "description": "Only events in this room."},
                "from_second": {"type": "number", "description": "Earliest timestamp."},
                "to_second": {"type": "number", "description": "Latest timestamp."},
                "limit": {
                    "type": "integer",
                    "description": f"How many to return, capped at {MAX_EVENTS}. Default 50.",
                },
            },
            "required": ["session_id", "event_types"],
            "additionalProperties": False,
        },
    )
    def get_events(args: dict[str, Any]) -> str:
        entry = _require(store, args)

        wanted = args.get("event_types")
        if not isinstance(wanted, list) or not wanted:
            raise ToolError(
                "event_types is required and must be a non-empty list. Without "
                "it this tool would return the whole session, which is what the "
                "analysis tools exist to avoid."
            )
        wanted_set = {str(t) for t in wanted}
        limit = min(_positive_int(args.get("limit"), default=50), MAX_EVENTS)

        selected = []
        for event in store.events(entry.session_id):
            if event.get("eventType") not in wanted_set:
                continue
            if args.get("room") and event.get("room") != args["room"]:
                continue
            timestamp = float(event.get("timestamp", 0) or 0)
            if args.get("from_second") is not None and timestamp < float(args["from_second"]):
                continue
            if args.get("to_second") is not None and timestamp > float(args["to_second"]):
                continue
            selected.append(event)

        payload: dict[str, Any] = {
            "sessionId": entry.session_id,
            "matched": len(selected),
            "returned": min(len(selected), limit),
            "events": selected[:limit],
        }
        if len(selected) > limit:
            payload["truncated"] = (
                f"{len(selected)} events matched; the first {limit} are shown. "
                "Narrow the filter with room or a time range."
            )
        if not selected:
            payload["note"] = (
                f"No events of those types in this session. Types present "
                f"include: {', '.join(_types_present(store, entry.session_id)[:12])}"
            )
        return _json(payload)


# ------------------------------------------------------------------ utilities

def _session_profile(entry: Any) -> dict[str, Any]:
    summary = entry.summary
    weapons = {
        weapon.get("weaponId"): round(float(weapon.get("timeEquipped", 0) or 0), 1)
        for weapon in summary.get("weapons", []) or []
        if weapon.get("weaponId")
    }
    return {
        "sessionId": entry.session_id,
        "playerId": entry.player_id,
        "gameVersion": entry.game_version,
        "levelVersion": entry.level_version,
        "result": entry.final_result,
        "duration": round(entry.duration, 1),
        "deaths": entry.deaths,
        "damageReceived": summary.get("damageReceived"),
        "damageDealt": summary.get("damageDealt"),
        "overallAccuracy": summary.get("overallAccuracy"),
        "roomsCompleted": entry.rooms_completed,
        "roomsTotal": summary.get("roomsTotal"),
        "favoriteWeapon": summary.get("favoriteWeapon"),
        "weaponTimeEquipped": weapons,
        "lowestHealthPct": summary.get("lowestHealthPct"),
        "timeInDanger": summary.get("timeInDanger"),
        "healingUsed": summary.get("healingUsed"),
        "unusedMechanics": summary.get("unusedMechanics", []),
        "traversal": summary.get("traversal"),
    }


def _room_deltas(a: Any, b: Any) -> list[dict[str, Any]]:
    """Per-room time and damage for both sessions, aligned by room id."""
    rooms_a = {r.get("roomId"): r for r in a.summary.get("rooms", []) or []}
    rooms_b = {r.get("roomId"): r for r in b.summary.get("rooms", []) or []}

    deltas = []
    for room_id in sorted(set(rooms_a) | set(rooms_b)):
        left, right = rooms_a.get(room_id, {}), rooms_b.get(room_id, {})
        time_a = float(left.get("timeSpent", 0) or 0)
        time_b = float(right.get("timeSpent", 0) or 0)
        deltas.append({
            "roomId": room_id,
            "timeSpentA": round(time_a, 1),
            "timeSpentB": round(time_b, 1),
            "timeDelta": round(time_b - time_a, 1),
            "damageA": left.get("damageReceived"),
            "damageB": right.get("damageReceived"),
            "visitedInBoth": bool(left) and bool(right),
        })
    return deltas


def _types_present(store: SessionStore, session_id: str) -> list[str]:
    counts = Counter(
        event.get("eventType") for event in store.events(session_id)
    )
    return [name for name, _ in counts.most_common() if name]
