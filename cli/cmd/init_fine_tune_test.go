package cmd

import (
	"testing"
)

// TestBuildState_FineTuneVariantGPU verifies that the TUI's default variant
// (index 0, "gpu") round-trips into State.FineTuningVariant when fine-tuning
// is enabled.
func TestBuildState_FineTuneVariantGPU(t *testing.T) {
	a := setupAnswers{
		dir:                mustAbs(t, t.TempDir()),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            true, // fine-tuning requires sandbox
		dockerSock:         defaultDockerSock(),
		logLevel:           "info",
		persistenceBackend: "sqlite",
		memoryBackend:      "sqlvector",
		busBackend:         "internal",
		encryptSecrets:     false,
		fineTuning:         true,
		fineTuneVariant:    "gpu",
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	if !state.FineTuning {
		t.Error("FineTuning = false, want true")
	}
	if state.FineTuningVariant != "gpu" {
		t.Errorf("FineTuningVariant = %q, want \"gpu\"", state.FineTuningVariant)
	}
	if got := state.FineTuneVariantOrDefault(); got != "gpu" {
		t.Errorf("FineTuneVariantOrDefault() = %q, want \"gpu\"", got)
	}
}

// TestBuildState_FineTuneVariantCPU verifies the CPU variant path.
func TestBuildState_FineTuneVariantCPU(t *testing.T) {
	a := setupAnswers{
		dir:                mustAbs(t, t.TempDir()),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            true,
		dockerSock:         defaultDockerSock(),
		logLevel:           "info",
		persistenceBackend: "sqlite",
		memoryBackend:      "sqlvector",
		busBackend:         "internal",
		encryptSecrets:     false,
		fineTuning:         true,
		fineTuneVariant:    "cpu",
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	if state.FineTuningVariant != "cpu" {
		t.Errorf("FineTuningVariant = %q, want \"cpu\"", state.FineTuningVariant)
	}
	if got := state.FineTuneVariantOrDefault(); got != "cpu" {
		t.Errorf("FineTuneVariantOrDefault() = %q, want \"cpu\"", got)
	}
}

// TestBuildState_FineTuneDisabledIgnoresVariant verifies that when fine-tuning
// is disabled, the variant value is still persisted (so the user can flip
// the toggle back later without losing their GPU/CPU choice) but does not
// affect runtime behavior because FineTuning gates image pulls.
func TestBuildState_FineTuneDisabledIgnoresVariant(t *testing.T) {
	a := setupAnswers{
		dir:                mustAbs(t, t.TempDir()),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "sqlite",
		memoryBackend:      "sqlvector",
		busBackend:         "internal",
		encryptSecrets:     false,
		fineTuning:         false,
		fineTuneVariant:    "cpu",
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	if state.FineTuning {
		t.Error("FineTuning = true, want false")
	}
	// Variant is still recorded -- Validate permits non-canonical values
	// only when FineTuning is false; canonical values remain round-tripped.
	if state.FineTuningVariant != "cpu" {
		t.Errorf("FineTuningVariant = %q, want \"cpu\"", state.FineTuningVariant)
	}
}
