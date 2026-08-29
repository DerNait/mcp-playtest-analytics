"""The tool catalogue.

Every tool computes and returns an aggregate. **None of them returns a whole
session**: a real file is around a megabyte, mostly position sampling, which is
200-300k tokens if handed to a model whole. It would fill the context, cost real
money, and leave the model doing arithmetic by hand -- what it is worst at.

The tool thinks; the model interprets and explains.

Results are JSON text, which models read reliably and which keeps the numbers
unambiguous.
"""

from __future__ import annotations

import json
from typing import Any

from .data.loader import SessionStore
from .protocol import Server, ToolError

#: Ceiling on how many path points any single call may return.
MAX_PATH_POINTS = 200

# Schemas stay plain -- flat objects, basic types, explicit required lists --
# so that any host can translate them, whatever provider it targets.
_SESSION_ARG = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "Session id, as returned by list_sessions.",
        }
    },
    "required": ["session_id"],
    "additionalProperties": False,
}


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def register(server: Server, store: SessionStore) -> None:
    """Attach every tool to the server."""

    # ------------------------------------------------------------- discovery

    @server.tool(
        "list_sessions",
        "List recorded playtest sessions, newest first. Returns one compact row "
        "per session (id, player, date, duration, versions, result, deaths, "
        "rooms completed) -- never the session contents. Start here to find a "
        "session id for the other tools.",
        {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many sessions to return. Default 20.",
                },
                "game_version": {
                    "type": "string",
                    "description": "Only sessions from this game version, e.g. '0.3.0'.",
                },
                "level_version": {
                    "type": "string",
                    "description": "Only sessions on this level version, e.g. 'Gauntlet01_v2'.",
                },
                "include_automated": {
                    "type": "boolean",
                    "description": (
                        "Include automated test-harness runs and aborted "
                        "launches. Default false: they are not playtests and "
                        "averaging them in corrupts every statistic."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def list_sessions(args: dict[str, Any]) -> str:
        limit = _positive_int(args.get("limit"), default=20)
        include_automated = bool(args.get("include_automated", False))

        rows = []
        for entry in store.entries(include_automated=include_automated):
            if args.get("game_version") and entry.game_version != args["game_version"]:
                continue
            if args.get("level_version") and entry.level_version != args["level_version"]:
                continue
            rows.append(entry.brief())
            if len(rows) >= limit:
                break

        payload: dict[str, Any] = {
            "sessions": rows,
            "returned": len(rows),
            "filesInFolder": len(store),
        }
        if not include_automated:
            payload["note"] = (
                "Automated harness runs and launches under "
                "60 seconds are excluded. Pass include_automated=true to see them."
            )
        if store.unreadable:
            payload["unreadableFiles"] = [name for name, _ in store.unreadable]
        return _json(payload)

    @server.tool(
        "get_session_summary",
        "The full computed summary for one session: duration, deaths, damage, "
        "favourite weapon, hardest room, per-room rows, per-weapon rows, "
        "per-enemy rows, and which mechanics went unused. A few KB. This answers "
        "most questions on its own -- try it before any other analysis tool.",
        _SESSION_ARG,
    )
    def get_session_summary(args: dict[str, Any]) -> str:
        entry = _require(store, args)
        payload = {
            "sessionId": entry.session_id,
            "playerId": entry.player_id,
            "gameVersion": entry.game_version,
            "levelVersion": entry.level_version,
            "startTime": entry.start_time,
            "result": entry.final_result,
            "automated": entry.automated,
            "summary": entry.summary,
        }
        if entry.dropped_events:
            payload["warning"] = (
                f"droppedEvents={entry.dropped_events}: the event buffer "
                "overflowed, so counts for this session are incomplete."
            )
        if entry.feel_settings and entry.feel_settings != "shake=1;hitstop=1;flash=1;numbers=1":
            payload["feelSettingsWarning"] = (
                f"played with non-default effect settings ({entry.feel_settings}); "
                "not comparable with default sessions."
            )
        return _json(payload)

    # --------------------------------------------------------------- analysis

    @server.tool(
        "get_room_statistics",
        "Per-room numbers for one session: attempts, deaths, time spent, clear "
        "time, damage received, enemies killed, lowest health reached and time "
        "spent in danger. Use it to find where a player struggled.",
        {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id."},
                "room_id": {
                    "type": "string",
                    "description": "Only this room, e.g. 'Room_03'. Omit for all rooms.",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    )
    def get_room_statistics(args: dict[str, Any]) -> str:
        entry = _require(store, args)
        rooms = entry.summary.get("rooms", []) or []
        if args.get("room_id"):
            rooms = [r for r in rooms if r.get("roomId") == args["room_id"]]
            if not rooms:
                raise ToolError(f"no room '{args['room_id']}' in this session")
        return _json({
            "sessionId": entry.session_id,
            "mostDifficultRoom": entry.summary.get("mostDifficultRoom"),
            "roomsCompleted": entry.summary.get("roomsCompleted"),
            "roomsTotal": entry.summary.get("roomsTotal"),
            "rooms": rooms,
        })

    @server.tool(
        "get_weapon_statistics",
        "Per-weapon numbers for one session: time equipped, attacks, hits, "
        "accuracy, damage dealt, kills and average hit distance. Shows which "
        "weapon a player preferred and whether that preference paid off.",
        _SESSION_ARG,
    )
    def get_weapon_statistics(args: dict[str, Any]) -> str:
        entry = _require(store, args)
        return _json({
            "sessionId": entry.session_id,
            "favoriteWeapon": entry.summary.get("favoriteWeapon"),
            "overallAccuracy": entry.summary.get("overallAccuracy"),
            "weapons": entry.summary.get("weapons", []),
        })

    @server.tool(
        "get_enemy_statistics",
        "Per-enemy-type numbers for one session: how many spawned and were "
        "killed, how much damage they dealt to the player, how many times they "
        "killed the player, and average time to kill.",
        _SESSION_ARG,
    )
    def get_enemy_statistics(args: dict[str, Any]) -> str:
        entry = _require(store, args)
        return _json({
            "sessionId": entry.session_id,
            "mostDangerousEnemy": entry.summary.get("mostDangerousEnemy"),
            "enemies": entry.summary.get("enemies", []),
        })

    @server.tool(
        "get_death_locations",
        "Where the player died, with world coordinates, room, killer and the "
        "weapon they were holding. Feeds a heatmap. Omit session_id to pool "
        "every playtest.",
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
    def get_death_locations(args: dict[str, Any]) -> str:
        deaths: list[dict[str, Any]] = []
        for entry in _scope(store, args):
            for event in store.events(entry.session_id):
                if event.get("eventType") != "PlayerDied":
                    continue
                data = event.get("data") or {}
                deaths.append({
                    "sessionId": entry.session_id,
                    "timestamp": event.get("timestamp"),
                    "room": event.get("room"),
                    "position": event.get("position"),
                    "killer": data.get("killer"),
                    "killerType": data.get("killerType"),
                    "weaponEquipped": event.get("weaponEquipped") or data.get("weaponEquipped"),
                })
        payload: dict[str, Any] = {"deaths": deaths, "count": len(deaths)}
        if not deaths:
            payload["note"] = (
                "No deaths recorded in the sessions examined. That is a finding, "
                "not an error: check lowest health and time in danger instead to "
                "tell a comfortable run from a near miss."
            )
        return _json(payload)

    @server.tool(
        "get_player_path",
        "The route a player took, as world-space points, always downsampled to "
        "at most a couple of hundred points. Use it to see where they went, "
        "where they lingered and which areas they never visited.",
        {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session id."},
                "room_id": {"type": "string", "description": "Only within this room."},
                "max_points": {
                    "type": "integer",
                    "description": f"Points to return, capped at {MAX_PATH_POINTS}. Default 100.",
                },
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    )
    def get_player_path(args: dict[str, Any]) -> str:
        entry = _require(store, args)
        limit = min(_positive_int(args.get("max_points"), default=100), MAX_PATH_POINTS)

        samples = [
            event
            for event in store.events(entry.session_id)
            if event.get("eventType") == "PlayerPositionSample"
            and (not args.get("room_id") or event.get("room") == args["room_id"])
        ]

        points = [
            {
                "t": round(float(s.get("timestamp", 0) or 0), 1),
                "room": s.get("room"),
                "position": s.get("position"),
                "speed": (s.get("data") or {}).get("speed"),
            }
            for s in _downsample(samples, limit)
        ]

        return _json({
            "sessionId": entry.session_id,
            "totalSamples": len(samples),
            "returned": len(points),
            "downsampled": len(samples) > len(points),
            "distanceTravelled": entry.summary.get("distanceTravelled"),
            "path": points,
        })

    # -------------------------------------------------------------- detection

    @server.tool(
        "detect_difficult_rooms",
        "Rank rooms by how much trouble they caused across playtests, weighting "
        "deaths above damage, time and retries. Use it to find where the level "
        "is too hard.",
        {
            "type": "object",
            "properties": {
                "game_version": {
                    "type": "string",
                    "description": "Only sessions from this game version.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def detect_difficult_rooms(args: dict[str, Any]) -> str:
        scores = _aggregate_rooms(store, args.get("game_version"))
        ranked = sorted(scores.values(), key=lambda r: -r["difficultyScore"])
        return _json({
            "rooms": ranked[:12],
            "sessionsExamined": scores and ranked[0]["sessions"] or 0,
            "scoring": "deaths x 100 + damage/10 + retries x 20 + seconds/10",
        })

    @server.tool(
        "detect_unused_mechanics",
        "Which mechanics players never used, and how long they took to first "
        "use the ones they did. A mechanic missing from first-use timings is "
        "the finding: it means nobody touched it.",
        {
            "type": "object",
            "properties": {
                "game_version": {"type": "string", "description": "Only this game version."}
            },
            "required": [],
            "additionalProperties": False,
        },
    )
    def detect_unused_mechanics(args: dict[str, Any]) -> str:
        rows = []
        for entry in store.entries():
            if args.get("game_version") and entry.game_version != args["game_version"]:
                continue
            rows.append({
                "sessionId": entry.session_id,
                "gameVersion": entry.game_version,
                "firstUse": entry.summary.get("mechanicFirstUse", {}),
                "unused": entry.summary.get("unusedMechanics", []),
            })
        never: set[str] = set()
        for row in rows:
            never |= set(row["unused"])
        return _json({
            "sessions": rows,
            "unusedInEverySession": sorted(never),
        })

    # ------------------------------------------------------------- comparison

    @server.tool(
        "compare_versions",
        "Compare two game versions by pooling their playtests: duration, "
        "deaths, damage, accuracy, weapon usage share and per-room time. Use it "
        "to tell whether a change actually helped.",
        {
            "type": "object",
            "properties": {
                "version_a": {"type": "string", "description": "First game version, e.g. '0.2.0'."},
                "version_b": {"type": "string", "description": "Second game version, e.g. '0.3.0'."},
            },
            "required": ["version_a", "version_b"],
            "additionalProperties": False,
        },
    )
    def compare_versions(args: dict[str, Any]) -> str:
        a = _version_profile(store, args["version_a"])
        b = _version_profile(store, args["version_b"])

        payload: dict[str, Any] = {args["version_a"]: a, args["version_b"]: b}

        thin = [v for v, p in ((args["version_a"], a), (args["version_b"], b))
                if p["sessions"] < 3]
        if thin:
            payload["caution"] = (
                f"Only a handful of playtests for {', '.join(thin)}. "
                "Differences between these versions are anecdotes, not evidence; "
                "report them as such."
            )
        if a["sessions"] and b["sessions"] and a["levelVersions"] != b["levelVersions"]:
            payload["levelWarning"] = (
                "The two versions were played on different level versions, so "
                "room-by-room differences may be layout changes, not the change "
                "being tested."
            )
        return _json(payload)


# ------------------------------------------------------------------ utilities

def _require(store: SessionStore, args: dict[str, Any]):
    session_id = args.get("session_id")
    if not session_id:
        raise ToolError("session_id is required")
    entry = store.get(session_id)
    if entry is None:
        known = [e.session_id for e in store.entries()][:5]
        raise ToolError(
            f"no session '{session_id}'. Recent ids: {', '.join(known) or '(none)'}"
        )
    return entry


def _scope(store: SessionStore, args: dict[str, Any]):
    if args.get("session_id"):
        return [_require(store, args)]
    return list(store.entries())


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _downsample(items: list[Any], limit: int) -> list[Any]:
    """Evenly thin a list, always keeping the first and last points."""
    if len(items) <= limit or limit <= 0:
        return items
    step = len(items) / limit
    picked = [items[int(i * step)] for i in range(limit)]
    if picked[-1] is not items[-1]:
        picked[-1] = items[-1]
    return picked


def _aggregate_rooms(store: SessionStore, game_version: str | None) -> dict[str, dict[str, Any]]:
    rooms: dict[str, dict[str, Any]] = {}
    for entry in store.entries():
        if game_version and entry.game_version != game_version:
            continue
        for room in entry.summary.get("rooms", []) or []:
            room_id = room.get("roomId")
            if not room_id:
                continue
            row = rooms.setdefault(room_id, {
                "roomId": room_id, "sessions": 0, "deaths": 0,
                "damageReceived": 0.0, "attempts": 0, "timeSpent": 0.0,
                "lowestHealthPct": 1.0,
            })
            row["sessions"] += 1
            row["deaths"] += int(room.get("deaths", 0) or 0)
            row["damageReceived"] += float(room.get("damageReceived", 0) or 0)
            row["attempts"] += max(int(room.get("attempts", 1) or 1) - 1, 0)
            row["timeSpent"] += float(room.get("timeSpent", 0) or 0)
            low = room.get("lowestHealthPct")
            if isinstance(low, (int, float)):
                row["lowestHealthPct"] = min(row["lowestHealthPct"], float(low))

    for row in rooms.values():
        row["difficultyScore"] = round(
            row["deaths"] * 100
            + row["damageReceived"] / 10
            + row["attempts"] * 20
            + row["timeSpent"] / 10,
            1,
        )
        row["damageReceived"] = round(row["damageReceived"], 1)
        row["timeSpent"] = round(row["timeSpent"], 1)
        row["lowestHealthPct"] = round(row["lowestHealthPct"], 3)
    return rooms


def _version_profile(store: SessionStore, version: str) -> dict[str, Any]:
    entries = [e for e in store.entries() if e.game_version == version]
    if not entries:
        return {"sessions": 0, "note": f"no playtests recorded for version {version}"}

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    weapon_time: dict[str, float] = {}
    for entry in entries:
        for weapon in entry.summary.get("weapons", []) or []:
            name = weapon.get("weaponId")
            if name:
                weapon_time[name] = weapon_time.get(name, 0.0) + float(
                    weapon.get("timeEquipped", 0) or 0
                )
    total_time = sum(weapon_time.values()) or 1.0

    return {
        "sessions": len(entries),
        "sessionIds": [e.session_id for e in entries],
        "levelVersions": sorted({e.level_version for e in entries}),
        "completedRuns": sum(1 for e in entries if e.completed),
        "avgDuration": mean([e.duration for e in entries]),
        "avgDeaths": mean([float(e.deaths) for e in entries]),
        "avgDamageReceived": mean(
            [float(e.summary.get("damageReceived", 0) or 0) for e in entries]
        ),
        "avgAccuracy": mean(
            [float(e.summary.get("overallAccuracy", 0) or 0) for e in entries]
        ),
        "avgRoomsCompleted": mean([float(e.rooms_completed) for e in entries]),
        "weaponUsageShare": {
            name: round(seconds / total_time, 3)
            for name, seconds in sorted(weapon_time.items())
        },
    }
