---
schema: nightshift-rule/v1
decided: 2026-11-24
source: "nightshift rule r-fbbfbc80"
---

# Load experiments

Before running a load or stress experiment on the developer's machine, pick the smallest load that shows the effect, plan how its processes get stopped, and confirm none remain afterwards, because a load experiment for the flaky pacing test slowed the machine for several minutes and left processes behind.
