"""``lint --annotate github`` workflow commands (ITER_06_v4 §04)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from mcp_cassette.cli import main

EXAMPLES = Path(__file__).parents[2] / "examples" / "cassettes"
API_KEY = "q8N2vR5xL1mZ7pK4tW9yB3cF6hJ0dG2sA5eU8iO1"


def _escape_property(value: str) -> str:
    for raw, escaped in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        value = value.replace(raw, escaped)
    return value.replace(":", "%3A").replace(",", "%2C")


def _split(stdout: str) -> tuple[list[str], str]:
    lines = stdout.splitlines()
    annotations = [line for line in lines if line.startswith("::")]
    rest = "\n".join(line for line in lines if not line.startswith("::"))
    return annotations, rest


def test_one_command_per_finding_with_severity_and_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cassette = tmp_path / "tools-v2.mcp.json"
    shutil.copy(EXAMPLES / "tools-v2.mcp.json", cassette)
    code = main(
        [
            "lint",
            str(cassette),
            "--no-config",
            "--format",
            "json",
            "--annotate",
            "github",
        ]
    )
    assert code == 4
    annotations, document = _split(capsys.readouterr().out)
    findings = json.loads(document)["findings"]
    assert findings, "tools-v2 is the deliberately drifted fixture"
    # --annotate composes with --format: the JSON document is still intact.
    assert len(annotations) == len(findings)
    for line, finding in zip(annotations, findings, strict=True):
        prefix = (
            f"::{finding['severity']} file={_escape_property(str(cassette))},"
            f"title={finding['rule']}::"
        )
        assert line.startswith(prefix)
        assert line[len(prefix) :].startswith(f"{finding['rule']} {finding['locator']}")


def test_no_findings_emit_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cassette = tmp_path / "tools.mcp.json"
    shutil.copy(EXAMPLES / "tools.mcp.json", cassette)
    assert main(["lint", str(cassette), "--no-config", "--annotate", "github"]) == 0
    annotations, _ = _split(capsys.readouterr().out)
    assert annotations == []


def _plant(node: Any) -> bool:
    """Append the key to the first text block found under a result."""
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            node["text"] += f" key {API_KEY}"
            return True
        return any(_plant(value) for value in node.values())
    if isinstance(node, list):
        return any(_plant(item) for item in node)
    return False


def test_annotation_text_carries_no_secret_material(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads((EXAMPLES / "echo_and_add.mcp.json").read_text(encoding="utf-8"))
    results = [m["payload"] for m in data["messages"] if "result" in m["payload"]]
    assert _plant([r["result"] for r in results])
    cassette = tmp_path / "secret.mcp.json"
    cassette.write_text(json.dumps(data), encoding="utf-8")

    main(["lint", str(cassette), "--no-config", "--annotate", "github"])
    annotations, _ = _split(capsys.readouterr().out)
    assert any(
        line.startswith("::warning ") and "title=R005" in line for line in annotations
    )
    assert all(API_KEY not in line for line in annotations)
