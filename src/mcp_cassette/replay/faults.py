"""Fault selection and construction for replay.

The :class:`Injector` is consulted by the replay server at exactly one hook point —
after a request is matched, before its response is written — and returns the single
fault (if any) that fires for that occurrence. Cassettes are never mutated; faults live
in a :class:`~mcp_cassette.cassette.FaultOverlay`.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

from ..cassette import Fault, FaultOverlay


class Injector:
    """Selects at most one fault per matched request from an overlay."""

    def __init__(self, overlay: FaultOverlay | None) -> None:
        """Initialize the injector.

        Args:
            overlay: The fault overlay, or ``None`` for no faults.
        """
        self._faults = list(overlay.faults) if overlay else []
        self._match_counts: dict[str, int] = {}
        self._fired: set[int] = set()

    def consult(self, method: str | None) -> Fault | None:
        """Return the fault firing for this matched request, if any.

        Increments the per-method occurrence counter and applies the one-fault-per-
        request rule (first overlay entry wins; a second match warns).

        Args:
            method: The matched request's JSON-RPC method.

        Returns:
            The firing :class:`Fault`, or ``None``.
        """
        if method is None:
            return None
        count = self._match_counts.get(method, 0) + 1
        self._match_counts[method] = count
        candidates = [
            (i, f)
            for i, f in enumerate(self._faults)
            if f.target.method == method
            and (f.target.nth is None or f.target.nth == count)
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            warnings.warn(
                f"mcp-cassette: multiple faults match {method} occurrence #{count}; "
                "only the first overlay entry fires",
                stacklevel=2,
            )
        index, fault = candidates[0]
        self._fired.add(index)
        return fault

    def unused_faults(self) -> list[Fault]:
        """Faults in the overlay that never fired (misconfigured resilience tests)."""
        return [f for i, f in enumerate(self._faults) if i not in self._fired]


def make_error_response(
    msg_id: str | int | None, code: int, message: str
) -> dict[str, Any]:
    """Build a JSON-RPC error response object with the given id."""
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def make_malformed_line(response_obj: dict[str, Any], strategy: str) -> bytes:
    """Corrupt a response per ``strategy`` and return the line bytes to emit.

    Args:
        response_obj: The recorded response object that would otherwise be sent.
        strategy: One of ``truncate``, ``not_json``, ``wrong_id``.

    Returns:
        The corrupted line as bytes (newline included).
    """
    if strategy == "not_json":
        return b"this is not json\n"
    if strategy == "wrong_id":
        corrupted = dict(response_obj)
        corrupted["id"] = "mcp-cassette-unknown-id"
        return (json.dumps(corrupted) + "\n").encode("utf-8")
    # truncate (default): cut a valid serialization mid-payload
    text = json.dumps(response_obj)
    cut = max(1, len(text) // 2)
    return (text[:cut] + "\n").encode("utf-8")
