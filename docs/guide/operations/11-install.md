# 11. Installation

**Audience:** operators standing up mcp-cassette in a project or pipeline.

## 11.1 Requirements

| Item | Requirement |
|---|---|
| Python | >= 3.12 (classifiers cover 3.12 and 3.13) |
| OS | Linux, macOS, Windows |
| Runtime deps | `anyio>=4.0`, `pydantic>=2.0` — that is all |
| Optional `[http]` | `httpx>=0.27`, `h11>=0.14` |
| Optional `[test]` | `pytest>=8.0` |

mcp-cassette does **not** depend on the official `mcp` SDK at runtime, and must not be
made to. It works at the transport level with any MCP client.

## 11.2 Install the package

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

## 11.3 Post-install health check

1. The CLI is on PATH and the version matches:

   ```
   uv run mcp-cassette --help
   ```

   Expected: usage text listing the subcommands `record`, `serve`, `inspect`, `lint`.

2. The pytest plugin is loaded:

   ```
   uv run pytest --fixtures -q | grep mcp_cassette
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

## 11.4 What gets installed

- Console script `mcp-cassette` → `mcp_cassette.cli:main`.
- Module entry point: `python -m mcp_cassette` runs the same CLI. This is the form the
  fixture builds into agent commands, using `sys.executable`, so subprocesses inherit the
  test environment.
- pytest plugin `mcp_cassette` (fixture, marker, and two ini options).

## 11.5 Next

- [12. Configuration](12-configure.md) — modes, ini options, matching.
- [13. CI pipeline](13-ci.md) — the settings a pipeline must have.
