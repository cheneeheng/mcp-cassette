# PyPI Deployment Plan — mcp-cassette

Status: **ready to release, after three small packaging fixes** shipped as v0.3.3.
Written 2026-07-22 against `main` @ `9e78c29` (v0.3.2).

## 1. Readiness evidence (verified 2026-07-22)

| Check | Result |
|---|---|
| PyPI name `mcp-cassette` | Available (`pypi.org/pypi/mcp-cassette/json` → 404) |
| CI on `main` | Green — 3 OS x 2 Python matrix, coverage gate `fail_under = 99` |
| `uv build` | Wheel + sdist build clean |
| `twine check dist/*` | PASSED for both artifacts |
| Wheel smoke test | `uv run --isolated --with dist/*.whl mcp-cassette --help` works; 8 packages total installed (only `anyio` + `pydantic` trees — no dep creep) |
| Version consistency | `pyproject.toml` = `__version__` = tag `v0.3.2`; CI `version-check` job gates this |
| Runtime deps | `anyio>=4.0`, `pydantic>=2.0` only — invariant holds |
| Entry points | `mcp-cassette` console script + `pytest11` plugin both declared and present in wheel `entry_points.txt` |
| License | Apache-2.0, shipped in wheel `dist-info/licenses/` |

## 2. Pre-release fixes (blocking, all packaging-only → v0.3.3)

1. **Ship `py.typed`** — the wheel has no PEP 561 marker, so consumers' mypy
   treats this strictly-typed library as untyped. Create the empty file
   `src/mcp_cassette/py.typed`; hatchling includes it automatically via
   `packages = ["src/mcp_cassette"]`. Verify with the wheel-content check in §4.
2. **Trim the sdist** — it currently ships `.agents_workspace/` (all planning
   docs, DECISION_LOG, summary report), `CLAUDE.md`, and
   `.pre-commit-config.yaml`. Repo is public so nothing is confidential, but
   internal agent-workspace docs do not belong in a distribution. Add:

   ```toml
   [tool.hatch.build.targets.sdist]
   exclude = [".agents_workspace", ".claude", "CLAUDE.md", ".pre-commit-config.yaml"]
   ```

   Keep `tests/`, `docs/`, `examples/` in the sdist (standard practice).
3. **License metadata** — move to the PEP 639 SPDX form so PyPI renders it:
   `license = "Apache-2.0"` and `license-files = ["LICENSE"]` (replaces the
   deprecated `{ file = "LICENSE" }` table). `[build-system]` pins no hatchling
   version, so a fresh `uv build` gets a PEP 639-capable hatchling.

Not blocking, do or skip at will: none identified — classifiers, keywords,
URLs, readme, and `requires-python` are already in order.

## 3. Publish mechanism (decision)

Use **PyPI Trusted Publishing (OIDC) from GitHub Actions**, triggered on GitHub
release published. No long-lived API token, matches the existing tag-based
release flow, and keeps publishing reproducible from CI rather than a laptop.

One-time setup (manual, on pypi.org):
- PyPI → Account → Publishing → add a *pending* publisher for project
  `mcp-cassette`: owner `cheneeheng`, repo `mcp-cassette`, workflow
  `publish.yml`, environment `pypi`.
- Same on test.pypi.org with environment `testpypi` for the dry run.

New workflow `.github/workflows/publish.yml` (~30 lines):

```yaml
name: Publish
on:
  release:
    types: [published]
  workflow_dispatch:        # manual trigger for the TestPyPI dry run
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv build
      - uses: actions/upload-artifact@v4
        with: { name: dist, path: dist/ }
  publish-testpypi:
    if: github.event_name == 'workflow_dispatch'
    needs: build
    runs-on: ubuntu-latest
    environment: testpypi
    permissions: { id-token: write }
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
        with: { repository-url: "https://test.pypi.org/legacy/" }
  publish-pypi:
    if: github.event_name == 'release'
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions: { id-token: write }
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
```

## 4. Release sequence

1. Branch `chore/pypi-packaging` off `main`; apply §2 fixes + add
   `publish.yml`; bump `pyproject.toml` and `__init__.__version__` to `0.3.3`
   in the same PR (the `version-check` CI job enforces they match); add a
   `0.3.3` changelog entry (packaging-only, Keep a Changelog format).
2. Local gate before the PR:
   - `uv run ruff check . && uv run mypy src`
   - `uv build && uv run --with twine twine check dist/*`
   - Wheel-content check: confirm `py.typed` present, `.agents_workspace`
     absent from the sdist:
     `python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist() if 'py.typed' in n])"`
     and `tar -tzf dist/*.tar.gz | grep -c agents_workspace` (expect 0 / error).
3. PR → CI green → merge (existing flow; branch-merger agent if delegated).
4. **TestPyPI dry run**: run `publish.yml` via `workflow_dispatch`; then from a
   clean venv:
   `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple mcp-cassette==0.3.3`
   and smoke `mcp-cassette --help` + `python -c "import mcp_cassette; print(mcp_cassette.__version__)"`.
5. Tag `v0.3.3` on main + GitHub release with changelog notes (release-cutter
   flow) → release event fires `publish.yml` → PyPI publish.
6. Post-publish verification:
   - `pip install mcp-cassette==0.3.3` from real PyPI in a clean venv; smoke
     CLI + import + `pytest --co -q` in a scratch project to confirm the
     pytest plugin registers.
   - Check the PyPI project page renders README and license correctly.
7. Remember: a version publishes exactly once — any fix after upload is a new
   patch version, never a re-upload.

## 5. Deferred-topics assessment (v3 "Out of MVP scope", ITER_04_v3)

None block a Beta (`Development Status :: 4 - Beta`) PyPI release. All fifteen
items are opt-in features or documented edges, not correctness gaps in the
shipped surface. Item-by-item:

| Deferred item | Blocks release? | Reason |
|---|---|---|
| Python lint rule plugins | No | Deliberate security stance (no third-party code execution in `lint`) — a feature of the design, not a gap |
| Entropy-based secret detection | No | Redaction defaults are on and key-structural; documented |
| `use_cassette_async` | No | Additive API, semver-minor later |
| In-process stdio replay (`[sdk]` extra) | No | Deferred on cost/benefit; subprocess replay works everywhere |
| Concurrency guard (shared cassette path) | No, but watch | The most user-visible edge: two sessions (e.g. pytest-xdist workers) sharing one cassette path can interleave writes. Documented deferred; consider for 0.4.0 if PyPI users hit it |
| Pacing jitter / statistical models | No | Pacing itself is opt-in |
| Record-path pacing | No | Recording is live by definition |
| TUI/color for inspect/diff | No | Cosmetic |
| `diff` beyond tool surfaces | No | Descriptive tool; R002 gates |
| Format migration tooling | No | `format_version` gate + `UnsupportedFormatVersion` already protect users |
| Multi-server orchestration | No | Compose multiple cassettes; documented |
| Legacy HTTP+SSE / OAuth / resumability | No | v2 edge, documented |
| Response-assertion for sampling; faults on server-initiated requests | No | v2 edge, documented |
| Graceful-interrupt finalize for `new_episodes` | No, but watch | Ctrl-C during `new_episodes` may lose appended episodes; `once`/`all` are covered by checkpointing. Second candidate for 0.4.0 |
| npm port / GitHub Action | No | Ecosystem expansion, unrelated to Python packaging |

## 6. Rollback / failure handling

- Publish workflow fails after TestPyPI but before PyPI: nothing public
  happened; fix and re-dispatch.
- Bad artifact reaches PyPI: `pip`-visible breakage → yank the release on PyPI
  (does not delete; prevents new default installs), ship a fixed patch version.
  Never delete a release outright — pinned users keep working under a yank.
