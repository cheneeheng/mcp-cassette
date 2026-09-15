"""The v4 lint rules, on a committed demo cassette.

``cassettes/surfaces.mcp.json`` hides three problems where the v3 rules never looked:

* **R006** — injection phrasing inside an ``inputSchema`` property description, not the
  tool description R001 reads.
* **R007** — a second tool named ``еcho`` whose first letter is Cyrillic, a lookalike
  of ``echo``.
* **R005** — a high-entropy deploy key in a ``tools/call`` result, which no key-name
  redaction rule could have caught because it sits in free text.

All three are warnings, so a plain ``lint`` still exits 0; ``--fail-on warning`` makes
them gate. ``--require-redaction`` is the separate gate that refuses a cassette with no
redaction manifest (this one was hand-written, so it has none).

Run the same checks by hand::

    uv run mcp-cassette lint examples/cassettes/surfaces.mcp.json
    uv run mcp-cassette lint examples/cassettes/surfaces.mcp.json --annotate github
    uv run mcp-cassette lint examples/cassettes/surfaces.mcp.json \\
        --require-redaction team-baseline          # exit 4: no manifest
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import mcp_cassette as mcc

HERE = Path(__file__).parent
SURFACES = HERE / "cassettes" / "surfaces.mcp.json"


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_cassette", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_v4_rules_find_what_v3_rules_missed() -> None:
    """R005, R006, and R007 each fire once; the v3 description rule finds nothing."""
    report = mcc.lint_cassette(SURFACES)
    by_rule = {f.rule: f for f in report.findings}

    assert sorted(by_rule) == ["R005", "R006", "R007"]
    assert "R001" not in by_rule  # the tool descriptions themselves are clean
    assert by_rule["R006"].locator.endswith("/properties/text/description")
    assert "U+0435 CYRILLIC SMALL LETTER IE" in by_rule["R007"].message
    # R005 names the token by its first six characters only, never the whole secret.
    assert "Zq8Xv2" in by_rule["R005"].message
    assert "Zq8Xv2Lm9Tn4Rb7Kp1Wc6Hy3Fd5Js0Ga" not in by_rule["R005"].message


def test_warnings_pass_by_default_and_gate_on_request() -> None:
    """Warnings alone exit 0; ``--fail-on warning`` turns them into exit 4."""
    assert _cli("lint", str(SURFACES)).returncode == 0
    assert _cli("lint", str(SURFACES), "--fail-on", "warning").returncode == 4


def test_entropy_rule_can_be_tuned_off() -> None:
    """``--no-entropy`` drops R005 and leaves the other rules running."""
    report = _cli("lint", str(SURFACES), "--no-entropy", "--format", "json")
    assert '"R005"' not in report.stdout
    assert '"R006"' in report.stdout


def test_github_annotations() -> None:
    """``--annotate github`` adds workflow commands a pull request renders inline."""
    out = _cli("lint", str(SURFACES), "--annotate", "github").stdout
    assert "::warning file=" in out
    assert "R007" in out


def test_require_redaction_refuses_an_unscrubbed_cassette() -> None:
    """No redaction manifest means no scrubber ever ran on the cassette: exit 4."""
    result = _cli("lint", str(SURFACES), "--require-redaction", "team-baseline")
    assert result.returncode == 4
    assert "--redact-profile team-baseline" in result.stderr
