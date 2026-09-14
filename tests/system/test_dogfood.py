"""The packaging is exercised by the project that ships it (ITER_06_v4 §04)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcp_cassette.cli import main

ROOT = Path(__file__).parents[2]


def _local_lint_hook() -> dict[str, Any]:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text("utf-8"))
    for repo in config["repos"]:
        if repo["repo"] == "local":
            for hook in repo["hooks"]:
                if hook["id"] == "mcp-cassette-lint":
                    return dict(hook)
    raise AssertionError("the repo's own config does not run mcp-cassette-lint")


def test_committed_cassettes_pass_the_repos_own_lint_hook() -> None:
    hook = _local_lint_hook()
    files, exclude = re.compile(hook["files"]), re.compile(hook["exclude"])
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    cassettes = [p for p in tracked if files.search(p) and not exclude.search(p)]
    assert cassettes
    for path in cassettes:
        assert main(["lint", str(ROOT / path)]) == 0, path


def test_published_hooks_match_the_dogfooded_one() -> None:
    published = yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text("utf-8"))
    hooks = {hook["id"]: hook for hook in published}
    assert set(hooks) == {"mcp-cassette-lint", "mcp-cassette-redaction-check"}
    for hook in hooks.values():
        assert hook["files"] == _local_lint_hook()["files"]
        assert hook["language"] == "python"
        assert hook["pass_filenames"] is True
    assert (
        "uv run " + hooks["mcp-cassette-lint"]["entry"] == _local_lint_hook()["entry"]
    )


def test_sdist_ships_the_action_and_hooks_but_not_the_repo_config(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not available")
    dist = tmp_path / "dist"
    subprocess.run(
        [uv, "build", "--sdist", "--out-dir", str(dist)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    (archive,) = dist.glob("*.tar.gz")
    with tarfile.open(archive) as tar:
        names = {name.split("/", 1)[1] for name in tar.getnames() if "/" in name}
    # One word apart: a broadened exclude glob would ship hooks that cannot resolve.
    assert ".pre-commit-hooks.yaml" in names
    assert ".pre-commit-config.yaml" not in names
    assert "action.yml" in names
    assert "scripts/ci_check.sh" in names
