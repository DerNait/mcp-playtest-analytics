"""MCP over JSON-RPC 2.0, on stdio, written by hand.

The server side of the same protocol the host speaks. No MCP SDK: the assignment
awards its extra for exchanging JSON-RPC elements directly, and doing it on both
ends keeps the wire format visible.

Implemented methods:

    initialize        -> capabilities and server info
    tools/list        -> the catalogue
    tools/call        -> run one tool
    ping              -> liveness

Notifications (``notifications/initialized`` among them) are accepted and
answered with silence, which is what the specification requires: a notification
has no ``id`` and must not be replied to.

**Nothing but JSON-RPC goes to stdout.** Diagnostics go to stderr. A stray print
on stdout corrupts the stream and the host's connection dies without an error
message -- the single most common way to break an MCP server.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: Protocol revision we implement. Whatever the client asks for, we answer with
#: ours; hosts negotiate rather than demand, and classmates' hosts will differ.
PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO = {"name": "mcp-playtest-analytics", "version": "0.1.0"}

#: A tool handler takes its arguments and returns text for the model.
ToolHandler = Callable[[dict[str, Any]], str]


class ToolError(Exception):
    """A tool failed in a way the model should see and react to.

    Reported as a normal result with ``isError``, not as a JSON-RPC error: the
    call did happen, it just did not work. A protocol error means something
    else entirely -- that the call never ran.
    """


def log(message: str) -> None:
    """Diagnostics to stderr, never stdout."""
    print(f"[mcp-playtest-analytics] {message}", file=sys.stderr, flush=True)


class Server:
    """A minimal MCP server over newline-delimited JSON on stdio."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._initialized = False

    # ----------------------------------------------------------- registration

    def tool(
        self, name: str, description: str, schema: dict[str, Any]
    ) -> Callable[[ToolHandler], ToolHandler]:
        """Register a tool.

        Schemas are kept deliberately plain -- flat objects, basic types,
        explicit ``required``. Every host has to translate them into its own
        provider's tool format, and the strict modes differ; an exotic schema
        works on one host and is rejected on another.
        """

        def decorator(handler: ToolHandler) -> ToolHandler:
            self._tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": schema,
            }
            self._handlers[name] = handler
            return handler

        return decorator

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    # ------------------------------------------------------------------ loop

    def run(self, stdin: Any = None, stdout: Any = None) -> None:
        """Read messages until the input closes."""
        source = stdin if stdin is not None else sys.stdin
        sink = stdout if stdout is not None else sys.stdout

        log(f"ready, {len(self._tools)} tools")

        for line in source:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._write(sink, _error(None, PARSE_ERROR, f"invalid JSON: {exc}"))
                continue

            response = self.handle(message)
            if response is not None:
                self._write(sink, response)

    @staticmethod
    def _write(sink: Any, message: dict[str, Any]) -> None:
        sink.write(json.dumps(message, ensure_ascii=False) + "\n")
        sink.flush()   # without this the host waits forever on a buffered pipe

    # --------------------------------------------------------------- dispatch

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Handle one message. Returns the response, or None for notifications."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "message must be an object")

        method = message.get("method")
        message_id = message.get("id")

        if method is None:
            return _error(message_id, INVALID_REQUEST, "no method")

        # No id means a notification: act on it, answer nothing.
        if message_id is None:
            if method == "notifications/initialized":
                self._initialized = True
            return None

        params = message.get("params") or {}

        try:
            if method == "initialize":
                return _result(message_id, self._initialize(params))
            if method == "ping":
                return _result(message_id, {})
            if method == "tools/list":
                return _result(message_id, {"tools": list(self._tools.values())})
            if method == "tools/call":
                return _result(message_id, self._call(params))
            return _error(message_id, METHOD_NOT_FOUND, f"unknown method '{method}'")
        except ToolError as exc:
            # A tool failure is a result, not a protocol error.
            return _result(message_id, _text_result(str(exc), is_error=True))
        except Exception as exc:  # pragma: no cover - last line of defence
            log("unhandled error:\n" + traceback.format_exc())
            return _error(message_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client = params.get("clientInfo") or {}
        log(f"initialize from {client.get('name', '?')} {client.get('version', '')}")
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not name:
            raise ToolError("no tool name given")

        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(
                f"unknown tool '{name}'. Available: {', '.join(self.tool_names)}"
            )

        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ToolError("arguments must be an object")

        return _text_result(handler(arguments))


# --------------------------------------------------------------- constructors

def _result(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result
