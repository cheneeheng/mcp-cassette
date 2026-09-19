"""Pattern packs can target the widened surfaces (ITER_03_v4 §02, §04)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_surface_extraction import tools_cassette

from mcp_cassette.cli import main
from mcp_cassette.lint import build_pattern_set, run
from mcp_cassette.lint.packs import ProjectLintConfig


def _pack(tmp_path: Path, surfaces: str | None) -> Path:
    path = tmp_path / "pack.toml"
    line = f"surfaces = {surfaces}\n" if surfaces is not None else ""
    path.write_text(
        'version = 1\n[[patterns]]\nid = "ACME"\nlabel = "vendor"\n'
        f'regex = "acme"\n{line}',
        encoding="utf-8",
    )
    return path


def _pack_locators(tmp_path: Path, pack: Path, tool: dict[str, object]) -> list[str]:
    cassette = tools_cassette(tmp_path / "c.mcp.json", [tool])
    config = ProjectLintConfig(pattern_packs=[pack])
    return [
        f.locator for f in run(cassette, config=config).findings if f.rule == "ACME"
    ]


def test_name_surface_fires_on_name_not_on_identical_description(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path, '["name"]')
    locators = _pack_locators(tmp_path, pack, {"name": "acme", "description": "acme"})
    assert [loc.rsplit("/", 1)[1] for loc in locators] == ["name"]


def test_schema_enum_surface_fires_on_enum_only(tmp_path: Path) -> None:
    pack = _pack(tmp_path, '["schema_enum"]')
    tool = {
        "name": "acme",
        "description": "acme",
        "inputSchema": {"properties": {"v": {"description": "acme", "enum": ["acme"]}}},
    }
    (locator,) = _pack_locators(tmp_path, pack, tool)
    assert locator.endswith("/properties/v/enum/0")


def test_pack_without_surfaces_behaves_as_in_v3(tmp_path: Path) -> None:
    pack = _pack(tmp_path, None)
    locators = _pack_locators(tmp_path, pack, {"name": "acme", "description": "acme"})
    assert [loc.rsplit("/", 1)[1] for loc in locators] == ["description"]


def test_unknown_surface_is_rejected_listing_all_five(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = _pack(tmp_path, '["title"]')
    with pytest.raises(ValueError, match="title"):
        build_pattern_set([pack])
    cassette = tools_cassette(tmp_path / "c.mcp.json", [{"name": "t"}])
    assert (
        main(["lint", str(cassette), "--no-config", "--pattern-pack", str(pack)]) == 2
    )
    err = capsys.readouterr().err
    for surface in (
        "name",
        "description",
        "result",
        "schema_description",
        "schema_enum",
    ):
        assert surface in err
