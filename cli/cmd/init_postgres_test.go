package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// TestBuildState_Postgres verifies that selecting the postgres persistence
// backend in init generates a random password, sets the default port, and
// persists both in the resulting State.
func TestBuildState_Postgres(t *testing.T) {
	a := setupAnswers{
		dir:                mustAbs(t, t.TempDir()),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "postgres",
		memoryBackend:      "mem0",
		busBackend:         "internal",
		postgresPort:       0,
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	if state.PersistenceBackend != "postgres" {
		t.Errorf("PersistenceBackend = %q, want postgres", state.PersistenceBackend)
	}
	if state.PostgresPort != 3002 {
		t.Errorf("PostgresPort = %d, want 3002 (default)", state.PostgresPort)
	}
	if len(state.PostgresPassword) < 32 {
		t.Errorf("PostgresPassword length = %d, want >= 32", len(state.PostgresPassword))
	}
}

// TestBuildState_PostgresCustomPort verifies --postgres-port is honoured.
func TestBuildState_PostgresCustomPort(t *testing.T) {
	a := setupAnswers{
		dir:                mustAbs(t, t.TempDir()),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "postgres",
		memoryBackend:      "mem0",
		busBackend:         "internal",
		postgresPort:       5433,
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}
	if state.PostgresPort != 5433 {
		t.Errorf("PostgresPort = %d, want 5433", state.PostgresPort)
	}
}

// TestBuildState_Sqlite verifies the default path still works for SQLite.
func TestBuildState_Sqlite(t *testing.T) {
	a := setupAnswers{
		dir:                mustAbs(t, t.TempDir()),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "sqlite",
		memoryBackend:      "mem0",
		busBackend:         "internal",
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}
	if state.PersistenceBackend != "sqlite" {
		t.Errorf("PersistenceBackend = %q, want sqlite", state.PersistenceBackend)
	}
	if state.PostgresPassword != "" {
		t.Errorf("PostgresPassword should be empty for sqlite, got %q", state.PostgresPassword)
	}
}

// TestInitValidatePostgresFlag verifies --persistence-backend validation.
func TestInitValidatePostgresFlag(t *testing.T) {
	tests := []struct {
		name    string
		backend string
		wantErr bool
	}{
		{"sqlite", "sqlite", false},
		{"postgres", "postgres", false},
		{"invalid", "mysql", true},
		{"empty (default)", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			defer snapshotInitFlags()()
			initPersistenceBackend = tt.backend
			err := validateInitFlags("")
			if (err != nil) != tt.wantErr {
				t.Errorf("validateInitFlags() err=%v, wantErr=%v", err, tt.wantErr)
			}
		})
	}
}

// TestInitValidatePostgresPort verifies --postgres-port range validation
// and the backend-gating check (port must be paired with postgres backend).
func TestInitValidatePostgresPort(t *testing.T) {
	tests := []struct {
		name    string
		backend string // --persistence-backend value
		port    int    // --postgres-port value
		wantErr bool
	}{
		{"default port with postgres", "postgres", 0, false},
		{"valid 5432 with postgres", "postgres", 5432, false},
		{"too low with postgres", "postgres", 0 - 1, true},
		{"too high with postgres", "postgres", 65536, true},
		{"valid port rejected without postgres backend", "", 5432, true},
		{"valid port rejected with sqlite backend", "sqlite", 5432, true},
		{"unset port with sqlite backend", "sqlite", 0, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			defer snapshotInitFlags()()
			initPersistenceBackend = tt.backend
			initPostgresPort = tt.port
			err := validateInitFlags("")
			if (err != nil) != tt.wantErr {
				t.Errorf("validateInitFlags() err=%v, wantErr=%v", err, tt.wantErr)
			}
		})
	}
}

// TestInitValidatePostgresPort_ReinitPersistedBackend verifies that
// during re-init, an existing postgres config in dataDir allows
// --postgres-port without an explicit --persistence-backend flag.
func TestInitValidatePostgresPort_ReinitPersistedBackend(t *testing.T) {
	defer snapshotInitFlags()()
	dir := t.TempDir()
	absDir := mustAbs(t, dir)

	// Seed a postgres config in the target data directory.
	seed := setupAnswers{
		dir:                absDir,
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "postgres",
		memoryBackend:      "mem0",
		busBackend:         "internal",
		postgresPort:       3002,
	}
	seedState, err := buildState(seed)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}
	if _, err := writeInitFiles(seedState); err != nil {
		t.Fatalf("writeInitFiles: %v", err)
	}

	// Now simulate re-init: user passes --postgres-port 5433 without
	// --persistence-backend. validateInitFlags should accept it because
	// the persisted backend is postgres.
	initPersistenceBackend = ""
	initPostgresPort = 5433
	if err := validateInitFlags(absDir); err != nil {
		t.Errorf("validateInitFlags with persisted postgres config: %v", err)
	}

	// Sanity check: same flags with an empty data dir should reject
	// because there's no persisted state to fall back to.
	if err := validateInitFlags(""); err == nil {
		t.Error("expected error when persisted state is unavailable")
	}
}

// snapshotInitFlags captures every package-level init* flag variable that
// validateInitFlags reads, and returns a restore function. Callers
// “defer snapshotInitFlags()()“ so subtests are order-independent even when
// they mutate a single flag -- the full init flag state is restored on exit.
func snapshotInitFlags() func() {
	saved := struct {
		backendPort        int
		webPort            int
		sandbox            string
		imageTag           string
		channel            string
		logLevel           string
		busBackend         string
		persistenceBackend string
		postgresPort       int
	}{
		backendPort:        initBackendPort,
		webPort:            initWebPort,
		sandbox:            initSandbox,
		imageTag:           initImageTag,
		channel:            initChannel,
		logLevel:           initLogLevel,
		busBackend:         initBusBackend,
		persistenceBackend: initPersistenceBackend,
		postgresPort:       initPostgresPort,
	}
	return func() {
		initBackendPort = saved.backendPort
		initWebPort = saved.webPort
		initSandbox = saved.sandbox
		initImageTag = saved.imageTag
		initChannel = saved.channel
		initLogLevel = saved.logLevel
		initBusBackend = saved.busBackend
		initPersistenceBackend = saved.persistenceBackend
		initPostgresPort = saved.postgresPort
	}
}

// TestPostgresLifecycle_InitGeneratesWritableState simulates the init flow
// end-to-end: builds state for postgres, writes init files, re-reads config,
// verifies the password + port survive the round-trip (stop/start cycle
// preservation).
func TestPostgresLifecycle_InitGeneratesWritableState(t *testing.T) {
	dir := t.TempDir()
	a := setupAnswers{
		dir:                mustAbs(t, dir),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "postgres",
		memoryBackend:      "mem0",
		busBackend:         "internal",
		postgresPort:       3002,
	}

	state, err := buildState(a)
	if err != nil {
		t.Fatalf("buildState: %v", err)
	}

	// Write files as init would.
	safeDir, err := writeInitFiles(state)
	if err != nil {
		t.Fatalf("writeInitFiles: %v", err)
	}

	// Verify compose.yml contains the postgres service.
	composeBytes, err := os.ReadFile(filepath.Join(safeDir, "compose.yml"))
	if err != nil {
		t.Fatalf("reading compose.yml: %v", err)
	}
	composeYAML := string(composeBytes)
	if !strings.Contains(composeYAML, "postgres:") {
		t.Error("compose.yml should contain postgres service")
	}
	if !strings.Contains(composeYAML, "dhi.io/postgres:"+config.DefaultPostgresImageTag) {
		t.Error("compose.yml should use DHI postgres image")
	}
	if !strings.Contains(composeYAML, "synthorg-pgdata") {
		t.Error("compose.yml should declare synthorg-pgdata volume")
	}
	if !strings.Contains(composeYAML, "pg_isready") {
		t.Error("compose.yml should include pg_isready healthcheck")
	}
	if !strings.Contains(composeYAML, "SYNTHORG_DATABASE_URL") {
		t.Error("compose.yml should set SYNTHORG_DATABASE_URL on backend")
	}
	if strings.Contains(composeYAML, "SYNTHORG_DB_PATH") {
		t.Error("compose.yml should NOT set SYNTHORG_DB_PATH when postgres selected")
	}

	// Verify config.json persists password + port.
	configBytes, err := os.ReadFile(filepath.Join(safeDir, "config.json"))
	if err != nil {
		t.Fatalf("reading config.json: %v", err)
	}
	var persisted config.State
	if err := json.Unmarshal(configBytes, &persisted); err != nil {
		t.Fatalf("parsing config.json: %v", err)
	}
	if persisted.PersistenceBackend != "postgres" {
		t.Errorf("persisted PersistenceBackend = %q, want postgres", persisted.PersistenceBackend)
	}
	if persisted.PostgresPort != 3002 {
		t.Errorf("persisted PostgresPort = %d, want 3002", persisted.PostgresPort)
	}
	if persisted.PostgresPassword != state.PostgresPassword {
		t.Error("persisted PostgresPassword != original (stop/start preservation would fail)")
	}

	// Verify we can regenerate compose.yml from the persisted state
	// (simulates `synthorg start` reading the state and rendering compose).
	params, err := compose.ParamsFromState(persisted)
	if err != nil {
		t.Fatalf("ParamsFromState: %v", err)
	}
	regenerated, err := compose.Generate(params)
	if err != nil {
		t.Fatalf("regenerate compose: %v", err)
	}
	if !strings.Contains(string(regenerated), persisted.PostgresPassword) {
		t.Error("regenerated compose must contain the persisted password")
	}
}

// newReinitCmd builds a throwaway cobra.Command with the --postgres-port flag
// registered so tests can drive handleReinit() through the real code path,
// including cmd.Flags().Changed("postgres-port") checks.
func newReinitCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "init"}
	cmd.Flags().IntVar(&initPostgresPort, "postgres-port", 0, "")
	return cmd
}

// TestPostgresLifecycle_ReinitPreservesCustomPostgresPort drives handleReinit()
// through both behaviours we care about:
//
//   - when the user does NOT pass --postgres-port, the persisted custom port
//     (5433) must survive the re-init;
//   - when the user DOES pass --postgres-port with a different value (6543),
//     the explicit flag must win and the persisted value must be discarded.
//
// The previous revision of this test only exercised buildState + writeInitFiles
// + config.Load, which never reached the handleReinit flag-precedence path.
func TestPostgresLifecycle_ReinitPreservesCustomPostgresPort(t *testing.T) {
	defer snapshotInitFlags()()

	dir := t.TempDir()
	// ── First init: custom port 5433 ─────────────────────────────
	first := setupAnswers{
		dir:                mustAbs(t, dir),
		backendPortStr:     "3001",
		webPortStr:         "3000",
		sandbox:            false,
		dockerSock:         "",
		logLevel:           "info",
		persistenceBackend: "postgres",
		memoryBackend:      "mem0",
		busBackend:         "internal",
		postgresPort:       5433,
	}
	initialState, err := buildState(first)
	if err != nil {
		t.Fatalf("buildState (first): %v", err)
	}
	if _, err := writeInitFiles(initialState); err != nil {
		t.Fatalf("writeInitFiles (first): %v", err)
	}
	originalPassword := initialState.PostgresPassword

	// ── Re-init WITHOUT --postgres-port: persisted 5433 wins ─────
	t.Run("no flag preserves persisted port", func(t *testing.T) {
		defer snapshotInitFlags()()
		second := first
		second.postgresPort = 0 // user did not pass the flag
		newState, err := buildState(second)
		if err != nil {
			t.Fatalf("buildState (no flag): %v", err)
		}
		// buildState fills in DefaultState().PostgresPort (3002) when 0;
		// handleReinit should override that with the persisted 5433.
		cmd := newReinitCmd() // flag NOT .Changed()
		opts := &GlobalOpts{DataDir: mustAbs(t, dir), Yes: true}
		ok, err := handleReinit(cmd, &newState, opts)
		if err != nil || !ok {
			t.Fatalf("handleReinit (no flag): ok=%v err=%v", ok, err)
		}
		if newState.PostgresPort != 5433 {
			t.Errorf("PostgresPort = %d, want 5433 (persisted)", newState.PostgresPort)
		}
		if newState.PostgresPassword != originalPassword {
			t.Error("PostgresPassword should be preserved from persisted state")
		}
	})

	// ── Re-init WITH --postgres-port=6543: flag wins ─────────────
	t.Run("explicit flag overrides persisted port", func(t *testing.T) {
		defer snapshotInitFlags()()
		second := first
		second.postgresPort = 6543
		newState, err := buildState(second)
		if err != nil {
			t.Fatalf("buildState (flag): %v", err)
		}
		cmd := newReinitCmd()
		// Simulate user passing --postgres-port=6543 on the command line.
		if err := cmd.Flags().Set("postgres-port", "6543"); err != nil {
			t.Fatalf("cmd.Flags().Set: %v", err)
		}
		opts := &GlobalOpts{DataDir: mustAbs(t, dir), Yes: true}
		ok, err := handleReinit(cmd, &newState, opts)
		if err != nil || !ok {
			t.Fatalf("handleReinit (flag): ok=%v err=%v", ok, err)
		}
		if newState.PostgresPort != 6543 {
			t.Errorf(
				"PostgresPort = %d, want 6543 (explicit flag must win)",
				newState.PostgresPort,
			)
		}
		if newState.PostgresPassword != originalPassword {
			t.Error("PostgresPassword should still be preserved from persisted state")
		}
	})

	// ── Regenerate compose from persisted state and verify round-trip ──
	persisted, err := config.Load(mustAbs(t, dir))
	if err != nil {
		t.Fatalf("config.Load: %v", err)
	}
	params, err := compose.ParamsFromState(persisted)
	if err != nil {
		t.Fatalf("ParamsFromState: %v", err)
	}
	regenerated, err := compose.Generate(params)
	if err != nil {
		t.Fatalf("regenerate compose: %v", err)
	}
	if !strings.Contains(string(regenerated), "\"5433:5432\"") {
		t.Error("regenerated compose must contain custom postgres port mapping 5433:5432")
	}
	if !strings.Contains(string(regenerated), persisted.PostgresPassword) {
		t.Error("regenerated compose must contain the persisted password")
	}
}

func mustAbs(t *testing.T, p string) string {
	t.Helper()
	abs, err := filepath.Abs(p)
	if err != nil {
		t.Fatalf("filepath.Abs(%q): %v", p, err)
	}
	return abs
}

// Note: nats.conf / compose atomicity tests now live in
// cli/internal/compose/writer_test.go alongside the exported helpers.
