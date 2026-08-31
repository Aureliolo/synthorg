<!-- HIGHLIGHTS_START -->
## Highlights

> _AI-generated summary (model: `example-provider/example-capable-001` via Example). Commit-based changelog below._

_Nineteen new gates shipped, because the previous nineteen were merely a warm-up._

### What you'll notice

- The release walk now opens on a one-line tagline instead of a wall of bullets.
- Update no longer stalls when the summary service is unreachable; it falls back to the commit log.

### What's new

- Release digests are built from commit bodies rather than subjects, so the summary knows what a change does.
- A dry-run workflow rehearses the whole summary path against any commit range without touching a release.

<!-- HIGHLIGHTS_END -->

## [0.9.0](https://github.com/Aureliolo/synthorg/compare/v0.8.9...v0.9.0) (2026-08-30)

### Features

* build release digests from commit bodies ([#4](https://github.com/Aureliolo/synthorg/issues/4)) ([abc1234](https://github.com/Aureliolo/synthorg/commit/abc1234))

### Bug Fixes

* stop erasing the summary block on a failed generation ([#5](https://github.com/Aureliolo/synthorg/issues/5)) ([def5678](https://github.com/Aureliolo/synthorg/commit/def5678))

---

## CLI Installation

```bash
curl -fsSL https://example.invalid/install.sh | sh
```
