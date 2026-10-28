---
schema: fix-record/v1
status: fixed
date: 2026-10-28
---

# `ordering="strict"` misses every call after a recorded handshake

**Symptom:** `uv run pytest tests/integration/test_replay.py::test_strict_ordering_replays_a_real_recording`
→ `mcp-cassette: replay diverged at step 2: tools/call: no recorded call matches` and exit `3`,
replaying the session it had just recorded.

## Diagnosis

| Hypothesis | Refuted by | Result |
| ---------- | ---------- | ------ |
| The recorded `initialize` exchange holds the head of the `strict` queue, because the replay servers answer `initialize` themselves and never pass it to the matcher | A `Matcher` over `initialize` + `tools/list` with `ordering="strict"` matching an incoming `tools/list` | confirmed: a scratch script printed `strict tools/list match: False` on v0.4.0 code |

**Cause:** `src/mcp_cassette/matching.py` `Matcher._lookup`, `strict` branch: it returns a miss
when the first unconsumed exchange does not match. Every real recording opens with `initialize`,
which `replay/server.py`, `transports/http/server.py` and `replay/new_episodes.py` all answer
without the matcher, so that exchange is never consumed and every later request misses.

**Prediction:** with the fix stashed, the two reproducers fail →
`2 failed, 24 passed`, seen.

## Fix

The `strict` lookup skips `initialize` exchanges, the same way the divergence lookup already
did. Order is still enforced for every other call.

**Tests:**

- `tests/unit/test_matching.py::test_ordering_strict_matches_the_first_call_after_a_recorded_handshake`
  — protects: `strict` replay of any real recording; fails_when: an unanswered-by-the-matcher
  exchange blocks the queue again; why_new: every existing `strict` test used a cassette with no
  `initialize`.
- `tests/unit/test_matching.py::test_ordering_strict_still_enforces_order_after_a_recorded_handshake`
  — protects: `strict` still refusing an out-of-order call; fails_when: a fix loosens `strict`
  into skipping more than `initialize`; why_new: the fix adds a skip, and nothing pinned its
  limit.
- `tests/integration/test_replay.py::test_strict_ordering_replays_a_real_recording` — protects:
  `serve --ordering strict` on a cassette recorded by `record`; fails_when: the defect returns
  anywhere on the stdio path; why_new: no integration test ran `strict` end to end.

**Unverified:** `strict` over HTTP replay has no new test. It shares the fixed `Matcher`, and HTTP
is frozen (Stage 2). Inferred from reading `transports/http/server.py`, not run.
