# mcp-playtest-analytics

An MCP server that turns raw playtesting telemetry into answers about game design.

Point it at a folder of session files from an instrumented game and ask which
room was hardest, which weapon players actually used, where they died, or whether
the change between two builds helped. The server does the arithmetic and returns
aggregates; the language model interprets them.

Built for **CC3067 Redes**, Universidad del Valle de Guatemala.

## Quick start

**With Docker — nothing else to install:**

```bash
docker build -t mcp-playtest-analytics .
docker run -i --rm mcp-playtest-analytics
```

That runs against the eleven real playtest sessions bundled in
`examples/sessions/`, so it works immediately after cloning. They span three
game versions and include runs with deaths and runs without, which is what
makes the detection and comparison tools show something on a fresh clone.

**With Python 3.11+ instead:**

```bash
PYTHONPATH=src python -m mcp_playtest_analytics
```

There are **no third-party dependencies**. The MCP protocol is implemented
directly over JSON-RPC 2.0, so there is nothing to install.

## Adding it to an MCP host

Any host works — the protocol is independent of the language model behind it.

```jsonc
{
  "playtest": {
    "command": "docker",
    "args": ["run", "-i", "--rm", "mcp-playtest-analytics"]
  }
}
```

`-i` is required: MCP over stdio needs stdin held open. Do **not** pass `-t`; a
TTY mangles the newline-delimited JSON stream.

Native equivalent:

```jsonc
{
  "playtest": {
    "command": "python",
    "args": ["-m", "mcp_playtest_analytics"],
    "env": { "PYTHONPATH": "/absolute/path/to/mcp-playtest-analytics/src" }
  }
}
```

## Using your own sessions

Mount a folder read-only at `/data`:

```bash
docker run -i --rm -v "/path/to/PlaytestSessions:/data:ro" mcp-playtest-analytics
```

Or, running natively, `--sessions /path/to/folder` or the
`PLAYTEST_SESSIONS_DIR` environment variable. If the configured folder holds no
session files, the server falls back to the bundled examples.

## Tools

### Discovery

| Tool | Arguments | Returns |
|---|---|---|
| `list_sessions` | `limit`, `game_version`, `level_version`, `include_automated` | One compact row per session: id, player, date, duration, versions, result, deaths, rooms completed |
| `get_session_summary` | `session_id` | The full computed summary: totals, per-room, per-weapon and per-enemy rows, unused mechanics |

### Single-session analysis

| Tool | Arguments | Returns |
|---|---|---|
| `get_room_statistics` | `session_id`, `room_id` | Attempts, deaths, time, clear time, damage, lowest health, time in danger |
| `get_weapon_statistics` | `session_id` | Time equipped, attacks, hits, accuracy, damage, kills, average hit distance |
| `get_enemy_statistics` | `session_id` | Spawned, killed, damage dealt to the player, player kills, average time to kill |
| `get_death_locations` | `session_id` (optional) | World coordinates, room, killer and weapon held, per death |
| `get_damage_locations` | `session_id`, `min_amount` | Where damage was taken, plus a per-room total |
| `get_player_path` | `session_id`, `room_id`, `max_points` | The route taken, **always downsampled**, capped at 200 points |
| `get_stuck_moments` | `session_id` (optional) | Where a player stopped progressing, for how long, and what the room was still waiting for |

### Detection

| Tool | Arguments | Returns |
|---|---|---|
| `detect_difficult_rooms` | `game_version` | Rooms ranked by trouble caused, weighting deaths above damage, retries and time |
| `detect_easy_rooms` | `game_version` | Rooms that cost the player nothing, with never-visited rooms listed separately |
| `detect_repeated_failures` | `session_id` (optional) | Gaps, puzzles, switch sequences and timed gates that failed more than once |
| `detect_unused_mechanics` | `game_version` | Mechanics nobody used, and first-use timings for the rest |

### Comparison

| Tool | Arguments | Returns |
|---|---|---|
| `compare_sessions` | `session_a`, `session_b` | Two sessions side by side, including per-room time deltas |
| `compare_versions` | `version_a`, `version_b` | Both versions pooled: duration, deaths, damage, accuracy, weapon usage share — with a warning when the sample is too thin to conclude anything |

### Escape hatch

| Tool | Arguments | Returns |
|---|---|---|
| `get_events` | `session_id`, **`event_types` (required)**, `room`, `from_second`, `to_second`, `limit` | Raw timeline events. The type filter is mandatory and results are capped at 100 — a session holds thousands of events, and returning them whole is what every other tool exists to avoid |

## Design

**No tool ever returns a full session.** A real session file is around 1.5 MB,
mostly position sampling — roughly 200-300k tokens if handed to a model whole.
That fills the context window, costs real money, and leaves the model doing
arithmetic by hand, which is what it is worst at.

So the server keeps two layers: an **index** built from each file's header and
computed summary, which answers most questions without opening a single
timeline; and **on-demand loading** of the event array for the questions that
genuinely need positions or raw events.

**Automated runs are excluded by default.** The corpus these tools were built
against holds 130 files, of which only 4 are real playtests: 55 are automated
test-harness runs and the rest are launches that lasted under a second.
Averaging them together produces confident statistics about nothing. The
discriminator is semantic — harness runs spawn a marker enemy — because file
size points the wrong way: harness runs produce the *largest* files in the
corpus.

**Small samples are reported as small.** `compare_versions` attaches a caution
when either side has fewer than three sessions, so the model says "anecdote"
instead of "finding".

## Tests

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

They cover the harness/playtest discrimination, which is the logic most worth
pinning down: the obvious heuristic is backwards, and getting it wrong makes
every statistic the server reports confidently meaningless.

## Example

```
> Compare version 0.2.0 with 0.3.0. What changed in weapon usage and accuracy?

  [tool] compare_versions(version_a='0.2.0', version_b='0.3.0')

  Sword usage went from 15.7% to 22.4%, accuracy from 0.34 to 0.58.
  But there are only 2 playtests for 0.2.0 and 1 for 0.3.0, so this is
  an anecdote rather than evidence...
```

## Input format

Sessions are JSON files with a header (ids, versions, duration, result), a
computed `summary`, and an `events` array of typed events carrying world-space
positions, room ids and version stamps. The files in `examples/sessions/` are
real recorded playtests and serve as the reference for the format.

## License

MIT.
