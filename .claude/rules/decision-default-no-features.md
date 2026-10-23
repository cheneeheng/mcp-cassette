---
schema: decision-rule/v1
---

# Default-no features

Decline new features and hardening work on these items: a second transport (whichever of stdio
and Streamable HTTP is not the Stage 1 transport), parallel-run safety, free-text PII scrubbing,
replay timing, async and non-pytest entry points, packaged CI (the GitHub Action and pre-commit
hooks), lint rule packs, a TypeScript port, and cassette viewers. Proceed only when the request
names an adopter's GitHub issue or a need in the author's own agent suite. Otherwise, name the item
and ask the author to confirm an override. Keeping CI green and fixing a bug someone reported are
not new work.

Why: the business plan holds these back so build hours go to outreach first, guarding against
features crowding out adoption. Decided 2026-10-23. Source:
`.agents_workspace/product/business/BUSINESS_PLAN.md` §05 and §12 (premortem story 1), which is
private and absent from a fresh clone.
