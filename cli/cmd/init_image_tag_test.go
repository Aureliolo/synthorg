package cmd

import (
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

// A released binary pins the image tag matching its own version, so the
// stack an operator runs is the one that release published.
func TestResolveImageTagPinsTheReleasedVersion(t *testing.T) {
	restore := version.Version
	t.Cleanup(func() { version.Version = restore })
	version.Version = "0.9.4-dev.109"

	if got := resolveImageTag(""); got != "0.9.4-dev.109" {
		t.Errorf("resolveImageTag() = %q, want the CLI version", got)
	}
}

// A binary built from source reports version "dev" and has no published
// release to match, so it pins the `dev` tag: the newest prerelease, which
// tracks main. `latest` is the last NON-prerelease release, and this project
// publishes prereleases, so falling back to it silently pins whatever stable
// release happened last -- v0.9.3 from 2026-07-08 at the time of writing,
// a month behind the source being built.
func TestResolveImageTagFallsBackToDevNotLatest(t *testing.T) {
	restore := version.Version
	t.Cleanup(func() { version.Version = restore })
	version.Version = "dev"

	got := resolveImageTag("")
	if got == "latest" {
		t.Fatal("resolveImageTag() returned \"latest\" for a source build; " +
			"that tag is the last stable release, not the current source")
	}
	if got != "dev" {
		t.Errorf("resolveImageTag() = %q, want %q", got, "dev")
	}
}

func TestResolveImageTagHonoursTheOverride(t *testing.T) {
	restore := version.Version
	t.Cleanup(func() { version.Version = restore })
	version.Version = "dev"

	if got := resolveImageTag("v0.2.0"); got != "v0.2.0" {
		t.Errorf("resolveImageTag(%q) = %q, want the override", "v0.2.0", got)
	}
}

// An empty version string is the same situation as "dev": no release to
// match, so the current-source pointer is the honest answer.
func TestResolveImageTagTreatsEmptyVersionAsASourceBuild(t *testing.T) {
	restore := version.Version
	t.Cleanup(func() { version.Version = restore })
	version.Version = ""

	if got := resolveImageTag(""); got != "dev" {
		t.Errorf("resolveImageTag() = %q, want %q", got, "dev")
	}
}

// DefaultState is what a config written without going through init falls
// back to, so it must not disagree with resolveImageTag's fallback.
func TestDefaultStateImageTagMatchesTheSourceBuildFallback(t *testing.T) {
	restore := version.Version
	t.Cleanup(func() { version.Version = restore })
	version.Version = "dev"

	if got := config.DefaultState().ImageTag; got != resolveImageTag("") {
		t.Errorf("DefaultState().ImageTag = %q, want %q", got, resolveImageTag(""))
	}
}
