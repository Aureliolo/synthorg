package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// Secrets a re-init must carry forward verbatim. Regenerating any of them
// breaks an existing install: the master key decrypts stored connection
// secrets, the settings key decrypts the settings store, the cursor secret
// signs outstanding pagination tokens, and the Postgres password is the
// credential for an already-provisioned volume.
const (
	recoverySettingsKey  = "preserved-settings-key"
	recoveryCursorSecret = "preserved-cursor-secret"
	recoveryPGPassword   = "preserved-postgres-password-at-least-32-chars"
	recoveryMasterKey    = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)

// writeLegacyConfig persists a config whose memory_backend holds a value
// this release no longer recognises, alongside every irreplaceable secret.
// This is the exact on-disk shape that made `synthorg init` refuse to run.
func writeLegacyConfig(t *testing.T, dir string) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            dir,
		"image_tag":           "latest",
		"backend_port":        3001,
		"web_port":            3000,
		"persistence_backend": "postgres",
		"postgres_port":       3002,
		"postgres_password":   recoveryPGPassword,
		"memory_backend":      "mem0",
		"encrypt_secrets":     true,
		"master_key":          recoveryMasterKey,
		"settings_key":        recoverySettingsKey,
		"cursor_secret":       recoveryCursorSecret,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config.StatePath(dir), raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

// recoveryAnswers is the answer set a re-init of the writeLegacyConfig
// install collects: same topology, current defaults for the enums.
func recoveryAnswers(dir string) setupAnswers {
	return setupAnswers{
		dir:                dir,
		backendPortStr:     "3001",
		webPortStr:         "3000",
		logLevel:           "info",
		persistenceBackend: "postgres",
		memoryBackend:      config.DefaultState().MemoryBackend,
		busBackend:         "internal",
		postgresPort:       3002,
		encryptSecrets:     true,
	}
}

func assertSecretsPreserved(t *testing.T, s config.State) {
	t.Helper()
	for _, c := range []struct{ name, got, want string }{
		{"master_key", s.MasterKey, recoveryMasterKey},
		{"settings_key", s.SettingsKey, recoverySettingsKey},
		{"cursor_secret", s.CursorSecret, recoveryCursorSecret},
		{"postgres_password", s.PostgresPassword, recoveryPGPassword},
	} {
		if c.got != c.want {
			t.Errorf("%s = %q, want %q", c.name, c.got, c.want)
		}
	}
}

// TestReinitRecoversConfigWithRemovedEnumValue is the acceptance case for
// the reported failure: a config written by an older release carries a
// memory_backend value that release supported and this one does not.
// `synthorg init` is the documented way to repair that, so it must run and
// must not lose a single secret on the way through.
func TestReinitRecoversConfigWithRemovedEnumValue(t *testing.T) {
	defer snapshotInitFlags()()

	dir := t.TempDir()
	writeLegacyConfig(t, dir)

	newState, err := buildState(recoveryAnswers(dir))
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	cmd := newReinitCmd()
	opts := &GlobalOpts{DataDir: mustAbs(t, dir), Yes: true}
	proceed, err := handleReinit(cmd, &newState, opts)
	if err != nil {
		t.Fatalf("handleReinit must recover a config holding a removed enum value, got %v", err)
	}
	if !proceed {
		t.Fatal("handleReinit refused to proceed")
	}
	assertSecretsPreserved(t, newState)
	if newState.MemoryBackend != config.DefaultState().MemoryBackend {
		t.Errorf("MemoryBackend = %q, want the current default", newState.MemoryBackend)
	}

	// The repair has to survive the write, not just the in-memory carry.
	if _, err := writeInitFiles(newState); err != nil {
		t.Fatalf("writeInitFiles: %v", err)
	}
	reloaded, err := config.Load(mustAbs(t, dir))
	if err != nil {
		t.Fatalf("config.Load after repair: %v", err)
	}
	assertSecretsPreserved(t, reloaded)
	if len(reloaded.Coerced) != 0 {
		t.Errorf("repaired config still reports coercions: %+v", reloaded.Coerced)
	}
}

// TestReinitRecoversConfigFailingValidation covers the general case behind
// the enum one: any invariant breach must still surrender the secrets,
// because re-init is about to replace every field anyway. An out-of-range
// port is used deliberately -- it is not coercible, so this fails unless
// the re-init loader genuinely skips validation.
func TestReinitRecoversConfigFailingValidation(t *testing.T) {
	defer snapshotInitFlags()()

	dir := t.TempDir()
	writeLegacyConfig(t, dir)

	var body map[string]any
	raw, err := os.ReadFile(config.StatePath(dir))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	body["nats_client_port"] = 999999
	patched, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config.StatePath(dir), patched, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := config.Load(mustAbs(t, dir)); err == nil {
		t.Fatal("expected strict Load to reject the invalid config")
	}

	newState, err := buildState(recoveryAnswers(dir))
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	proceed, err := handleReinit(newReinitCmd(), &newState, &GlobalOpts{DataDir: mustAbs(t, dir), Yes: true})
	if err != nil {
		t.Fatalf("handleReinit must tolerate a config failing validation, got %v", err)
	}
	if !proceed {
		t.Fatal("handleReinit refused to proceed")
	}
	assertSecretsPreserved(t, newState)
}

// TestUnreadableConfigErrorNamesTheSecrets pins the rule that no error may
// steer an operator into deleting the only copy of their master key. The
// message must name every secret that has to come out of the file first.
func TestUnreadableConfigErrorNamesTheSecrets(t *testing.T) {
	// Not parallel: newReinitCmd registers flags whose pflag *Var calls
	// write the package-level init* variables at registration time, so two
	// concurrent tests calling it race on shared state.
	defer snapshotInitFlags()()

	dir := t.TempDir()
	if err := os.WriteFile(config.StatePath(dir), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}

	newState := config.DefaultState()
	newState.DataDir = dir
	_, err := handleReinit(newReinitCmd(), &newState, &GlobalOpts{DataDir: mustAbs(t, dir), Yes: true})
	if err == nil {
		t.Fatal("expected an error: an unparseable config cannot surrender its secrets")
	}

	msg := err.Error()
	for _, secret := range []string{"master_key", "settings_key", "cursor_secret", "postgres_password"} {
		if !strings.Contains(msg, secret) {
			t.Errorf("error message must name %s so it is preserved; got: %s", secret, msg)
		}
	}
	// "delete it manually to force a fresh init" is the exact advice that
	// destroyed installs. Any bare deletion instruction is a regression.
	for _, destructive := range []string{"delete it manually", "remove it and re-run"} {
		if strings.Contains(msg, destructive) {
			t.Errorf("error message must not advise %q; got: %s", destructive, msg)
		}
	}
}

// TestWriteInitFilesRefusesAConfigItCannotLoadBack closes the hole the
// tolerant re-init loader opens. Skipping validation is what lets init read
// a broken config, but the secrets it carries forward arrive unvalidated,
// so without a check at the write boundary a malformed master key would be
// persisted behind a success banner and brick every later command with no
// way back.
func TestWriteInitFilesRefusesAConfigItCannotLoadBack(t *testing.T) {
	defer snapshotInitFlags()()

	dir := t.TempDir()
	state, err := buildState(recoveryAnswers(dir))
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}
	// A Fernet key is 32 url-safe-base64 bytes; this is the shape a
	// hand-edited or truncated config carries forward.
	state.MasterKey = "not-a-fernet-key"

	if _, err := writeInitFiles(state); err == nil {
		t.Fatal("writeInitFiles must refuse a state that Validate rejects")
	} else {
		for _, secret := range []string{"master_key", "settings_key", "cursor_secret", "postgres_password"} {
			if !strings.Contains(err.Error(), secret) {
				t.Errorf("refusal must still name %s; got: %v", secret, err)
			}
		}
	}

	// Nothing may be left behind: a half-written install is the state an
	// operator cannot reason about.
	if _, err := os.Stat(config.StatePath(dir)); !os.IsNotExist(err) {
		t.Errorf("config.json must not exist after a refused write, stat err = %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "compose.yml")); !os.IsNotExist(err) {
		t.Errorf("compose.yml must not exist after a refused write, stat err = %v", err)
	}
}

// TestReinitRecoversWhenPersistedDataDirIsUnusable covers the field that
// cannot be repaired by re-reading it: a data_dir that fails SecurePath.
// Refusing over it would strand the install exactly the way this whole
// change exists to prevent, and it is pointless besides -- init overwrites
// data_dir with the caller's --data-dir. Reachable in practice: a POSIX
// absolute path persisted by one machine is not absolute on Windows.
func TestReinitRecoversWhenPersistedDataDirIsUnusable(t *testing.T) {
	defer snapshotInitFlags()()

	dir := t.TempDir()
	writeLegacyConfig(t, dir)

	var body map[string]any
	raw, err := os.ReadFile(config.StatePath(dir))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	body["data_dir"] = filepath.Join("relative", "synthorg")
	patched, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(config.StatePath(dir), patched, 0o600); err != nil {
		t.Fatal(err)
	}

	newState, err := buildState(recoveryAnswers(dir))
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}
	proceed, err := handleReinit(newReinitCmd(), &newState, &GlobalOpts{DataDir: mustAbs(t, dir), Yes: true})
	if err != nil {
		t.Fatalf("handleReinit must tolerate an unusable persisted data_dir, got %v", err)
	}
	if !proceed {
		t.Fatal("handleReinit refused to proceed")
	}
	assertSecretsPreserved(t, newState)
	if newState.DataDir != mustAbs(t, dir) {
		t.Errorf("DataDir = %q, want the caller's --data-dir %q", newState.DataDir, mustAbs(t, dir))
	}
}
