"""Session recording: classify wire lines into cassette messages.

The recorder is the tap behind the proxy pumps. It classifies each newline-delimited
line from JSON-RPC shape alone, numbers it, timestamps it against a monotonic clock,
watches the ``initialize`` exchange for session metadata, and applies redaction at write
time to a deep copy (traffic in flight is never touched).
"""

from __future__ import annotations

import json
import time
import warnings
from datetime import UTC, datetime
from typing import Any

from ..cassette import (
    Cassette,
    Channel,
    Message,
    RedactionRule,
    Sender,
    ServerInfo,
    Transport,
    apply_redactions,
)


class SessionRecorder:
    """Accumulates classified messages for one MCP stdio session.

    The tap callbacks are synchronous and run to completion between event-loop yields,
    so under anyio's cooperative scheduler ``seq`` stays strictly ordered without an
    explicit lock (a sync callback cannot be preempted mid-classification).
    """

    def __init__(self, redaction_rules: list[RedactionRule] | None = None) -> None:
        """Initialize the recorder.

        Args:
            redaction_rules: Rules applied to each payload at capture time. Defaults to
                an empty list (caller usually passes defaults + user rules).
        """
        self._rules = redaction_rules or []
        self._messages: list[Message] = []
        self._seq = 0
        self._start = time.monotonic()
        self._initialize_request_id: str | int | None = None
        self._protocol_version: str | None = None
        self._server_info: ServerInfo | None = None
        self._warned_raw = False

    def on_line(self, sender: Sender, line: bytes) -> None:
        """Classify and buffer one wire line.

        Args:
            sender: Which side emitted the line.
            line: The raw line bytes (newline included).
        """
        text = line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        self.on_message(sender, text)

    def on_message(
        self,
        sender: Sender,
        text: str,
        *,
        exchange: int | None = None,
        channel: Channel | None = None,
    ) -> None:
        """Classify and buffer one wire message given as decoded text.

        Classification is identical for every transport — JSON-RPC shape only.

        Args:
            sender: Which side emitted the message.
            text: The message text (one JSON-RPC object, or junk recorded as raw).
            exchange: HTTP exchange number (``None`` for stdio).
            channel: HTTP stream that carried a server message (``None`` otherwise).
        """
        offset_ms = int((time.monotonic() - self._start) * 1000)
        if not text.strip():
            return
        obj = self._try_decode(text)
        if obj is None:
            self._append(sender, "raw", None, None, text, offset_ms, exchange, channel)
            return

        method = obj.get("method")
        has_id = "id" in obj
        msg_id = obj.get("id")
        if method is not None and has_id:
            kind = "request"
        elif method is not None:
            kind = "notification"
        elif has_id:
            kind = "response"
        else:
            self._append(sender, "raw", None, None, text, offset_ms, exchange, channel)
            return

        self._watch_initialize(sender, kind, method, msg_id, obj)
        self._append(sender, kind, method, msg_id, obj, offset_ms, exchange, channel)

    def build(
        self,
        *,
        transport: Transport = "stdio",
        server_url: str | None = None,
        session_id: str | None = None,
    ) -> Cassette:
        """Materialize the buffered session into a :class:`Cassette`.

        Args:
            transport: The transport the session traveled over.
            server_url: The remote server URL (http only; provenance).
            session_id: The server's ``Mcp-Session-Id`` (http only; evidence).
        """
        return Cassette(
            recorded_at=datetime.now(UTC),
            transport=transport,
            server_url=server_url,
            session_id=session_id,
            protocol_version=self._protocol_version,
            server_info=self._server_info,
            messages=list(self._messages),
        )

    @property
    def message_count(self) -> int:
        """Number of messages captured so far."""
        return len(self._messages)

    def _append(
        self,
        sender: Sender,
        kind: str,
        method: str | None,
        msg_id: str | int | None,
        payload: dict[str, Any] | str,
        offset_ms: int,
        exchange: int | None = None,
        channel: Channel | None = None,
    ) -> None:
        redacted_payload, changed = apply_redactions(payload, self._rules)
        self._messages.append(
            Message(
                seq=self._seq,
                t_offset_ms=offset_ms,
                sender=sender,
                kind=kind,  # type: ignore[arg-type]  # validated by pydantic Literal
                method=method,
                msg_id=msg_id,
                payload=redacted_payload,
                redacted=changed,
                exchange=exchange,
                channel=channel,
            )
        )
        self._seq += 1

    def _try_decode(self, text: str) -> dict[str, Any] | None:
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            if not self._warned_raw:
                warnings.warn(
                    "mcp-cassette: non-JSON line on the wire recorded as kind='raw'",
                    stacklevel=2,
                )
                self._warned_raw = True
            return None
        if not isinstance(decoded, dict):
            return None
        return decoded

    def _watch_initialize(
        self,
        sender: Sender,
        kind: str,
        method: str | None,
        msg_id: str | int | None,
        obj: dict[str, Any],
    ) -> None:
        if sender == "client" and kind == "request" and method == "initialize":
            self._initialize_request_id = msg_id
            return
        if (
            sender == "server"
            and kind == "response"
            and self._initialize_request_id is not None
            and msg_id == self._initialize_request_id
        ):
            result = obj.get("result")
            if isinstance(result, dict):
                pv = result.get("protocolVersion")
                if isinstance(pv, str):
                    self._protocol_version = pv
                info = result.get("serverInfo")
                if isinstance(info, dict) and "name" in info and "version" in info:
                    self._server_info = ServerInfo(
                        name=str(info["name"]), version=str(info["version"])
                    )
