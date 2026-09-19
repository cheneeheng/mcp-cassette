"""The Action's shell logic, tested directly (ITER_06_v4 §04).

A composite action cannot be exercised through YAML alone, so its steps live in
``scripts/ci_check.sh`` and run here against throwaway git repositories.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "ci_check.sh"
EXAMPLES = ROOT / "examples" / "cassettes"
CASSETTE = "cassettes/tools.mcp.json"


def _bash() -> str:
    # On Windows, System32\bash.exe is WSL and cannot see this venv; use Git Bash.
    git = shutil.which("git")
    if sys.platform == "win32" and git is not None:
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    found = shutil.which("bash")
    if found is None:
        pytest.skip("bash is not available")
    return found


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # The script calls mcp-cassette by name, as the Action's uv tool install does.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    env.pop("GITHUB_ACTIONS", None)
    env.pop("GITHUB_STEP_SUMMARY", None)
    env.pop("GITHUB_OUTPUT", None)
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        env[f"{key}_NAME"] = "test"
        env[f"{key}_EMAIL"] = "test@example.com"
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, source: str | None, message: str) -> str:
    if source is not None:
        target = repo / CASSETTE
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLES / source, target)
    else:
        (repo / "README.txt").write_text(message, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _init(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _check(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(SCRIPT), *args],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
    )


def test_modified_tool_description_fails_naming_the_tool(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "tools.mcp.json", "baseline")
    _commit(repo, "tools-v2.mcp.json", "drift")
    result = _check(repo, "--baseline-ref", base, "--checks", "lint,diff", CASSETTE)
    assert result.returncode == 4, result.stdout + result.stderr
    r002 = [line for line in result.stdout.splitlines() if "title=R002" in line]
    assert r002, result.stdout
    assert any('tool "' in line for line in r002)


def test_shallow_checkout_exits_2_naming_fetch_depth(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, "tools.mcp.json", "baseline")
    _commit(repo, "tools-v2.mcp.json", "drift")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)],
        env=_env(),
        check=True,
        capture_output=True,
    )
    result = _check(shallow, "--baseline-ref", base, CASSETTE)
    assert result.returncode == 2
    assert "fetch-depth: 0" in result.stderr


def test_cassette_new_on_the_branch_is_skipped_with_a_note(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    base = _commit(repo, None, "before any cassette")
    _commit(repo, "tools.mcp.json", "add a cassette")
    result = _check(repo, "--baseline-ref", base, "--checks", "lint,diff", CASSETTE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"note: {CASSETTE} is new since {base}" in result.stdout
