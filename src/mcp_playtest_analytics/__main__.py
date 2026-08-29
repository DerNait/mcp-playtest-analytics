"""Entry point: python -m mcp_playtest_analytics

Reads sessions from, in order of precedence:
  1. --sessions PATH
  2. PLAYTEST_SESSIONS_DIR
  3. the bundled examples/sessions folder

The default matters: someone who clones this repository and does not have the
game must still get a working server.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .data.loader import SessionStore
from .protocol import Server, log
from .tools import register

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SESSIONS = REPO_ROOT / "examples" / "sessions"


def _has_sessions(folder: Path) -> bool:
    return folder.is_dir() and any(folder.glob("*.json"))


def resolve_sessions_dir(explicit: str | None) -> Path:
    """Pick the sessions folder, falling back to the bundled examples.

    The fallback is what makes `docker run -i --rm <image>` work with no
    arguments: the image sets PLAYTEST_SESSIONS_DIR=/data so that mounting a
    folder there Just Works, but an unmounted run finds /data empty and would
    otherwise report zero sessions. An empty configured folder therefore falls
    back to the examples shipped with the repository.
    """
    if explicit:
        # An explicit flag is an instruction, not a hint: honour it even if empty.
        return Path(explicit).expanduser()

    from_env = os.getenv("PLAYTEST_SESSIONS_DIR")
    if from_env:
        folder = Path(from_env).expanduser()
        if _has_sessions(folder) or not _has_sessions(BUNDLED_SESSIONS):
            return folder
        log(f"{folder} holds no session files; using the bundled examples")
    return BUNDLED_SESSIONS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-playtest-analytics")
    parser.add_argument("--sessions", help="folder holding session JSON files")
    args = parser.parse_args(argv)

    # stdout carries the protocol and nothing else; stderr carries diagnostics.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass

    sessions_dir = resolve_sessions_dir(args.sessions)
    store = SessionStore(sessions_dir)
    store.load_index()

    log(f"sessions folder: {sessions_dir}")
    log(f"{len(store)} files indexed, {sum(1 for _ in store.entries())} playtests")
    if store.unreadable:
        log(f"{len(store.unreadable)} unreadable file(s): "
            + ", ".join(name for name, _ in store.unreadable[:5]))
    if not sessions_dir.is_dir():
        log("warning: that folder does not exist; the server will report zero sessions")

    server = Server()
    register(server, store)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
