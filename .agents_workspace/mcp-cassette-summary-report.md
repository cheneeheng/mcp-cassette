# mcp-cassette — Summary Report
 
**Date:** 2026-07-05
**Status:** Committed. Planning to follow separately.
**Name:** `mcp-cassette` (PyPI name verified available; `mcp-tapedeck`, `mcpvcr`, `replaydeck` also free as fallbacks)
 
## Verdict
 
Build an **agent-side MCP record/replay and mocking library** for Python, surfaced as a pytest fixture. The originally scoped server-testing harness is out: that window closed. The agent-side gap is verified empty as of today.
 
## How we got here
 
Starting point was "MCP as the integration layer" from the July 2026 tech-trend scan, filtered for what one person can build and maintain: no hosted infra, distribution via package registries. Three candidate legs were identified — testing/mocking harness, spec conformance suite, security linting — with the thesis that they could share one codebase.
 
The market check (PyPI metadata, npm registry, GitHub org/repo activity) invalidated two of the three legs and narrowed the third.
 
## Market check findings
 
**Dead — conformance.** The official `modelcontextprotocol/conformance` repo exists, tests both clients and servers against the spec, ships CLI suites and an SDK integration guide, and was pushed July 2, 2026. First-party tooling is actively developed here. Do not compete.
 
**Dead — server-side testing and lint.** `mcp-assert` (Go, blackwell-systems, 21 stars but pushed July 5) is executing this well: real stdio/SSE/HTTP transports, YAML assertions, 14 lint rules, fuzzing, a GitHub Action, production adopters (incl. a 25-server CI setup), and fix PRs merged into Google, Grafana, and LangChain repos. It positions explicitly as "pytest for MCP servers." The graveyard around it (golf-testing abandoned Nov 2025; pytest-mcp single dead release; mcp-lint one-day project) confirms attempts were made and one competent player emerged.
 
**Empty — agent-side testing.** Everything above answers "does my *server* behave." Nothing answers "does my *agent* behave correctly against MCP servers." No record/replay of MCP sessions, no deterministic mock servers rebuilt from recordings, no MCP-aware fault injection. Closest gesture is `mcp-chaos-rig` (10 stars, fault injection only, no record/replay). npm has nothing MCP-specific; msw/mockttp are HTTP-generic and don't speak the protocol. PyPI names `mcp-mock`, `mcp-replay`, `mcp-record`, `mcp-fixtures`, `mock-mcp` are all unclaimed — nobody has even squatted the namespace.
 
## Product definition
 
`mcp-cassette` is vcrpy for MCP. It records real MCP sessions into cassettes (structured, diffable files), then replays them as deterministic mock servers in CI. Agent tests stop being flaky and expensive because they stop hitting live servers.
 
Core capabilities, in build order:
 
1. **Record** — a transparent proxy (stdio first, HTTP second) between client and server that captures the full protocol exchange: initialize handshake, tools/list, tools/call, results, errors, timing. Works with Claude Code unmodified — no SDK monkeypatching.
2. **Replay** — spin up a mock MCP server from a cassette; responses matched on request shape, deterministic ordering.
3. **pytest fixture** — `mcp_cassette` fixture that manages record-on-first-run / replay-thereafter, mirroring vcrpy ergonomics that the target audience already knows.
4. **Fault injection** — mutate cassettes to simulate timeouts, auth failures, malformed results, server disappearance. This absorbs what mcp-chaos-rig gestures at and becomes the differentiator beyond plain replay.
Security linting survives only as a possible later feature (flagging suspicious tool descriptions in recorded cassettes), not a pillar.
 
## Why the position is defensible
 
- **Complementary, not competitive, with first-party tooling.** Testing harnesses for consumers of a protocol historically stay third-party (pytest isn't from the Python core team; vcrpy isn't from the requests team). The official conformance suite and mcp-assert make servers trustworthy; mcp-cassette makes agents that consume them testable. Different buyer, different CI stage.
- **Bottom-up distribution.** A dev adds it because their agent tests are flaky — no purchase decision, no sales motion. Fits solo constraints exactly.
- **User zero exists.** The Claude Code subagent orchestration work is itself an agent system consuming MCP servers; its test suite is the first real workload.
## Risks
 
- **Spec churn.** MCP is versioning fast; the proxy and cassette format must track transport changes. Mitigation: this same churn is the moat — it's why nobody hand-rolls this.
- **First-party expansion.** The official conformance repo tests *clients* too; if it grows record/replay for agent CI, overlap appears. Current scope is spec compliance, not test doubles for app developers — watch the repo, don't panic-pivot.
- **mcp-assert scope creep.** They own server-side mindshare and could extend toward agents. Their architecture (YAML assertions against live servers) points the other way, but they're the fastest-moving neighbor.
- **Small current market.** Agent-in-production numbers are still low (~11% of orgs). The bet is that agent CI pain grows with production adoption through 2026–27.
## Open decision (for planning session)
 
Recording-point architecture: transparent stdio/HTTP proxy is the working assumption (client-agnostic, no monkeypatching), but the cassette format, matching rules, and HTTP/SSE session capture semantics need design.