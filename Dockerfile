# mcp-playtest-analytics
#
# The image exists so that running this server needs nothing but Docker: no
# Python version to match, no virtualenv, no dependency resolution. That is the
# real barrier for someone integrating this server into their own MCP host --
# not the language model they use, which the protocol makes irrelevant.
#
# Build:
#   docker build -t mcp-playtest-analytics .
#
# Run (bundled example sessions, works with no extra setup):
#   docker run -i --rm mcp-playtest-analytics
#
# Run against your own sessions folder, mounted read-only:
#   docker run -i --rm -v "/path/to/PlaytestSessions:/data:ro" mcp-playtest-analytics
#
# The -i is required: MCP over stdio needs stdin held open. Do NOT pass -t --
# a TTY mangles the newline-delimited JSON stream.

FROM python:3.12-slim

# The server has no third-party dependencies on purpose: the MCP protocol is
# implemented directly over JSON-RPC 2.0, so there is nothing to install.

WORKDIR /app

COPY src/ /app/src/
COPY examples/ /app/examples/

# Unbuffered, or responses sit in the pipe and the host waits forever.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# A mounted folder wins; without one, the bundled example sessions are used.
ENV PLAYTEST_SESSIONS_DIR=/data

# Present so that `-v ...:/data:ro` has somewhere to land, and so that an
# unmounted run finds an empty directory rather than a missing one.
RUN mkdir -p /data

# Run as a non-root user: this process reads a folder the host mounts into it.
RUN useradd --create-home --uid 10001 mcp && chown -R mcp:mcp /app /data
USER mcp

ENTRYPOINT ["python", "-m", "mcp_playtest_analytics"]
