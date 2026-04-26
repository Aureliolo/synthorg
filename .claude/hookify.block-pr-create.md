---
name: block-pr-create
enabled: true
event: bash
pattern: gh\s+pr\s+create
action: block
---

**PR creation blocked.** Do not use `gh pr create` directly.

Use `/pre-pr-review` instead; it runs automated checks + review agents + fixes before creating the PR.

For trivial or docs-only changes: `/pre-pr-review quick` skips agents but still runs automated checks.
