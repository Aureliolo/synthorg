package ui

import "testing"

// Sample release-body inputs sized to a realistic “synthorg update“
// payload: the v0.7.0 changelog has 12 highlight bullets and ~60
// commit lines. Inputs are kept inline (not files) so the bench
// allocation cost is fully attributable to the renderer, not I/O.
const sampleHighlightsBody = `**TL;DR**: API hardening, engine quality + shadow eval, observability hardening, settings service, fine-tune image GPU/CPU split.

### What's new
- API hardening: structured RFC 9457 errors, per-operation rate limits, escalation queue
- Engine quality: shadow eval and applier dry-run for engine middleware
- Observability hardening: TSA timestamping, OTLP exporter
- Settings service + ConfigResolver: centralised settings model
- Fine-tune image GPU/CPU split: synthorg-fine-tune-gpu and synthorg-fine-tune-cpu
- Cross-deployment analytics: aggregate metrics across multiple SynthOrg installs
- A/B rollout strategy: gradual feature rollout with canary cohort
- Chief of Staff advanced capabilities: meeting summarisation, decision tracking
- CLI UX overhaul: redesigned init wizard, status banner, hint tiers
- Custom rule authoring UI: visual editor for org rules with live preview
- AG-UI Evidence Package: standardised agent-to-UI evidence schema
- Brain/hands/session decoupling: cleaner agent lifecycle separation`

const sampleCommitsBody = `## [0.7.0](https://github.com/Aureliolo/synthorg/compare/v0.6.9...v0.7.0) (2026-04-18)

### Features
* **api:** structured RFC 9457 error envelopes ([#1234](https://github.com/Aureliolo/synthorg/pull/1234)) ([abc1234](https://github.com/Aureliolo/synthorg/commit/abc1234567890abcdef1234567890abcdef12345))
* **api:** per-operation rate limits ([#1235](https://github.com/Aureliolo/synthorg/pull/1235)) ([def5678](https://github.com/Aureliolo/synthorg/commit/def5678901234567890abcdef1234567890abcde))
* **engine:** shadow eval + applier dry-run ([#1240](https://github.com/Aureliolo/synthorg/pull/1240)) ([1a2b3c4](https://github.com/Aureliolo/synthorg/commit/1a2b3c4567890abcdef1234567890abcdef12345))
* **observability:** TSA timestamping support ([#1245](https://github.com/Aureliolo/synthorg/pull/1245)) ([5e6f7a8](https://github.com/Aureliolo/synthorg/commit/5e6f7a8901234567890abcdef1234567890abcde))
* **observability:** OTLP exporter ([#1247](https://github.com/Aureliolo/synthorg/pull/1247)) ([9b0c1d2](https://github.com/Aureliolo/synthorg/commit/9b0c1d2345678901234567890abcdef1234abcde))
* **settings:** centralised settings service + ConfigResolver ([#1252](https://github.com/Aureliolo/synthorg/pull/1252)) ([3e4f5a6](https://github.com/Aureliolo/synthorg/commit/3e4f5a6789012345678901234567890abcdef123))
* **fine-tune:** GPU/CPU image split ([#1260](https://github.com/Aureliolo/synthorg/pull/1260)) ([7b8c9d0](https://github.com/Aureliolo/synthorg/commit/7b8c9d0123456789012345678901234567890abc))
* **analytics:** cross-deployment aggregate metrics ([#1262](https://github.com/Aureliolo/synthorg/pull/1262)) ([1e2f3a4](https://github.com/Aureliolo/synthorg/commit/1e2f3a4567890123456789012345678901234567))

### Bug Fixes
* **persistence:** datetime round-trip strict utc enforcement ([#1268](https://github.com/Aureliolo/synthorg/pull/1268)) ([5b6c7d8](https://github.com/Aureliolo/synthorg/commit/5b6c7d8901234567890123456789012345678901))
* **api:** correct cursor-pagination consistency check ([#1271](https://github.com/Aureliolo/synthorg/pull/1271)) ([9e0f1a2](https://github.com/Aureliolo/synthorg/commit/9e0f1a2345678901234567890123456789012345))

### Refactors
* **engine:** middleware + coordination split ([#1278](https://github.com/Aureliolo/synthorg/pull/1278)) ([3b4c5d6](https://github.com/Aureliolo/synthorg/commit/3b4c5d6789012345678901234567890123456789))`

// BenchmarkRenderHighlightsStyled measures the styled-output path
// (lipgloss colors enabled). Called every "synthorg update" invocation
// in the default "color auto" mode on a TTY.
func BenchmarkRenderHighlightsStyled(b *testing.B) {
	opts := Options{NoColor: false, Plain: false}
	for b.Loop() {
		_ = RenderHighlights(sampleHighlightsBody, opts)
	}
}

// BenchmarkRenderHighlightsPlain measures the no-color / plain path.
// CI logs and "--no-color" invocations hit this regularly.
func BenchmarkRenderHighlightsPlain(b *testing.B) {
	opts := Options{NoColor: true, Plain: true}
	for b.Loop() {
		_ = RenderHighlights(sampleHighlightsBody, opts)
	}
}

// BenchmarkRenderCommitsStyled measures the commits-view styled path.
// The dev channel always renders this view, so dev-channel users hit
// this on every update walk.
func BenchmarkRenderCommitsStyled(b *testing.B) {
	opts := Options{NoColor: false, Plain: false}
	for b.Loop() {
		_ = RenderCommits(sampleCommitsBody, opts)
	}
}
