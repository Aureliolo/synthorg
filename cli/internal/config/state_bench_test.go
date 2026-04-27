package config

import (
	"os"
	"testing"
)

// BenchmarkLoadDefault measures cold-path config load (file does not
// exist, returns DefaultState). Every CLI invocation hits this code
// path before the file exists.
func BenchmarkLoadDefault(b *testing.B) {
	dir := b.TempDir()
	for b.Loop() {
		if _, err := Load(dir); err != nil {
			b.Fatalf("Load: %v", err)
		}
	}
}

// BenchmarkLoadExisting measures the warm path: state.json exists and
// must be unmarshalled + migrated on Load. The kernel page cache is
// hot after the first iteration -- this measures the parse+migrate
// cost dominated by cached I/O, not cold-disk reads. Every CLI
// invocation after first init runs this path with a similarly hot
// cache, so the warm-cache scenario is the realistic one.
func BenchmarkLoadExisting(b *testing.B) {
	dir := b.TempDir()
	state := DefaultState()
	state.BackendPort = 3001
	state.WebPort = 3000
	state.LogLevel = "info"
	state.PersistenceBackend = "postgres"
	state.PostgresPort = 3002
	state.PostgresPassword = "test-postgres-password-32-chars-min"
	state.MemoryBackend = "mem0"
	state.BusBackend = "nats"
	state.Sandbox = true
	state.FineTuning = true
	state.FineTuningVariant = "cpu"
	// v0.7.3 is illustrative -- a representative recent stable tag with
	// full feature coverage (postgres, nats, fine-tuning). The exact tag
	// does not matter for perf; it is a fixture, not a pinned version.
	state.ImageTag = "v0.7.3"
	state.Channel = "stable"

	if err := writeStateForBench(b, dir, &state); err != nil {
		b.Fatalf("write state: %v", err)
	}
	// b.Loop() handles setup-time exclusion automatically; no explicit
	// b.ResetTimer() is needed here.
	for b.Loop() {
		if _, err := Load(dir); err != nil {
			b.Fatalf("Load: %v", err)
		}
	}
}

// BenchmarkIsValidImageTag exercises the image-tag validation regex
// across a representative mix of inputs. Called every "config set",
// every flag parse, every digest-pin verification.
//
// Per-call cost (~55 ns/op) is dominated by the inner range loop's
// bookkeeping; the reported ns/op is the cost of seven consecutive
// IsValidImageTag calls. We accept this because the variance across
// inputs is the regression signal we care about, not single-input
// micro-cost.
func BenchmarkIsValidImageTag(b *testing.B) {
	tags := []string{
		"latest",
		"v0.7.3",
		"v0.7.3-dev.20",
		"sha256-abcdef0123456789-amd64",
		"INVALID..tag",
		"",
		"v1.2.3-rc.1+build.42",
	}
	for b.Loop() {
		for _, t := range tags {
			IsValidImageTag(t)
		}
	}
}

// BenchmarkResolveTunables measures the env-var + config-state +
// default precedence walk for the full tunables surface. Runs once
// per CLI invocation.
func BenchmarkResolveTunables(b *testing.B) {
	state := DefaultState()
	state.RegistryHost = "ghcr.io"
	state.ImageRepoPrefix = "aureliolo/synthorg"
	for b.Loop() {
		if _, err := ResolveTunables(state); err != nil {
			b.Fatalf("ResolveTunables: %v", err)
		}
	}
}

// writeStateForBench serialises *state to disk where Load() expects
// it. Sets state.DataDir = dir so Save's SecurePath normalisation
// lands on the temp directory. Pointer parameter is intentional --
// the helper mutates DataDir on the caller's behalf, and a pointer
// makes the side effect explicit at every call site.
func writeStateForBench(b *testing.B, dir string, state *State) error {
	b.Helper()
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	state.DataDir = dir
	return Save(*state)
}
