package cmd

import (
	"maps"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// fullSandboxedPins returns a pin map populated with valid sha256 digests
// for every SynthOrg image of a sandbox-enabled config (backend / web /
// sandbox / sidecar, optionally fine-tune). Used to construct fixtures
// where hasSynthOrgDigests would otherwise reject for missing keys.
func fullSandboxedPins(fineTuning bool, variant string) map[string]string {
	pins := map[string]string{
		"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
		"web":     "sha256:2222222222222222222222222222222222222222222222222222222222222222",
		"sandbox": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
		"sidecar": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
	}
	if fineTuning {
		svc := "fine-tune-gpu"
		if variant == config.FineTuneVariantCPU {
			svc = "fine-tune-cpu"
		}
		pins[svc] = "sha256:5555555555555555555555555555555555555555555555555555555555555555"
	}
	return pins
}

func TestHasSynthOrgDigests(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name  string
		state config.State
		want  bool
	}{
		{
			name: "rejects tag mismatch",
			state: config.State{
				ImageTag:         "0.8.3-dev.15",
				Sandbox:          true,
				VerifiedDigests:  fullSandboxedPins(false, ""),
				VerifiedImageTag: "0.8.3-dev.14",
			},
			want: false,
		},
		{
			name: "rejects missing sentinel (legacy state)",
			state: config.State{
				ImageTag:        "0.8.3-dev.15",
				Sandbox:         true,
				VerifiedDigests: fullSandboxedPins(false, ""),
			},
			want: false,
		},
		{
			name: "rejects missing pin for enabled image",
			state: config.State{
				ImageTag: "0.8.3-dev.15",
				Sandbox:  true,
				VerifiedDigests: map[string]string{
					"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
					"web":     "sha256:2222222222222222222222222222222222222222222222222222222222222222",
					"sandbox": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
					// sidecar intentionally missing
				},
				VerifiedImageTag: "0.8.3-dev.15",
			},
			want: false,
		},
		{
			name: "hits when sentinel and all enabled-image pins match",
			state: config.State{
				ImageTag:         "0.8.3-dev.15",
				Sandbox:          true,
				FineTuning:       true,
				VerifiedDigests:  fullSandboxedPins(true, config.FineTuneVariantGPU),
				VerifiedImageTag: "0.8.3-dev.15",
			},
			want: true,
		},
		{
			name: "hits when sandbox disabled and only core images pinned",
			state: config.State{
				ImageTag: "0.8.3-dev.15",
				Sandbox:  false,
				VerifiedDigests: map[string]string{
					"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
					"web":     "sha256:2222222222222222222222222222222222222222222222222222222222222222",
				},
				VerifiedImageTag: "0.8.3-dev.15",
			},
			want: true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if got := hasSynthOrgDigests(tc.state); got != tc.want {
				t.Errorf("hasSynthOrgDigests() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestSynthOrgPins_ExcludesDHIKeys(t *testing.T) {
	t.Parallel()
	merged := map[string]string{
		"backend":                              "sha256:1111111111111111111111111111111111111111111111111111111111111111",
		"web":                                  "sha256:2222222222222222222222222222222222222222222222222222222222222222",
		"dhi:dhi.io/postgres:17.2":             "sha256:3333333333333333333333333333333333333333333333333333333333333333",
		"dhi:dhi.io/postgres:17.2:platform":    "sha256:4444444444444444444444444444444444444444444444444444444444444444",
		"dhi:dhi.io/postgres:17.2:attestation": "sha256:5555555555555555555555555555555555555555555555555555555555555555",
		"dhi:dhi.io/postgres:17.2:signature":   "sha256:6666666666666666666666666666666666666666666666666666666666666666",
		"dhi:dhi.io/nats:2.11":                 "sha256:7777777777777777777777777777777777777777777777777777777777777777",
	}
	got := synthOrgPins(merged)
	if len(got) != 2 {
		t.Fatalf("synthOrgPins returned %d keys, want 2 (only bare-name keys); got %v", len(got), got)
	}
	if _, ok := got["backend"]; !ok {
		t.Error("synthOrgPins dropped 'backend' key")
	}
	if _, ok := got["web"]; !ok {
		t.Error("synthOrgPins dropped 'web' key")
	}
	for k := range got {
		if len(k) >= 4 && k[:4] == "dhi:" {
			t.Errorf("synthOrgPins leaked DHI key %q", k)
		}
	}
}

func TestMergeVerifiedDigests_PreservesDHIKeys(t *testing.T) {
	t.Parallel()
	existing := map[string]string{
		"backend":                  "sha256:0000000000000000000000000000000000000000000000000000000000000000",
		"dhi:dhi.io/postgres:17.2": "sha256:aaaa000000000000000000000000000000000000000000000000000000000000",
		"dhi:dhi.io/nats:2.11":     "sha256:bbbb000000000000000000000000000000000000000000000000000000000000",
	}
	fresh := map[string]string{
		"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
		"web":     "sha256:2222222222222222222222222222222222222222222222222222222222222222",
	}
	got := mergeVerifiedDigests(existing, fresh)

	if got["backend"] != fresh["backend"] {
		t.Errorf("merge must overlay fresh on existing for shared key; got backend=%q, want %q", got["backend"], fresh["backend"])
	}
	if got["web"] != fresh["web"] {
		t.Errorf("merge must include new fresh keys; web=%q missing", got["web"])
	}
	if got["dhi:dhi.io/postgres:17.2"] != existing["dhi:dhi.io/postgres:17.2"] {
		t.Errorf("merge must preserve existing DHI postgres pin; got %q", got["dhi:dhi.io/postgres:17.2"])
	}
	if got["dhi:dhi.io/nats:2.11"] != existing["dhi:dhi.io/nats:2.11"] {
		t.Errorf("merge must preserve existing DHI nats pin; got %q", got["dhi:dhi.io/nats:2.11"])
	}
}

func TestMergeVerifiedDigests_NilInputsReturnNil(t *testing.T) {
	t.Parallel()
	if got := mergeVerifiedDigests(nil, nil); got != nil {
		t.Errorf("mergeVerifiedDigests(nil, nil) = %v, want nil so compose's nil-pin fallback path can fire", got)
	}
}

func TestMergeVerifiedDigests_DoesNotMutateInputs(t *testing.T) {
	t.Parallel()
	existing := map[string]string{
		"backend":                  "sha256:0000000000000000000000000000000000000000000000000000000000000000",
		"dhi:dhi.io/postgres:17.2": "sha256:aaaa000000000000000000000000000000000000000000000000000000000000",
	}
	fresh := map[string]string{
		"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
		"web":     "sha256:2222222222222222222222222222222222222222222222222222222222222222",
	}
	originalExisting := maps.Clone(existing)
	originalFresh := maps.Clone(fresh)

	_ = mergeVerifiedDigests(existing, fresh)

	if !maps.Equal(existing, originalExisting) {
		t.Errorf("mergeVerifiedDigests must not mutate the existing map; got %v, want %v", existing, originalExisting)
	}
	if !maps.Equal(fresh, originalFresh) {
		t.Errorf("mergeVerifiedDigests must not mutate the fresh map; got %v, want %v", fresh, originalFresh)
	}
}
