"""resolve_mode validates before consulting the environment (ITER_04_v4 decision 8)."""

from __future__ import annotations

import pytest

from mcp_cassette import resolve_mode


@pytest.mark.parametrize("env", [None, "none", "all"])
def test_invalid_argument_raises_whatever_the_environment(
    monkeypatch: pytest.MonkeyPatch, env: str | None
) -> None:
    if env is None:
        monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    else:
        monkeypatch.setenv("MCP_CASSETTE_MODE", env)
    with pytest.raises(ValueError, match="mode= argument"):
        resolve_mode("nope")


@pytest.mark.parametrize(
    ("env", "explicit", "expected"),
    [
        (None, None, "once"),
        (None, "all", "all"),
        ("none", None, "none"),
        ("none", "all", "none"),
        ("new_episodes", "once", "new_episodes"),
    ],
)
def test_valid_precedence_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    env: str | None,
    explicit: str | None,
    expected: str,
) -> None:
    if env is None:
        monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    else:
        monkeypatch.setenv("MCP_CASSETTE_MODE", env)
    assert resolve_mode(explicit) == expected
