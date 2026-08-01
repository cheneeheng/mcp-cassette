# OP-01. Installation

**Audience:** operators standing up mcp-cassette in a project or pipeline.

## OP-01.0 The whole setup, in five commands

Install, verify, and lock the pipeline down. Every line is explained below; run these if
you just need it working.

```bash
uv add --dev mcp-cassette                                # 1. install (core: stdio)
uv add --dev "mcp-cassette[http]"                        # 2. only if you record remote HTTP servers
uv run mcp-cassette --help                               # 3. CLI is on PATH
uv run pytest --fixtures -q | grep mcp_cassette          # 4. pytest plugin is loaded
export MCP_CASSETTE_MODE=none                            # 5. in CI only — forbid recording
```

Steps 4 and 5 in PowerShell:

```powershell
uv run pytest --fixtures -q | Select-String mcp_cassette
$env:MCP_CASSETTE_MODE = "none"
```

Step 5 is the one non-negotiable pipeline setting: in `none` mode a missing cassette fails
the test instead of silently recording against a live server. Set it in the CI environment,
never in a developer shell. Full pipeline wiring is [OP-03](OP-03-ci.md).

**Verify:** step 3 prints usage listing `record`, `serve`, `inspect`, `diff`, `lint`; step 4
prints a line pointing at `.../mcp_cassette/pytest_plugin.py`. If step 4 prints nothing, go
to [OP-01.3](#op-013-post-install-health-check).

## OP-01.1 Requirements

| Item | Requirement |
|---|---|
| Python | >= 3.12 (classifiers cover 3.12 and 3.13) |
| OS | Linux, macOS, Windows |
| Runtime deps | `anyio>=4.2`, `pydantic>=2.0` — that is all |
| Optional `[http]` | `httpx>=0.27`, `h11>=0.14` |
| Optional `[test]` | `pytest>=8.0` |

mcp-cassette does **not** depend on the official `mcp` SDK at runtime, and must not be
made to. It works at the transport level with any MCP client.

## OP-01.2 Install the package

```
uv add --dev mcp-cassette              # core: stdio record/replay
uv add --dev "mcp-cassette[http]"      # adds remote Streamable HTTP record/replay
```

pip equivalents:

```
pip install mcp-cassette
pip install "mcp-cassette[http]"
```

Install it into the **same environment pytest runs in**. The plugin is discovered via
the `pytest11` entry point; a package installed elsewhere is invisible to pytest.

## OP-01.3 Post-install health check

1. The CLI is on PATH:

   ```
   uv run mcp-cassette --help
   ```

   Expected: usage text whose first line lists all five subcommands.

   ```
   usage: mcp-cassette [-h] {record,serve,inspect,diff,lint} ...
   ```

   There is no `--version` flag. To check the installed version:

   ```
   uv run python -c "import mcp_cassette; print(mcp_cassette.__version__)"
   ```

2. The pytest plugin is loaded:

   ```bash
   uv run pytest --fixtures -q | grep mcp_cassette
   ```

   ```powershell
   uv run pytest --fixtures -q | Select-String mcp_cassette
   ```

   Expected: a line naming `mcp_cassette` and pointing at
   `.../mcp_cassette/pytest_plugin.py`.

3. The HTTP extra, if you installed it:

   ```
   uv run python -c "import httpx, h11; print('http extra ok')"
   ```

   Expected output: `http extra ok`.

**If step 2 shows nothing:** you have two environments. Confirm with
`uv run python -c "import mcp_cassette, sys; print(sys.executable)"` and compare against
the interpreter pytest reports in its header.

## OP-01.4 What gets installed

- Console script `mcp-cassette` → `mcp_cassette.cli:main`.
- Module entry point: `python -m mcp_cassette` runs the same CLI. This is the form the
  fixture builds into agent commands, using `sys.executable`, so subprocesses inherit the
  test environment.
- pytest plugin `mcp_cassette` (fixture, marker, and two ini options).

## OP-01.5 Next

- [OP-02. Configuration](OP-02-configure.md) — modes, ini options, matching.
- [OP-03. CI pipeline](OP-03-ci.md) — the settings a pipeline must have.
