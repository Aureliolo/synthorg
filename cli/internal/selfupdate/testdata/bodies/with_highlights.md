<!-- HIGHLIGHTS_START -->
## Highlights

> _AI-generated summary (model: `example-provider/example-medium-001` via Mistral). Commit-based changelog below._

### What you'll notice

- Update walks every release between installed and target, oldest first.
- New `c` shortcut toggles between AI summary and the commit log per session.
- Setup wizard recovers cleanly from stale browser cookies on first run.

### What's new

- Per-version Highlights view in `synthorg update` with live commit-changelog toggle.
- Dev channel renders a single combined commit list between current and target.

### Under the hood

- Bubbletea-based viewport for in-block scrolling on long releases.
- GitHub compare API integration with redirect host validation reused from selfupdate.

<!-- HIGHLIGHTS_END -->

## [0.7.3](https://github.com/Aureliolo/synthorg/compare/v0.7.2...v0.7.3) (2026-04-25)


### Features

* **cli:** show per-version Highlights on upgrade walk + commit-based toggle ([#1564](https://github.com/Aureliolo/synthorg/issues/1564)) ([abc1234](https://github.com/Aureliolo/synthorg/commit/abc1234abc1234abc1234abc1234abc1234abc12))
* **cli:** dev-channel commit-list view between installed and target ([#1572](https://github.com/Aureliolo/synthorg/issues/1572)) ([def5678](https://github.com/Aureliolo/synthorg/commit/def5678def5678def5678def5678def5678def56))


### Bug Fixes

* **selfupdate:** harden GitHub API pagination cap to 5 pages ([#1573](https://github.com/Aureliolo/synthorg/issues/1573)) ([fed9876](https://github.com/Aureliolo/synthorg/commit/fed9876fed9876fed9876fed9876fed9876fed98))


### Refactoring

* **ui:** factor changelog rendering helpers out of cmd ([#1574](https://github.com/Aureliolo/synthorg/issues/1574)) ([cba8765](https://github.com/Aureliolo/synthorg/commit/cba8765cba8765cba8765cba8765cba8765cba87))
---

## CLI Installation

**Linux / macOS:**

```bash
curl -sSfL https://synthorg.io/get/install.sh | bash
```

## Container Images

| Image | Command |
|-------|---------|
| Backend | `docker pull ghcr.io/aureliolo/synthorg-backend:0.7.3` |

---

## Verification

### CLI Checksums (SHA-256)

| Archive | SHA-256 |
|---------|---------|
| `synthorg_linux_amd64.tar.gz` | `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef` |
