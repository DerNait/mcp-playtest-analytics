# mcp-playtest-analytics

An MCP server that turns raw playtesting telemetry into answers about game design.

Point it at a folder of session files from a playtested game and ask things like
*which room was hardest*, *which weapon did players actually use*, *where did they
die*, or *did the change between two builds help*. The server does the arithmetic
and returns aggregates; the language model interprets them.

Built for **CC3067 Redes**, Universidad del Valle de Guatemala.

## Why this is not a file reader

**No tool ever returns a full session.** A real session file is around 1 MB,
mostly position sampling — roughly 200-300k tokens if handed to a model whole.
That fills the context window, costs real money, and leaves the model doing
arithmetic by hand, which is what it is worst at.

Instead, each tool computes and returns the result. `get_player_path`
downsamples. `get_events` requires an event-type filter and enforces a hard cap.
**The tool thinks; the model interprets and explains.**

## Status

🚧 **In development.** Phase 0: project setup. Tool implementations land in
phase 3, along with example sessions and full per-tool documentation.

## Planned tools

**Discovery**

| Tool | Returns |
|---|---|
| `list_sessions` | Session index: id, date, duration, result, versions, deaths |
| `get_session_summary` | The full computed summary for one session |

**Single-session analysis**

| Tool | Returns |
|---|---|
| `get_room_statistics` | Attempts, deaths, time, damage, lowest health, stalls |
| `get_weapon_statistics` | Time equipped, accuracy, kills per weapon |
| `get_enemy_statistics` | What killed the player, and how often |
| `get_death_locations` | Coordinates and cause — feeds a heatmap |
| `get_damage_locations` | Same, for damage taken |
| `get_player_path` | Route through the level, always downsampled |
| `get_stuck_moments` | Where the player stopped progressing, and what was pending |

**Detection**

| Tool | Finds |
|---|---|
| `detect_difficult_rooms` | Rooms weighted by deaths, then damage, time, attempts |
| `detect_easy_rooms` | The opposite problem, which almost nobody measures |
| `detect_repeated_failures` | Gaps, puzzles and waves that fail over and over |
| `detect_unused_mechanics` | Mechanics players never touched |

**Comparison**

| Tool | Compares |
|---|---|
| `compare_sessions` | Two sessions, side by side |
| `compare_versions` | Sessions aggregated by game version |

**Escape hatch**

| Tool | Notes |
|---|---|
| `get_events` | Raw timeline. Event-type filter required, hard result cap |

## Input data

The server reads JSON session files produced by an instrumented game. The data
contract covers session metadata, a computed summary, and a timeline of typed
events carrying world-space positions, room ids and game/level versions.

**Example sessions ship with this repository** so it can be run without the game
that produced them — see `examples/sessions/`.

> Example sessions and the full schema documentation land in phase 3.

## Requirements

- Python 3.11+

No third-party dependencies: the MCP protocol layer is implemented directly over
JSON-RPC 2.0 on stdio.

## Installation

```bash
git clone <this repository>
cd mcp-playtest-analytics
python -m mcp_playtest_analytics
```

> Verified installation instructions land in phase 3, tested from a clean folder.

## Usage with an MCP host

Add it to your host's server configuration as a stdio server:

```jsonc
{
  "playtest": {
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "mcp_playtest_analytics"]
  }
}
```

By default it reads the bundled example sessions, so it works immediately after
cloning.

## License

MIT.
