package cmd

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// TestBuildState_EncryptSecretsDefault verifies that the encryption
// toggle propagates into the persisted State. Safe-by-default: the
// caller sets encryptSecrets=true when the user does not flip the
// toggle.
func TestBuildState_EncryptSecretsDefault(t *testing.T) {
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
		encryptSecrets:     true,
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	if !state.EncryptSecrets {
		t.Error("EncryptSecrets = false, want true (follows the toggle)")
	}
	if state.MasterKey == "" {
		t.Error("MasterKey is empty; expected a generated Fernet key")
	}
	// Fernet keys are URL-safe base64 of 32 bytes -> 44 characters
	// (42 payload + 2 padding '=').
	if len(state.MasterKey) != 44 {
		t.Errorf("MasterKey length = %d, want 44 (Fernet URL-safe base64)", len(state.MasterKey))
	}
	// URL-safe base64 may contain '_' and '-'; assert no regular '+' or '/'.
	if strings.ContainsAny(state.MasterKey, "+/") {
		t.Errorf("MasterKey %q contains '+' or '/' (not URL-safe)", state.MasterKey)
	}
}

// TestBuildState_EncryptSecretsDisabled verifies that opting out via
// encryptSecrets=false still generates and stores a master key (so
// the user can flip the toggle back on later without orphaning
// previously-stored ciphertext). The compose template gates the env
// var on EncryptSecrets AND MasterKey, so an unused key never leaks
// to the container.
func TestBuildState_EncryptSecretsDisabled(t *testing.T) {
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
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	if state.EncryptSecrets {
		t.Error("EncryptSecrets = true, want false")
	}
	if state.MasterKey == "" {
		t.Error("MasterKey should still be generated so re-enabling encryption doesn't orphan future ciphertext")
	}
}

// TestHandleReinit_YesPreservesMasterKey verifies that the
// non-interactive re-init path (--yes) preserves the existing
// MasterKey. Regenerating the key would silently orphan every
// stored connection secret.
func TestHandleReinit_YesPreservesMasterKey(t *testing.T) {
	oldKey := "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
	oldSettingsKey := "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
	dir := mustAbs(t, t.TempDir())
	oldState := config.DefaultState()
	oldState.DataDir = dir
	oldState.PersistenceBackend = "sqlite"
	oldState.MasterKey = oldKey
	oldState.SettingsKey = oldSettingsKey
	oldState.EncryptSecrets = true
	if err := config.Save(oldState); err != nil {
		t.Fatalf("config.Save: %v", err)
	}

	// Simulate a re-init that generated NEW keys via buildState.
	newState := oldState
	newState.MasterKey = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX="
	newState.SettingsKey = "YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY="

	cmd := &cobra.Command{Use: "init"}
	opts := &GlobalOpts{Yes: true}

	proceed, err := handleReinit(cmd, &newState, opts)
	if err != nil {
		t.Fatalf("handleReinit: %v", err)
	}
	if !proceed {
		t.Fatal("handleReinit returned proceed=false with --yes")
	}

	if newState.MasterKey != oldKey {
		t.Errorf("MasterKey = %q, want preserved %q", newState.MasterKey, oldKey)
	}
	if newState.SettingsKey != oldSettingsKey {
		t.Errorf("SettingsKey = %q, want preserved %q", newState.SettingsKey, oldSettingsKey)
	}
}

// TestHandleReinit_YesNoExistingMasterKeyKeepsNew verifies that re-
// init with an old state that has no master key keeps the newly
// generated one (upgrade path for installs that predate the
// encrypt-secrets toggle).
func TestHandleReinit_YesNoExistingMasterKeyKeepsNew(t *testing.T) {
	dir := mustAbs(t, t.TempDir())
	oldState := config.DefaultState()
	oldState.DataDir = dir
	oldState.PersistenceBackend = "sqlite"
	oldState.MasterKey = "" // old install, no master key
	oldState.SettingsKey = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
	oldState.EncryptSecrets = true
	if err := config.Save(oldState); err != nil {
		t.Fatalf("config.Save: %v", err)
	}

	newKey := "NEWKEYNEWKEYNEWKEYNEWKEYNEWKEYNEWKEYNEWKEY="
	newState := oldState
	newState.MasterKey = newKey

	cmd := &cobra.Command{Use: "init"}
	opts := &GlobalOpts{Yes: true}

	proceed, err := handleReinit(cmd, &newState, opts)
	if err != nil {
		t.Fatalf("handleReinit: %v", err)
	}
	if !proceed {
		t.Fatal("handleReinit returned proceed=false with --yes")
	}
	if newState.MasterKey != newKey {
		t.Errorf("MasterKey = %q, want newly generated %q", newState.MasterKey, newKey)
	}
}
