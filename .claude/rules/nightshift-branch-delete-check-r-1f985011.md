---
schema: nightshift-rule/v1
decided: 2026-11-24
source: "nightshift rule r-1f985011"
---

# Branch delete check

Before deleting a branch, even on a direct request, look at what it holds and whether its commits are reachable from another ref, and report any unique content before deleting, because `backup/internals-refactor-guide-pre-scrub` was deleted with 7 commits (about 3,450 lines of `docs/internals/`) that existed nowhere else.
