"""Server-initiated request replay: emission plan and release gating.

Transport-neutral. At load, every recorded server-to-client request (sampling,
elicitation) gets an emission trigger derived from its recorded position; at runtime
the tracker holds pending-response state and implements release-on-response gating:
messages recorded *after* the original recorded response to a server request are held
until the live agent has responded, because the real server only produced them after
being answered. The agent's response is accepted whatever its content — success or
error alike — and never matched against the recording.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio

from ..cassette import Cassette, Message

logger = logging.getLogger("mcp_cassette")

Trigger = Literal["during", "after", "initialize"]


@dataclass
class ServerRequestState:
    """Runtime state for one recorded server-to-client request.

    Attributes:
        message: The recorded request message (emitted with its recorded ``msg_id``).
        trigger: When to emit — ``during`` (inside its exchange, before the gated
            response), ``after`` (after the anchor exchange's response, like an
            anchored notification), or ``initialize`` (free-floating).
        anchor_seq: Seq of the anchoring client request (``None`` for free-floating).
        response_seq: Seq of the recorded client response to it, if one was recorded
            (messages after this seq are gated until the live agent responds).
        emitted: Whether replay has emitted this request to the live agent.
        answered: Set once the live agent has responded (any content).
    """

    message: Message
    trigger: Trigger
    anchor_seq: int | None
    response_seq: int | None
    emitted: bool = False
    answered: anyio.Event = field(default_factory=anyio.Event)


class ServerRequestTracker:
    """Builds the emission plan at load and tracks pending-response state."""

    def __init__(self, cassette: Cassette) -> None:
        """Compute triggers and recorded-response positions for every server request.

        Args:
            cassette: The loaded cassette (any transport, any format version).
        """
        self._states: list[ServerRequestState] = []
        requests = [
            m for m in cassette.messages if m.sender == "client" and m.kind == "request"
        ]
        response_seq_by_id: dict[str | int, int] = {}
        for m in cassette.messages:
            if (
                m.sender == "server"
                and m.kind == "response"
                and m.msg_id is not None
                and m.msg_id not in response_seq_by_id
            ):
                response_seq_by_id[m.msg_id] = m.seq
        client_response_seq: dict[str | int, int] = {}
        for m in cassette.messages:
            if (
                m.sender == "client"
                and m.kind == "response"
                and m.msg_id is not None
                and m.msg_id not in client_response_seq
            ):
                client_response_seq[m.msg_id] = m.seq
        for m in cassette.messages:
            if not (m.sender == "server" and m.kind == "request"):
                continue
            anchor = None
            for req in requests:
                if req.seq < m.seq:
                    anchor = req
                else:
                    break
            trigger: Trigger
            if anchor is None:
                trigger = "initialize"
                anchor_seq = None
            else:
                anchor_seq = anchor.seq
                anchor_response_seq = (
                    response_seq_by_id.get(anchor.msg_id)
                    if anchor.msg_id is not None
                    else None
                )
                inside = anchor_response_seq is not None and m.seq < anchor_response_seq
                trigger = "during" if inside else "after"
            response_seq = (
                client_response_seq.get(m.msg_id) if m.msg_id is not None else None
            )
            self._states.append(
                ServerRequestState(
                    message=m,
                    trigger=trigger,
                    anchor_seq=anchor_seq,
                    response_seq=response_seq,
                )
            )

    @property
    def has_server_requests(self) -> bool:
        """Whether the cassette contains any server-initiated request."""
        return bool(self._states)

    def triggered_by(
        self, anchor_seq: int | None, trigger: Trigger
    ) -> list[ServerRequestState]:
        """States to emit for the given anchor and trigger point, in seq order."""
        return [
            s
            for s in self._states
            if s.trigger == trigger and s.anchor_seq == anchor_seq and not s.emitted
        ]

    def state_for_seq(self, seq: int) -> ServerRequestState | None:
        """The state whose recorded message has this seq, if any."""
        for s in self._states:
            if s.message.seq == seq:
                return s
        return None

    def mark_emitted(self, state: ServerRequestState) -> None:
        """Record that the request was emitted; it is now pending a response."""
        state.emitted = True

    def on_client_message(self, obj: dict[str, Any]) -> bool:
        """Consume a client response to an emitted server request, if it is one.

        Accept-anything: the response is accepted whatever its content — success or
        error alike (the answer comes from the agent's LLM or user and legitimately
        differs every run). It is logged at debug level and never matched against
        the recorded response.

        Args:
            obj: A decoded client JSON-RPC object.

        Returns:
            True if the object answered a pending server request (the caller should
            not process it further).
        """
        if "id" not in obj or obj.get("method") is not None:
            return False
        msg_id = obj.get("id")
        for s in self._states:
            if s.emitted and not s.answered.is_set() and s.message.msg_id == msg_id:
                logger.debug(
                    "mcp-cassette: agent answered server request %s id=%r",
                    s.message.method,
                    msg_id,
                )
                s.answered.set()
                return True
        return False

    async def wait_ready(self, seq: int, exchange: int | None) -> None:
        """Block until every gate covering the message at ``seq`` is open.

        A gate covers ``seq`` when an emitted-but-unanswered server request has a
        recorded response at an earlier seq — scoped to the same exchange over HTTP,
        global over stdio (``exchange`` fields all ``None``). No internal timeout:
        if the agent never responds, the gated messages never release (pytest's own
        timeout applies; a default here would mask real agent bugs).
        """
        while True:
            blocker = self._find_blocker(seq, exchange)
            if blocker is None:
                return
            await blocker.answered.wait()

    def would_block(self, seq: int, exchange: int | None) -> bool:
        """Whether :meth:`wait_ready` would currently block for this message."""
        return self._find_blocker(seq, exchange) is not None

    def _find_blocker(
        self, seq: int, exchange: int | None
    ) -> ServerRequestState | None:
        for s in self._states:
            if not s.emitted or s.answered.is_set() or s.response_seq is None:
                continue
            if seq <= s.response_seq:
                continue
            scope = s.message.exchange
            if scope is None or scope == exchange:
                return s
        return None

    def pending_summaries(self) -> list[str]:
        """Emitted-but-unanswered server requests, for the shutdown summary."""
        return [
            f"{s.message.method} id={s.message.msg_id!r} still awaiting the agent's "
            "response"
            for s in self._states
            if s.emitted and not s.answered.is_set()
        ]
