package cmd

import (
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

func TestHasSynthOrgDigests_RejectsTagMismatch(t *testing.T) {
	t.Parallel()
	state := config.State{
		ImageTag:         "0.8.3-dev.15",
		Sandbox:          true,
		VerifiedDigests:  fullSandboxedPins(false, ""),
		VerifiedImageTag: "0.8.3-dev.14",
	}
	if hasSynthOrgDigests(state) {
		t.Fatal("hasSynthOrgDigests must return false when VerifiedImageTag != ImageTag")
	}
}

func TestHasSynthOrgDigests_RejectsMissingSentinel(t *testing.T) {
	t.Parallel()
	state := config.State{
		ImageTag:        "0.8.3-dev.15",
		Sandbox:         true,
		VerifiedDigests: fullSandboxedPins(false, ""),
	}
	if hasSynthOrgDigests(state) {
		t.Fatal("hasSynthOrgDigests must return false when VerifiedImageTag is empty (legacy state)")
	}
}

func TestHasSynthOrgDigests_RejectsMissingPin(t *testing.T) {
	t.Parallel()
	pins := fullSandboxedPins(false, "")
	delete(pins, "sidecar")
	state := config.State{
		ImageTag:         "0.8.3-dev.15",
		Sandbox:          true,
		VerifiedDigests:  pins,
		VerifiedImageTag: "0.8.3-dev.15",
	}
	if hasSynthOrgDigests(state) {
		t.Fatal("hasSynthOrgDigests must return false when an enabled-image pin is missing")
	}
}

func TestHasSynthOrgDigests_HitsWhenSentinelAndKeysMatch(t *testing.T) {
	t.Parallel()
	state := config.State{
		ImageTag:         "0.8.3-dev.15",
		Sandbox:          true,
		FineTuning:       true,
		VerifiedDigests:  fullSandboxedPins(true, config.FineTuneVariantGPU),
		VerifiedImageTag: "0.8.3-dev.15",
	}
	if !hasSynthOrgDigests(state) {
		t.Fatal("hasSynthOrgDigests must return true when sentinel and all enabled-image pins match")
	}
}

func TestHasSynthOrgDigests_NoSandboxOnlyRequiresCoreImages(t *testing.T) {
	t.Parallel()
	state := config.State{
		ImageTag: "0.8.3-dev.15",
		Sandbox:  false,
		VerifiedDigests: map[string]string{
			"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
			"web":     "sha256:2222222222222222222222222222222222222222222222222222222222222222",
		},
		VerifiedImageTag: "0.8.3-dev.15",
	}
	if !hasSynthOrgDigests(state) {
		t.Fatal("hasSynthOrgDigests must hit when sandbox is disabled and core images are pinned")
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
	existing := map[string]string{"backend": "sha256:0000000000000000000000000000000000000000000000000000000000000000"}
	fresh := map[string]string{"backend": "sha256:1111111111111111111111111111111111111111111111111111111111111111"}

	_ = mergeVerifiedDigests(existing, fresh)

	if existing["backend"] != "sha256:0000000000000000000000000000000000000000000000000000000000000000" {
		t.Error("mergeVerifiedDigests must not mutate the existing map")
	}
	if fresh["backend"] != "sha256:1111111111111111111111111111111111111111111111111111111111111111" {
		t.Error("mergeVerifiedDigests must not mutate the fresh map")
	}
}
