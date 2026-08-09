package cmd

import (
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

// setVersion pins the compiled-in version for one test and restores it
// afterwards. Callers must not run in parallel: version.Version is a
// package-level var, so a parallel sibling would observe the mutation.
func setVersion(t *testing.T, v string) {
	t.Helper()
	restore := version.Version
	t.Cleanup(func() { version.Version = restore })
	version.Version = v
}

func TestResolveImageTag(t *testing.T) {
	tests := []struct {
		name     string
		override string
		ver      string
		want     string
	}{
		{
			name: "released binary pins its own version",
			ver:  "0.9.4-dev.109", want: "0.9.4-dev.109",
		},
		{
			// `latest` is applied only on a `v*` tag whose ref carries no
			// `-dev.`, and this project publishes `-dev.N` prereleases, so
			// it names the last stable release rather than this tree.
			name: "source build pins dev, never latest",
			ver:  "dev", want: "dev",
		},
		{
			name: "empty version is a source build too",
			ver:  "", want: "dev",
		},
		{
			name:     "an override wins",
			override: "v0.2.0", ver: "dev", want: "v0.2.0",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			setVersion(t, tt.ver)

			if got := resolveImageTag(tt.override); got != tt.want {
				t.Errorf("resolveImageTag(%q) = %q, want %q", tt.override, got, tt.want)
			}
		})
	}
}

// A version that is neither empty nor the source-build sentinel but is not
// a usable tag came from outside this binary, so it falls back to the
// stable channel. Malformed input must not be what moves an install onto
// prereleases.
func TestImageTagForVersionFallsBackToStableOnGarbage(t *testing.T) {
	if got := config.ImageTagForVersion("not a tag!"); got != config.StableImageTag {
		t.Errorf("ImageTagForVersion(garbage) = %q, want %q", got, config.StableImageTag)
	}
}

// `synthorg config unset image_tag` restores DefaultState().ImageTag. A
// released binary must land back on its own release, not on the prerelease
// channel: unsetting an override is not a request to change channel.
func TestConfigUnsetImageTagKeepsAReleaseBinaryOffPrereleases(t *testing.T) {
	setVersion(t, "1.2.3")
	state := config.State{ImageTag: "0.9.0"}

	configResetters["image_tag"](&state, config.DefaultState())

	if state.ImageTag == config.SourceBuildImageTag {
		t.Fatalf("config unset image_tag put a released binary on %q",
			config.SourceBuildImageTag)
	}
	if state.ImageTag != "1.2.3" {
		t.Errorf("ImageTag = %q, want the running binary's release %q",
			state.ImageTag, "1.2.3")
	}
}

// A source build has no release to fall back to, so the same reset lands
// on the prerelease tag.
func TestConfigUnsetImageTagOnASourceBuildRestoresDev(t *testing.T) {
	setVersion(t, config.SourceBuildVersion)
	state := config.State{ImageTag: "0.9.0"}

	configResetters["image_tag"](&state, config.DefaultState())

	if state.ImageTag != config.SourceBuildImageTag {
		t.Errorf("ImageTag = %q, want %q", state.ImageTag, config.SourceBuildImageTag)
	}
}

// init and update must answer "which tag does this binary pin" the same
// way. When they disagree, update re-pins whatever init chose.
func TestInitAndUpdateAgreeOnTheTag(t *testing.T) {
	for _, ver := range []string{"dev", "", "1.2.3", "v1.2.3"} {
		t.Run(ver, func(t *testing.T) {
			setVersion(t, ver)

			initTag := resolveImageTag("")

			if updateTag := targetImageTag(ver); updateTag != initTag {
				t.Errorf("targetImageTag(%q) = %q, but init pinned %q",
					ver, updateTag, initTag)
			}
		})
	}
}
