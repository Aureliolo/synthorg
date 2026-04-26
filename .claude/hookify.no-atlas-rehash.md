---
name: no-atlas-rehash
enabled: true
event: bash
pattern: atlas\s+migrate\s+hash
action: block
---

**BLOCKED: Do not rehash Atlas migrations.**

`atlas migrate hash` rewrites `atlas.sum` checksums. Post-release, this would silently invalidate existing database installations.

If you see a checksum mismatch after rebase:
1. The baseline was likely regenerated; check `git diff` on the baseline `.sql`
2. If schema actually changed, create a **new migration**: `atlas migrate diff --env sqlite <name>`
3. Never modify or rehash an existing migration that has been released

**Pre-alpha exception**: the `block` action stops the command before it runs. If you genuinely need to rehash during development:

1. Ask the human developer in this session and get explicit approval before rehashing
2. Temporarily disable this rule by setting `enabled: false` in this file's frontmatter, run the command, then re-enable immediately
3. Document the reason in the commit message (e.g., "chore: rehash atlas checksums after CRLF normalization fix")

Never silently bypass the hook. Post-release this rule becomes mandatory: no overrides.
