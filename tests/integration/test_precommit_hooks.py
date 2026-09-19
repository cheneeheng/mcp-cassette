"""The published pre-commit hooks, installed into a throwaway repo (ITER_06_v4 §04).

``pre-commit try-repo`` cannot pass a hook's ``args``, and the redaction check needs
one, so the hooks are consumed the way a real user consumes them: a hook source
repository at a commit, a consumer ``.pre-commit-config.yaml`` pinning that commit,
``pre-commit install``, and a real ``git commit``. Installing the hook environment
builds this project from source, so the first test pays for a package install.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "examples" / "cassettes"


def _env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PRE_COMMIT_HOME"] = str(home)
    env.pop("MCP_CASSETTE_MODE", None)
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        env[f"{key}_NAME"] = "test"
        env[f"{key}_EMAIL"] = "test@example.com"
    return env


def _run(
    cmd: list[str], cwd: Path, home: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, env=_env(home), check=check, capture_output=True, text=True
    )


def _git_init(repo: Path, home: Path) -> None:
    repo.mkdir(parents=True)
    _run(["git", "init", "-q"], repo, home)
    _run(["git", "config", "commit.gpgsign", "false"], repo, home)


@pytest.fixture(scope="module")
def hook_source(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, Path]:
    """A committed copy of the files a hook install needs; returns (repo, rev, home)."""
    base = tmp_path_factory.mktemp("hooks")
    home = base / "pre-commit-home"
    source = base / "source"
    _git_init(source, home)
    shutil.copytree(
        ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    for name in ("pyproject.toml", "README.md", "LICENSE", ".pre-commit-hooks.yaml"):
        shutil.copy(ROOT / name, source / name)
    _run(["git", "add", "-A"], source, home)
    _run(["git", "commit", "-q", "-m", "hooks"], source, home)
    rev = _run(["git", "rev-parse", "HEAD"], source, home).stdout.strip()
    return source, rev, home


def _consumer(tmp_path: Path, hook_source: tuple[Path, str, Path]) -> Path:
    source, rev, home = hook_source
    repo = tmp_path / "consumer"
    _git_init(repo, home)
    (repo / ".pre-commit-config.yaml").write_text(
        f"""repos:
  - repo: {source.as_posix()}
    rev: {rev}
    hooks:
      - id: mcp-cassette-lint
      - id: mcp-cassette-redaction-check
        args: [team-baseline]
""",
        encoding="utf-8",
    )
    _run([sys.executable, "-m", "pre_commit", "install"], repo, home)
    return repo


def _commit(repo: Path, home: Path, name: str, content: bytes) -> tuple[int, str]:
    """Commit one file; returns the exit code and the combined output.

    git routes hook output to stderr, so both streams are read together.
    """
    (repo / name).write_bytes(content)
    _run(["git", "add", name], repo, home)
    result = _run(["git", "commit", "-m", f"add {name}"], repo, home, check=False)
    return result.returncode, result.stdout + result.stderr


def test_non_cassette_file_runs_neither_hook(
    tmp_path: Path, hook_source: tuple[Path, str, Path]
) -> None:
    repo = _consumer(tmp_path, hook_source)
    code, output = _commit(repo, hook_source[2], "notes.txt", b"hello\n")
    assert code == 0, output
    assert output.count("(no files to check)Skipped") == 2


def test_planted_injection_is_refused_with_exit_4(
    tmp_path: Path, hook_source: tuple[Path, str, Path]
) -> None:
    repo = _consumer(tmp_path, hook_source)
    planted = (EXAMPLES / "tools-v2.mcp.json").read_bytes()
    code, output = _commit(repo, hook_source[2], "planted.mcp.json", planted)
    assert code != 0
    assert "R001" in output
    assert "exit code: 4" in output


def test_cassette_without_a_manifest_is_refused_by_the_redaction_check(
    tmp_path: Path, hook_source: tuple[Path, str, Path]
) -> None:
    repo = _consumer(tmp_path, hook_source)
    clean = (EXAMPLES / "tools.mcp.json").read_bytes()
    code, output = _commit(repo, hook_source[2], "clean.mcp.json", clean)
    assert code != 0
    assert "carries no redaction manifest" in output
    # The lint hook itself passes: only the redaction check refuses this cassette.
    lint_line = next(
        line for line in output.splitlines() if line.startswith("mcp-cassette lint.")
    )
    assert lint_line.endswith("Passed")


def test_clean_scrubbed_cassette_commits(
    tmp_path: Path, hook_source: tuple[Path, str, Path]
) -> None:
    repo = _consumer(tmp_path, hook_source)
    data = json.loads((EXAMPLES / "tools.mcp.json").read_text(encoding="utf-8"))
    # Stamp the manifest a `record --redact-profile team-baseline` run would write.
    data["format_version"] = 3
    data["redaction"] = {
        "profile": "team-baseline",
        "backends": ["structural"],
        "applied_at": "2026-09-14T00:00:00Z",
    }
    scrubbed = json.dumps(data).encode("utf-8")
    code, output = _commit(repo, hook_source[2], "clean.mcp.json", scrubbed)
    assert code == 0, output
