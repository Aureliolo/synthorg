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
// must be unmarshalled + migrated. Every CLI invocation after first
// init runs this path.
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
	state.ImageTag = "v0.7.3"
	state.Channel = "stable"

	if err := writeStateForBench(b, dir, state); err != nil {
		b.Fatalf("write state: %v", err)
	}
	b.ResetTimer()
	for b.Loop() {
		if _, err := Load(dir); err != nil {
			b.Fatalf("Load: %v", err)
		}
	}
}

// BenchmarkIsValidImageTag exercises the image-tag validation regex
// across a representative mix of inputs. Called every config set,
// every flag parse, every digest-pin verification.
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
	b.ResetTimer()
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

// writeStateForBench serialises “state“ to disk where Load() expects
// it. “state.DataDir“ is set to “dir“ so Save's SecurePath
// normalisation lands on the temp directory.
func writeStateForBench(b *testing.B, dir string, state State) error {
	b.Helper()
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	state.DataDir = dir
	return Save(state)
}
