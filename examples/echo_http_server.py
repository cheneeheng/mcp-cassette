"""The echo server exposed over Streamable HTTP, pure standard library.

A single ``POST /mcp`` endpoint in JSON response mode: each JSON-RPC request gets an
``application/json`` response, notifications get ``202 Accepted``. This is the
"remote MCP server" for the HTTP examples — mcp-cassette's recording proxy sits in
front of it and its cassette replays on a local mock server with the same shape.

SSE response mode and server-initiated requests are deliberately out of scope here
(the ``summarize`` tool answers with an error over this transport); the point of the
example is URL substitution, not a full server.

Run it::

    python examples/echo_http_server.py --port 8901
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from echo_server import handle


class McpRequestHandler(BaseHTTPRequestHandler):
    """Serve ``POST /mcp`` by delegating to :func:`echo_server.handle`."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802  (http.server's fixed naming)
        """Answer one JSON-RPC message: JSON body for requests, 202 otherwise."""
        if self.path != "/mcp":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length))
        response = handle(request)
        if response is None:
            self.send_response(202)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence per-request logging; the examples read this server's output."""


def main() -> None:
    """Serve ``/mcp`` on 127.0.0.1 until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8901)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), McpRequestHandler).serve_forever()


if __name__ == "__main__":
    main()
