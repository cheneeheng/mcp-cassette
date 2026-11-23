---
schema: fix-record/v3
generated_from: 20261123-205245-replay-pacing-tests.yaml
generated_note: "Do not edit. artifact.py renders this file from 20261123-205245-replay-pacing-tests.yaml: edit the YAML, then run artifact.py render."
status: fixed
date: 2026-11-23
issue: CI run on main at 49020ad, job "test (macos-latest, 3.12)"
---

<!-- prettier-ignore-start -->

# Replay pacing integration tests

## 1. The delay-fault pacing test fails when two interpreter startups differ by half a second

**Status:** fixed

**Symptom:** `uv run pytest tests/integration/test_replay_pacing.py::test_delay_fault_is_additive_on_top_of_pacing` → assert (3.2014962920000016 - 2.8197479579999936) >= 0.5 (1 failed, 685 passed, 3 skipped on macos-latest, Python 3.12)

### Diagnosis

| Hypothesis | Refuted by | Result |
| --- | --- | --- |
| A delay fault replaces the recorded pacing sleep instead of adding to it, so the real signal is 500 ms and sits on the floor | The replay server sleeping once for the larger of the two, or a local delta near 0.5 s | refuted: src/mcp_cassette/replay/server.py sleeps the pacing gap, then the fault delay. Six local runs gave deltas 1.12, 1.00, 0.99, 0.94, 1.53, 1.00 |
| The test compares the wall time of two whole subprocess runs, and interpreter startup varies by more than the 0.5 s margin | Whole-run times for the same command staying within a few hundredths of a second across runs | confirmed: Six local runs of the same commands: instant replay 0.59 to 0.95 s, paced plus fault 3.14 to 3.75 s. On the failed job the paced run took 2.82 s against 2.2 s locally |

**Cause:** tests/integration/test_replay_pacing.py test_delay_fault_is_additive_on_top_of_pacing, through _elapsed: _elapsed times a whole subprocess run, interpreter startup and shutdown included. The test
subtracts two such runs and requires at least 0.5 s of the 1.0 s delay to show. Startup
alone varied by 0.36 s and one whole run by 0.61 s on a quiet Windows machine, so on a
loaded macOS runner a slow baseline run hid 0.62 s of the delay and the delta came to 0.38 s.
The product was correct: the delay was slept in full.

**Prediction:** `Six runs each of the paced and the paced-plus-fault command, timing only the span between the first and the last response inside one run` → Predicted a spread of a few hundredths of a second. Seen: paced 1.000 to 1.032 s, with fault 2.016 to 2.032 s.

### Fix

The scripted test client now stamps the arrival time of each awaited response, and the test
subtracts spans measured inside one run, first response to last response, instead of whole
runs. Startup and shutdown are no longer in the measurement, so the 1.0 s signal keeps about
0.98 s against the 0.5 s floor.

**Tests:**

- `tests/integration/test_replay_pacing.py::test_delay_fault_is_additive_on_top_of_pacing` — protects: A delay fault adds its full delay on top of the recorded pacing sleep; fails_when: The delay is not slept. With a 0 ms delay fault the span delta was at most 0.031 s, below the 0.5 s floor; why_new: Not a new test. The existing test measured the right behavior through a noisy quantity, so it failed on a correct product

**Unverified:** The fix was not run on a macOS runner before the PR. test_recorded_gaps_are_replayed and test_timeout_fault_spends_no_pacing_sleep still compare whole runs and were left as they are, because neither has failed.

<!-- prettier-ignore-end -->
