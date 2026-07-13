"""Request matching for replay.

Turns a cassette's recorded messages into ordered exchanges (client request, its server
response, and the server notifications anchored to it) and matches incoming client
requests against them per a :class:`MatchConfig`. Matching is structural over parsed
JSON; the JSON-RPC ``id`` is never matched on and is re-stamped by the server.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from .cassette import Cassette, MatchConfig, Message


@dataclass
class Exchange:
    """A recorded client request together with its response and anchored notifications.

    Attributes:
        request: The recorded client request message.
        response: The matching server response message, if one was recorded.
        notifications: Server notifications recorded between the request and its
            response, replayed immediately after the response.
        key: Canonical match key computed from the active :class:`MatchConfig`.
        consumed: Whether this exchange has been used (per-method / strict ordering).
    """

    request: Message
    response: Message | None
    notifications: list[Message] = field(default_factory=list)
    key: str = ""
    consumed: bool = False


def detect_server_initiated_requests(cassette: Cassette) -> bool:
    """Return True if the cassette contains any server-to-client request.

    Server-initiated requests (sampling/elicitation) are recorded generically but not
    replayable in the MVP; :class:`~mcp_cassette.replay.server.ReplayServer` refuses
    such cassettes at load.
    """
    return any(
        m.sender == "server" and m.kind == "request" for m in cassette.messages
    )


class Matcher:
    """Matches incoming replay requests against a cassette's recorded exchanges."""

    def __init__(self, cassette: Cassette, config: MatchConfig | None = None) -> None:
        """Build lookup structures once from the cassette.

        Args:
            cassette: The loaded cassette to replay.
            config: Matching configuration; defaults to :class:`MatchConfig` defaults.
        """
        self.config = config or MatchConfig()
        self._exchanges = self._build_exchanges(cassette)
        self._misses: list[str] = []

    @property
    def leading_notifications(self) -> list[Message]:
        """Server notifications recorded before any client request (post-initialize)."""
        return self._leading_notifications

    @property
    def misses(self) -> list[str]:
        """Human-readable summaries of unmatched requests seen so far."""
        return list(self._misses)

    def find(self, request_obj: dict[str, Any]) -> Exchange | None:
        """Find the recorded exchange for an incoming client request.

        Args:
            request_obj: The decoded incoming JSON-RPC request object.

        Returns:
            The matching :class:`Exchange`, or ``None`` if nothing matches (the miss is
            recorded).
        """
        key = self._canonical_key(request_obj)
        match = self._lookup(key)
        if match is None:
            method = request_obj.get("method", "<none>")
            digest = self._params_digest(request_obj)
            self._misses.append(f"{method} params={digest}")
        return match

    def record_miss(self, summary: str) -> None:
        """Record an externally-detected miss summary."""
        self._misses.append(summary)

    def _lookup(self, key: str) -> Exchange | None:
        ordering = self.config.ordering
        if ordering == "none":
            for ex in self._exchanges:
                if ex.key == key:
                    return ex
            return None
        if ordering == "strict":
            for ex in self._exchanges:
                if ex.consumed:
                    continue
                if ex.key == key:
                    ex.consumed = True
                    return ex
                return None  # next-in-line did not match -> unmatched
            return None
        # per_method (default): earliest unconsumed exchange with the same key
        for ex in self._exchanges:
            if not ex.consumed and ex.key == key:
                ex.consumed = True
                return ex
        return None

    def _build_exchanges(self, cassette: Cassette) -> list[Exchange]:
        messages = cassette.messages
        response_by_id: dict[Any, Message] = {
            m.msg_id: m
            for m in messages
            if m.sender == "server" and m.kind == "response" and m.msg_id is not None
        }
        exchanges: list[Exchange] = []
        self._leading_notifications: list[Message] = []
        seen_request = False
        pending_notifications: list[Message] = []
        for m in messages:
            if m.sender == "server" and m.kind == "notification":
                if seen_request:
                    pending_notifications.append(m)
                else:
                    self._leading_notifications.append(m)
                continue
            if m.sender == "client" and m.kind == "request":
                seen_request = True
                if exchanges and pending_notifications:
                    exchanges[-1].notifications.extend(pending_notifications)
                    pending_notifications = []
                response = response_by_id.get(m.msg_id)
                ex = Exchange(
                    request=m,
                    response=response,
                    key=self._canonical_key(_payload_obj(m)),
                )
                exchanges.append(ex)
        if exchanges and pending_notifications:
            exchanges[-1].notifications.extend(pending_notifications)
        return exchanges

    def _canonical_key(self, obj: dict[str, Any]) -> str:
        clone = copy.deepcopy(obj)
        for ptr in self.config.ignore_params:
            _delete_pointer(clone, ptr)
        components = {name: clone.get(name) for name in self.config.match_on}
        return json.dumps(components, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _params_digest(obj: dict[str, Any]) -> str:
        params = obj.get("params")
        text = json.dumps(params, sort_keys=True, separators=(",", ":"))
        return text if len(text) <= 120 else text[:117] + "..."


def _payload_obj(message: Message) -> dict[str, Any]:
    if isinstance(message.payload, dict):
        return message.payload
    return {}


def _delete_pointer(root: Any, pointer: str) -> None:
    if not pointer.startswith("/"):
        # Bare token: treat as a top-level key for convenience.
        if isinstance(root, dict):
            root.pop(pointer, None)
        return
    tokens = [
        p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]
    ]
    if not tokens:
        return
    node = root
    for token in tokens[:-1]:
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and _is_index(token, len(node)):
            node = node[int(token)]
        else:
            return
    last = tokens[-1]
    if isinstance(node, dict):
        node.pop(last, None)
    elif isinstance(node, list) and _is_index(last, len(node)):
        del node[int(last)]


def _is_index(token: str, length: int) -> bool:
    try:
        idx = int(token)
    except ValueError:
        return False
    return 0 <= idx < length
