# OP-01. Installation

**Audience:** operators standing up mcp-cassette in a project or pipeline.

## OP-01.0 The whole setup, in five commands

Install, verify, and lock the pipeline down. Every line is explained below; run these if
you just need it working.

```bash
uv add --dev mcp-cassette                                # 1. install (core: stdio)
uv add --dev "mcp-cassette[http]"                        # 2. only if you record remote HTTP servers
uv run mcp-cassette --help                               # 3. CLI answers
uv run pytest --fixtures -q | grep mcp_cassette          # 4. pytest plugin is loaded
export MCP_CASSETTE_MODE=none                            # 5. in CI only — forbid recording
```

This is the sequence for **your own project**. In a clone of the mcp-cassette repo,
replace steps 1–2 with `uv sync` — `uv add mcp-cassette` there is a self-dependency error.
See [OP-01.2](#op-012-install-the-package), which also states what each install writes and
how to undo it.

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

Which command you want depends on where you are standing.

### Into your own project (the normal case)

mcp-cassette is a test-only tool, so it belongs in the dev group:

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

### Into a clone of the mcp-cassette repo

Contributing, or running `examples/` and the CLI walkthrough in
[Getting started](../getting-started.md)? Those live in this repo, so the install is a
sync, not an add:

```
uv sync
```

`uv add mcp-cassette` inside the clone is a hard error — uv exits `2` with
`self-dependencies are not permitted`, because the requirement name matches the project
name. `uv add --dev mcp-cassette` does not error, but it silently rebuilds the checkout
as its own dependency, which is not what you meant either. Use `uv sync`.

### OP-01.2.1 What each install writes, and how to undo it

Neither command is destructive, but both write outside the virtualenv, so state it before
you run it in someone else's checkout:

| Command | Writes | Undo |
|---|---|---|
| `uv add --dev mcp-cassette` | `pyproject.toml` (a dev-group entry), `uv.lock`, `.venv/` | `uv remove --dev mcp-cassette` |
| `pip install mcp-cassette` | the active environment only | `pip uninstall mcp-cassette` |
| `uv sync` | `.venv/`, and `uv.lock` if it was stale | delete `.venv/` |

Nothing here touches your cassettes, your tests, or your git history.

One thing to expect if you undo the wrong install **inside a clone**: `uv remove --dev
mcp-cassette` prints `~ mcp-cassette==0.4.0` and leaves the package importable. That is not a
failed removal. It removed both things `uv add --dev` had written to `pyproject.toml` (the
`dev` entry and a `[tool.uv.sources]` line) — `git diff pyproject.toml` comes back empty — and
the line you see is uv reinstalling the *project itself*, which is always present in its own
checkout's virtualenv.

## OP-01.3 Post-install health check

1. The CLI answers. It is installed into the virtualenv and *not* placed on `PATH`, so
   invoke it through `uv run` (or activate the venv first); a bare `mcp-cassette` is
   `command not found`:

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

What it does **not** install is `examples/` — the sample servers, clients, and cassettes
live in the repo, not in the wheel. Recipes naming `examples/...` need a clone; from your
own project, [Getting started](../getting-started.md#no-server-to-record-against-yet)
carries a paste-able 30-line server that stands in for them.

## OP-01.5 Next

- [OP-02. Configuration](OP-02-configure.md) — modes, ini options, matching.
- [OP-03. CI pipeline](OP-03-ci.md) — the settings a pipeline must have.
