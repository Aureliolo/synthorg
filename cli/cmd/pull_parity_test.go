package cmd

import (
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/images"
)

// TestBuildPullItemsCoversEveryServiceName pins the invariant that let an
// image ship declared-but-never-fetched: images.ServiceNames is the single
// source of truth for "has this install pulled image X?", consumed by the
// health check, the auto-cleanup keep-set and diagnostics, but the only code
// that actually runs `docker pull` is buildPullItems. A name present in the
// former and absent from the latter is permanently missing on disk, which
// makes collectCurrentImageIDs return errImageNotLocal forever and silently
// disables auto-cleanup for the whole install.
func TestBuildPullItemsCoversEveryServiceName(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name     string
		sandbox  bool
		fineTune bool
		variant  string
	}{
		{name: "minimal"},
		{name: "sandbox", sandbox: true},
		{name: "fine_tune_gpu", fineTune: true, variant: "gpu"},
		{name: "fine_tune_cpu", fineTune: true, variant: "cpu"},
		{name: "sandbox_and_fine_tune", sandbox: true, fineTune: true, variant: "gpu"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			state := config.State{
				ImageTag:          "v1.2.3",
				Sandbox:           tc.sandbox,
				FineTuning:        tc.fineTune,
				FineTuningVariant: tc.variant,
			}

			pulled := make(map[string]bool)
			for _, item := range buildPullItems(state) {
				pulled[item.name] = true
			}

			want := images.ServiceNames(
				state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault(),
			)
			for _, svc := range want {
				if !pulled[svc] {
					t.Errorf(
						"images.ServiceNames lists %q but buildPullItems never pulls it; "+
							"it will be reported missing forever and disable auto-cleanup",
						svc,
					)
				}
			}
		})
	}
}

// TestBuildPullItemsPinsSandboxImagesByDigest guards that the standalone
// images the backend spawns over the Docker socket are digest-pinned from
// VerifiedDigests, so what the CLI verified is what the daemon runs.
func TestBuildPullItemsPinsSandboxImagesByDigest(t *testing.T) {
	t.Parallel()

	const digest = "sha256:" +
		"1111111111111111111111111111111111111111111111111111111111111111"

	state := config.State{
		ImageTag: "v1.2.3",
		Sandbox:  true,
		VerifiedDigests: map[string]string{
			"sandbox": digest,
			"sidecar": digest,
		},
	}

	refByName := make(map[string]string)
	for _, item := range buildPullItems(state) {
		refByName[item.name] = item.ref
	}

	for _, svc := range []string{"sandbox", "sidecar"} {
		ref, ok := refByName[svc]
		if !ok {
			t.Fatalf("no pull item for %q", svc)
		}
		if !contains(ref, digest) {
			t.Errorf("%q pull ref %q is not digest-pinned", svc, ref)
		}
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) &&
		haystack[len(haystack)-len(needle):] == needle
}
