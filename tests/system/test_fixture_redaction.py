"""The fixture door resolves redaction packs via pii_packs= (ITER_01_v4 decision 9)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_cassette.cassette import Cassette
from mcp_cassette.redaction import build_redactor

PACK = """version = 1
id = "team"

[[rules]]
id = "customer"
label = "CUSTOMER"
regex = 'CUST-\\d{4}'
"""


def test_marker_pii_packs_fixes_an_unresolvable_pack(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MCP_CASSETTE_MODE", raising=False)
    pack = tmp_path / "team.toml"
    pack.write_text(PACK, encoding="utf-8")
    manifest = build_redactor(pii_packs=[pack]).manifest()
    cassette = tmp_path / "c.mcp.json"
    Cassette(recorded_at=datetime(2026, 9, 1, tzinfo=UTC), redaction=manifest).save(
        cassette
    )
    moved = tmp_path / "moved.toml"
    pack.rename(moved)  # the manifest's recorded path is now stale

    pytester.makepyfile(
        f"""
        import pytest
        from mcp_cassette import CassetteError

        @pytest.mark.mcp_cassette(mode="none", cassette={str(cassette)!r})
        def test_without_pack(mcp_cassette):
            with pytest.raises(CassetteError, match="'team'"):
                mcp_cassette.server_command(["python", "server.py"])

        @pytest.mark.mcp_cassette(
            mode="none", cassette={str(cassette)!r}, pii_packs=[{str(moved)!r}]
        )
        def test_with_pack(mcp_cassette):
            cmd = mcp_cassette.server_command(["python", "server.py"])
            assert cmd[cmd.index("--pii-pack") + 1] == {str(moved)!r}
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=2)
