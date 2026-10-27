---
schema: decision-rule/v1
---

# Stage transport

Stdio is the Stage 1 transport. Streamable HTTP is Stage 2: decline new features and hardening
work on it until Stage 1 has a real user, and otherwise name the item and ask the author to confirm
an override. Keeping CI green and fixing a bug someone reported are not new work.

Why: stdio leads Streamable HTTP about 3 to 1 among public agent repos that configure MCP servers
in tests. Decided 2026-10-27. Source: `.agents_workspace/product/code-search/20261026-67162e59.md`
and `.agents_workspace/product/business/BUSINESS_PLAN.md` §05, which are private and absent from a
fresh clone.
