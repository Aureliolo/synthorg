package config

import (
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

func TestDefaultState(t *testing.T) {
	s := DefaultState()
	if s.BackendPort != 3001 {
		t.Errorf("BackendPort = %d, want 3001", s.BackendPort)
	}
	if s.WebPort != 3000 {
		t.Errorf("WebPort = %d, want 3000", s.WebPort)
	}
	if s.ImageTag != "latest" {
		t.Errorf("ImageTag = %q, want latest", s.ImageTag)
	}
	if s.LogLevel != "info" {
		t.Errorf("LogLevel = %q, want info", s.LogLevel)
	}
	if !s.Sandbox {
		t.Error("Sandbox should default to true")
	}
	if s.DataDir == "" {
		t.Error("DataDir should not be empty")
	}
	if s.PersistenceBackend != "sqlite" {
		t.Errorf("PersistenceBackend = %q, want sqlite", s.PersistenceBackend)
	}
	if s.MemoryBackend != "sqlvector" {
		t.Errorf("MemoryBackend = %q, want sqlvector", s.MemoryBackend)
	}
	if s.SettingsKey != "" {
		t.Errorf("SettingsKey should default to empty, got %q", s.SettingsKey)
	}
	if s.MasterKey != "" {
		t.Errorf("MasterKey should default to empty, got %q", s.MasterKey)
	}
	if !s.EncryptSecrets {
		t.Errorf("EncryptSecrets = %v, want true (safe-by-default)", s.EncryptSecrets)
	}
	if s.AutoCleanup {
		t.Error("AutoCleanup should default to false")
	}
}

func TestSaveAndLoad(t *testing.T) {
	tmp := t.TempDir()
	// 44-char URL-safe base64 that decodes to 32 bytes (valid Fernet key).
	validFernetKey := "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
	s := State{
		DataDir:            tmp,
		ImageTag:           "v0.1.5",
		BackendPort:        9000,
		WebPort:            4000,
		LogLevel:           "debug",
		JWTSecret:          "test-secret",
		SettingsKey:        "test-settings-key",
		MasterKey:          validFernetKey,
		EncryptSecrets:     true,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
	}

	if err := Save(s); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if loaded.BackendPort != s.BackendPort {
		t.Errorf("BackendPort = %d, want %d", loaded.BackendPort, s.BackendPort)
	}
	if loaded.ImageTag != s.ImageTag {
		t.Errorf("ImageTag = %q, want %q", loaded.ImageTag, s.ImageTag)
	}
	if loaded.JWTSecret != s.JWTSecret {
		t.Errorf("JWTSecret = %q, want %q", loaded.JWTSecret, s.JWTSecret)
	}
	if loaded.SettingsKey != s.SettingsKey {
		t.Errorf("SettingsKey = %q, want %q", loaded.SettingsKey, s.SettingsKey)
	}
	if loaded.MasterKey != s.MasterKey {
		t.Errorf("MasterKey = %q, want %q", loaded.MasterKey, s.MasterKey)
	}
	if loaded.EncryptSecrets != s.EncryptSecrets {
		t.Errorf("EncryptSecrets = %v, want %v", loaded.EncryptSecrets, s.EncryptSecrets)
	}
	if loaded.WebPort != s.WebPort {
		t.Errorf("WebPort = %d, want %d", loaded.WebPort, s.WebPort)
	}
	if loaded.LogLevel != s.LogLevel {
		t.Errorf("LogLevel = %q, want %q", loaded.LogLevel, s.LogLevel)
	}
	if loaded.PersistenceBackend != s.PersistenceBackend {
		t.Errorf("PersistenceBackend = %q, want %q", loaded.PersistenceBackend, s.PersistenceBackend)
	}
	if loaded.MemoryBackend != s.MemoryBackend {
		t.Errorf("MemoryBackend = %q, want %q", loaded.MemoryBackend, s.MemoryBackend)
	}
}

// TestLoadForTeardown covers the best-effort teardown loader: it must
// never run Validate and never fail on a missing, unreadable, or invalid
// config, so wipe / uninstall can always parse what they can and still
// tear down the rest.
func TestLoadForTeardown(t *testing.T) {
	t.Parallel()
	t.Run("missing file returns seeded state, no error", func(t *testing.T) {
		tmp := t.TempDir()
		s, err := LoadForTeardown(tmp)
		if err != nil {
			t.Fatalf("LoadForTeardown on missing file: unexpected err %v", err)
		}
		if s.DataDir != filepath.Clean(tmp) {
			t.Errorf("DataDir = %q, want %q", s.DataDir, filepath.Clean(tmp))
		}
	})

	t.Run("corrupt JSON returns seeded state plus advisory error", func(t *testing.T) {
		tmp := t.TempDir()
		if err := os.WriteFile(StatePath(tmp), []byte("{not json"), 0o600); err != nil {
			t.Fatal(err)
		}
		s, err := LoadForTeardown(tmp)
		if err == nil {
			t.Error("expected an advisory parse error for corrupt JSON")
		}
		if s.DataDir != filepath.Clean(tmp) {
			t.Errorf("DataDir = %q, want %q (seeded fallback)", s.DataDir, filepath.Clean(tmp))
		}
	})

	t.Run("config that fails strict Validate still loads", func(t *testing.T) {
		tmp := t.TempDir()
		// encrypt_secrets=true with an empty master_key AND an out-of-range
		// port both fail strict Validate; teardown must tolerate them.
		raw, err := json.Marshal(map[string]any{
			"data_dir":            tmp,
			"encrypt_secrets":     true,
			"backend_port":        999999,
			"persistence_backend": "sqlite",
			"memory_backend":      "sqlvector",
		})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
			t.Fatal(err)
		}
		// Sanity: strict Load rejects it.
		if _, err := Load(tmp); err == nil {
			t.Fatal("expected strict Load to reject the invalid config")
		}
		s, err := LoadForTeardown(tmp)
		if err != nil {
			t.Fatalf("LoadForTeardown must tolerate an invalid config, got %v", err)
		}
		if s.BackendPort != 999999 {
			t.Errorf("parsed fields should survive: BackendPort = %d, want 999999", s.BackendPort)
		}
	})

	t.Run("config omitting data_dir falls back to the CLI-supplied dir", func(t *testing.T) {
		tmp := t.TempDir()
		// A persisted config with NO data_dir field must not let DefaultState's
		// platform default leak through: teardown targeting must use the
		// caller-supplied dir, never drift to the platform default.
		raw, err := json.Marshal(map[string]any{
			"backend_port":        3001,
			"persistence_backend": "sqlite",
			"memory_backend":      "sqlvector",
		})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
			t.Fatal(err)
		}
		s, err := LoadForTeardown(tmp)
		if err != nil {
			t.Fatalf("LoadForTeardown with omitted data_dir: unexpected err %v", err)
		}
		if s.DataDir != filepath.Clean(tmp) {
			t.Errorf("DataDir = %q, want %q (caller-supplied dir)", s.DataDir, filepath.Clean(tmp))
		}
	})
}

// TestLoadForReinit covers the re-init loader. It must surrender the
// secrets from a config that fails strict validation -- that is the whole
// point, since re-init overwrites every other field anyway -- while still
// failing hard when the file cannot be read at all, because proceeding
// without master_key / settings_key / cursor_secret / postgres_password
// would orphan every stored ciphertext.
func TestLoadForReinit(t *testing.T) {
	t.Parallel()

	const (
		settingsKey    = "carried-settings-key"
		cursorSecret   = "carried-cursor-secret"
		pgPassword     = "carried-postgres-password-at-least-32-chars"
		validFernetKey = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
	)

	writeConfig := func(t *testing.T, tmp string, extra map[string]any) {
		t.Helper()
		body := map[string]any{
			"data_dir":            tmp,
			"backend_port":        3001,
			"web_port":            3000,
			"persistence_backend": "postgres",
			"postgres_port":       3002,
			"postgres_password":   pgPassword,
			"encrypt_secrets":     true,
			"master_key":          validFernetKey,
			"settings_key":        settingsKey,
			"cursor_secret":       cursorSecret,
		}
		maps.Copy(body, extra)
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(StatePath(tmp), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	assertSecrets := func(t *testing.T, s State) {
		t.Helper()
		if s.MasterKey != validFernetKey {
			t.Errorf("MasterKey = %q, want %q", s.MasterKey, validFernetKey)
		}
		if s.SettingsKey != settingsKey {
			t.Errorf("SettingsKey = %q, want %q", s.SettingsKey, settingsKey)
		}
		if s.CursorSecret != cursorSecret {
			t.Errorf("CursorSecret = %q, want %q", s.CursorSecret, cursorSecret)
		}
		if s.PostgresPassword != pgPassword {
			t.Errorf("PostgresPassword = %q, want %q", s.PostgresPassword, pgPassword)
		}
	}

	t.Run("a value no allowlist accepts still surrenders every secret", func(t *testing.T) {
		t.Parallel()
		tmp := t.TempDir()
		// An out-of-range port fails strict Validate and, unlike a stale
		// enum, is not coercible -- so this proves LoadForReinit skips
		// validation rather than merely riding on Coerce.
		writeConfig(t, tmp, map[string]any{"nats_client_port": 999999})
		if _, err := Load(tmp); err == nil {
			t.Fatal("expected strict Load to reject the invalid config")
		}
		s, err := LoadForReinit(tmp)
		if err != nil {
			t.Fatalf("LoadForReinit must tolerate an invalid config, got %v", err)
		}
		assertSecrets(t, s)
	})

	t.Run("a removed enum value still surrenders every secret", func(t *testing.T) {
		t.Parallel()
		tmp := t.TempDir()
		writeConfig(t, tmp, map[string]any{"memory_backend": "mem0"})
		s, err := LoadForReinit(tmp)
		if err != nil {
			t.Fatalf("LoadForReinit: %v", err)
		}
		assertSecrets(t, s)
	})

	t.Run("missing file is a hard error", func(t *testing.T) {
		t.Parallel()
		if _, err := LoadForReinit(t.TempDir()); err == nil {
			t.Error("expected an error: there are no secrets to carry forward")
		}
	})

	t.Run("corrupt JSON is a hard error", func(t *testing.T) {
		t.Parallel()
		tmp := t.TempDir()
		if err := os.WriteFile(StatePath(tmp), []byte("{not json"), 0o600); err != nil {
			t.Fatal(err)
		}
		_, err := LoadForReinit(tmp)
		if err == nil {
			t.Fatal("expected a parse error: the secrets cannot be recovered")
		}
		if !errors.Is(err, ErrParsing) {
			t.Errorf("error = %v, want it to wrap ErrParsing", err)
		}
	})

	t.Run("config omitting data_dir falls back to the CLI-supplied dir", func(t *testing.T) {
		t.Parallel()
		tmp := t.TempDir()
		writeConfig(t, tmp, map[string]any{"data_dir": ""})
		s, err := LoadForReinit(tmp)
		if err != nil {
			t.Fatalf("LoadForReinit: %v", err)
		}
		if s.DataDir != filepath.Clean(tmp) {
			t.Errorf("DataDir = %q, want %q", s.DataDir, filepath.Clean(tmp))
		}
	})
}

// TestValidateFernetKey covers the MasterKey format check that gates
// invalid keys before they can be injected as SYNTHORG_MASTER_KEY at
// container start time.
func TestValidateFernetKey(t *testing.T) {
	tests := []struct {
		name    string
		key     string
		wantErr bool
	}{
		{
			name:    "valid 44-char Fernet key",
			key:     "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
			wantErr: false,
		},
		{
			name:    "too short",
			key:     "short",
			wantErr: true,
		},
		{
			name:    "44 chars but not base64",
			key:     "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!=",
			wantErr: true,
		},
		{
			name:    "43 chars (missing padding)",
			key:     "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
			wantErr: true,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := validateFernetKey(tc.key)
			if (err != nil) != tc.wantErr {
				t.Errorf("validateFernetKey(%q) err = %v, wantErr %v", tc.key, err, tc.wantErr)
			}
		})
	}
}

// TestLoadRejectsInvalidMasterKey ensures a malformed key under
// EncryptSecrets=true surfaces as a load error instead of silently
// reaching the backend container.
func TestLoadRejectsInvalidMasterKey(t *testing.T) {
	tmp := t.TempDir()
	s := State{
		DataDir:            tmp,
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
		MasterKey:          "not-a-valid-fernet-key",
		EncryptSecrets:     true,
	}
	if err := Save(s); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, err := Load(tmp); err == nil {
		t.Fatal("Load succeeded; expected error for malformed master_key")
	}
}

func TestSaveCreatesDirectory(t *testing.T) {
	tmp := t.TempDir()
	nested := filepath.Join(tmp, "nested", "deep")
	s := State{
		DataDir:     nested,
		ImageTag:    "latest",
		BackendPort: 3001,
		WebPort:     3000,
		LogLevel:    "info",
	}

	if err := Save(s); err != nil {
		t.Fatalf("Save to nested dir: %v", err)
	}

	// Verify the file exists.
	if _, err := os.Stat(StatePath(nested)); err != nil {
		t.Fatalf("config file should exist: %v", err)
	}
}

func TestSaveFilePermissions(t *testing.T) {
	tmp := t.TempDir()
	s := State{DataDir: tmp, ImageTag: "latest", BackendPort: 3001, WebPort: 3000, LogLevel: "info", JWTSecret: "secret"}

	if err := Save(s); err != nil {
		t.Fatalf("Save: %v", err)
	}

	// Verify the file is valid JSON.
	path := StatePath(tmp)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var loaded State
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("saved file is not valid JSON: %v", err)
	}

	// Verify file permissions (0600 -- owner read/write only).
	// Skip on Windows where Unix permissions are not enforced.
	if runtime.GOOS != "windows" {
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		perm := info.Mode().Perm()
		if perm != 0o600 {
			t.Errorf("file permissions = %o, want 0600", perm)
		}
	}
}

func TestLoadMissing(t *testing.T) {
	tmp := t.TempDir()
	s, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load missing file: %v", err)
	}
	// Should return defaults.
	if s.BackendPort != 3001 {
		t.Errorf("expected default BackendPort 3001, got %d", s.BackendPort)
	}
	// Conservative fallback: sandbox disabled when no config exists.
	if s.Sandbox {
		t.Error("Sandbox should be false when config file is missing")
	}
}

func TestLoadInvalid(t *testing.T) {
	tmp := t.TempDir()
	if err := os.WriteFile(filepath.Join(tmp, stateFileName), []byte("{invalid"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := Load(tmp)
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

// TestLoadRejectsInvalidBackends pins the deliberate exception to the
// recovery contract. Every other closed-set field is coerced to its
// default so a removed value cannot brick the CLI; these two are not,
// because they select WHERE DATA LIVES. Defaulting persistence_backend
// would have `start` regenerate compose without postgres and bring the
// stack up against an empty SQLite database, which also re-arms the
// unauthenticated first-run admin claim. Failing the load is the safe
// outcome; LoadTolerant is how doctor still reports on it.
//
// Each fixture sets encrypt_secrets false deliberately: with it left on,
// the missing master key fails validation first and this test would pass
// without ever reaching the backend checks.
func TestLoadRejectsInvalidBackends(t *testing.T) {
	tests := []struct {
		name     string
		persist  string
		memory   string
		wantMsgs []string
	}{
		{"empty persistence", "", "sqlvector", []string{"persistence_backend"}},
		{"empty memory", "sqlite", "", []string{"memory_backend"}},
		{"unknown persistence", "a-store-that-was-removed", "sqlvector", []string{"persistence_backend"}},
		{"unknown memory", "sqlite", "a-store-that-was-removed", []string{"memory_backend"}},
		{"both empty", "", "", []string{"persistence_backend"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmp := t.TempDir()
			raw, err := json.Marshal(map[string]any{
				"data_dir":            tmp,
				"image_tag":           "latest",
				"backend_port":        3001,
				"web_port":            3000,
				"log_level":           "info",
				"encrypt_secrets":     false,
				"persistence_backend": tt.persist,
				"memory_backend":      tt.memory,
			})
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(tmp, stateFileName), raw, 0o600); err != nil {
				t.Fatal(err)
			}
			_, err = Load(tmp)
			if err == nil {
				t.Fatalf("expected validation error for persist=%q memory=%q", tt.persist, tt.memory)
			}
			for _, want := range tt.wantMsgs {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error must name %s, got: %v", want, err)
				}
			}
		})
	}
}

// TestEnumValidators covers every closed-set validator in one table.
//
// All are case-sensitive by design: a persisted value is compared against
// its allowlist verbatim, never folded, so "STABLE" is as invalid as
// "nightly" and both take the same coercion path. The empty string is
// invalid for all of them; whether that is a coercion or a legitimate
// "use the default" is decided by the enumFields table in coerce.go, not
// here.
func TestEnumValidators(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		fn      func(string) bool
		valid   []string
		invalid []string
	}{
		{
			name:    "IsValidChannel",
			fn:      IsValidChannel,
			valid:   []string{"stable", "dev"},
			invalid: []string{"", "nightly", "STABLE", "Dev"},
		},
		{
			name:  "IsValidLogLevel",
			fn:    IsValidLogLevel,
			valid: []string{"debug", "info", "warn", "error"},
			// "warning" is the near-miss worth pinning: it is the spelling
			// most other tools accept, and this one does not.
			invalid: []string{"", "warning", "trace", "WARN"},
		},
		{
			name:    "IsValidColorMode",
			fn:      IsValidColorMode,
			valid:   []string{"always", "auto", "never"},
			invalid: []string{"", "none", "Always", "AUTO", "NEVER"},
		},
		{
			name:    "IsValidOutputMode",
			fn:      IsValidOutputMode,
			valid:   []string{"text", "json"},
			invalid: []string{"", "yaml", "xml", "JSON", "TEXT"},
		},
		{
			name:    "IsValidTimestampMode",
			fn:      IsValidTimestampMode,
			valid:   []string{"relative", "iso8601"},
			invalid: []string{"", "unix", "rfc3339", "ISO8601", "Relative"},
		},
		{
			name:    "IsValidHintsMode",
			fn:      IsValidHintsMode,
			valid:   []string{"always", "auto", "never"},
			invalid: []string{"", "none", "Always", "NEVER"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			for _, in := range tt.valid {
				if !tt.fn(in) {
					t.Errorf("%s(%q) = false, want true", tt.name, in)
				}
			}
			for _, in := range tt.invalid {
				if tt.fn(in) {
					t.Errorf("%s(%q) = true, want false", tt.name, in)
				}
			}
		})
	}
}

func TestDisplayChannel(t *testing.T) {
	tests := []struct {
		channel string
		want    string
	}{
		{"", "stable"},
		{"stable", "stable"},
		{"dev", "dev"},
	}
	for _, tt := range tests {
		t.Run(tt.channel, func(t *testing.T) {
			s := State{Channel: tt.channel}
			if got := s.DisplayChannel(); got != tt.want {
				t.Errorf("DisplayChannel() = %q, want %q", got, tt.want)
			}
		})
	}
}

// TestLoadCoercesInvalidChannelAndLogLevel pins the load-time contract for
// the two enums a user is most likely to hand-edit: an unrecognised value
// never fails the load, it falls back to the default and is reported on
// State.Coerced for the caller to warn about. Refusing to load would take
// down every command including `init` and `doctor`, which exist to repair
// exactly this.
func TestLoadCoercesInvalidChannelAndLogLevel(t *testing.T) {
	tests := []struct {
		name     string
		channel  string
		logLevel string
		// omitted drops the log_level key entirely, as distinct from
		// writing it as an empty string.
		omitted      bool
		wantCoerced  []string
		wantChannel  string
		wantLogLevel string
	}{
		{
			name:    "valid channel and log level survive untouched",
			channel: "dev", logLevel: "warn",
			wantChannel: "dev", wantLogLevel: "warn",
		},
		{
			name:    "empty channel stays empty",
			channel: "", logLevel: "info",
			wantChannel: "", wantLogLevel: "info",
		},
		{
			name:    "unrecognised channel coerces to unset",
			channel: "nightly", logLevel: "info",
			wantCoerced: []string{"channel"},
			wantChannel: "", wantLogLevel: "info",
		},
		{
			name:    "unrecognised log level coerces to the default",
			channel: "stable", logLevel: "warning",
			wantCoerced: []string{"log_level"},
			wantChannel: "stable", wantLogLevel: DefaultState().LogLevel,
		},
		{
			// An explicitly empty log_level overrides the DefaultState
			// value it was unmarshalled onto, and would reach the backend
			// container as an empty SYNTHORG_LOG_LEVEL, so it is coerced
			// back to the default rather than passed through.
			name:    "explicitly empty log level coerces to the default",
			channel: "", logLevel: "",
			wantCoerced: []string{"log_level"},
			wantChannel: "", wantLogLevel: DefaultState().LogLevel,
		},
		{
			// An OMITTED log_level keeps the DefaultState value and is not
			// a coercion: there was nothing on disk to repair.
			name:        "omitted log level is not a coercion",
			omitted:     true,
			wantChannel: "", wantLogLevel: DefaultState().LogLevel,
		},
		{
			name:    "both unrecognised are reported together",
			channel: "nightly", logLevel: "warning",
			wantCoerced: []string{"channel", "log_level"},
			wantChannel: "", wantLogLevel: DefaultState().LogLevel,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmp := t.TempDir()
			body := map[string]any{
				"data_dir":            tmp,
				"image_tag":           "latest",
				"backend_port":        3001,
				"web_port":            3000,
				"log_level":           tt.logLevel,
				"channel":             tt.channel,
				"persistence_backend": "sqlite",
				"memory_backend":      "sqlvector",
				// encrypt_secrets defaults to true (DefaultState), and
				// the master-key invariant now rejects an empty key in
				// that combination; opt this fixture out since it is
				// targeting channel/log-level coercion only.
				"encrypt_secrets": false,
			}
			if tt.omitted {
				delete(body, "log_level")
			}
			raw, _ := json.Marshal(body)
			if err := os.WriteFile(filepath.Join(tmp, stateFileName), raw, 0o600); err != nil {
				t.Fatal(err)
			}
			got, err := Load(tmp)
			if err != nil {
				t.Fatalf("Load() must not fail on an unrecognised enum, got %v", err)
			}
			if got.Channel != tt.wantChannel {
				t.Errorf("Channel = %q, want %q", got.Channel, tt.wantChannel)
			}
			if got.LogLevel != tt.wantLogLevel {
				t.Errorf("LogLevel = %q, want %q", got.LogLevel, tt.wantLogLevel)
			}
			coercedFields := make([]string, 0, len(got.Coerced))
			for _, c := range got.Coerced {
				coercedFields = append(coercedFields, c.Field)
			}
			if !slices.Equal(coercedFields, tt.wantCoerced) {
				t.Errorf("coerced fields = %v, want %v", coercedFields, tt.wantCoerced)
			}
		})
	}
}

func TestStatePath(t *testing.T) {
	path := StatePath("/some/dir")
	if filepath.Base(path) != stateFileName {
		t.Errorf("StatePath base = %q, want %q", filepath.Base(path), stateFileName)
	}
}

func TestSaveLoadRoundTrip(t *testing.T) {
	tmp := t.TempDir()
	original := State{
		DataDir:            tmp,
		ImageTag:           "v2.0.0",
		BackendPort:        8080,
		WebPort:            3030,
		Sandbox:            true,
		DockerSock:         "/custom/docker.sock",
		LogLevel:           "warn",
		JWTSecret:          "super-secret-key",
		SettingsKey:        "super-settings-key",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
		AutoCleanup:        true,
	}

	if err := Save(original); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if loaded.DataDir != original.DataDir {
		t.Errorf("DataDir = %q, want %q", loaded.DataDir, original.DataDir)
	}
	if loaded.Sandbox != original.Sandbox {
		t.Errorf("Sandbox = %v, want %v", loaded.Sandbox, original.Sandbox)
	}
	if loaded.DockerSock != original.DockerSock {
		t.Errorf("DockerSock = %q, want %q", loaded.DockerSock, original.DockerSock)
	}
	if loaded.AutoCleanup != original.AutoCleanup {
		t.Errorf("AutoCleanup = %v, want %v", loaded.AutoCleanup, original.AutoCleanup)
	}
}

func TestIsValidBool(t *testing.T) {
	t.Parallel()

	tests := []struct {
		input string
		want  bool
	}{
		{"true", true},
		{"false", true},
		{"", false},
		{"1", false},
		{"0", false},
		{"yes", false},
		{"no", false},
		{"True", false},
		{"TRUE", false},
		{"False", false},
		{"FALSE", false},
		{"t", false},
		{"f", false},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			t.Parallel()
			if got := IsValidBool(tt.input); got != tt.want {
				t.Errorf("IsValidBool(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
}

func FuzzIsValidBool(f *testing.F) {
	f.Add("true")
	f.Add("false")
	f.Add("")
	f.Add("TRUE")
	f.Add("1")
	f.Add("yes")

	f.Fuzz(func(t *testing.T, s string) {
		got := IsValidBool(s)
		want := s == "true" || s == "false"
		if got != want {
			t.Fatalf("IsValidBool(%q) = %v, want %v", s, got, want)
		}
	})
}

func TestSaveLoadRoundTripNewFields(t *testing.T) {
	tmp := t.TempDir()
	original := State{
		DataDir:            tmp,
		ImageTag:           "v2.0.0",
		BackendPort:        8080,
		WebPort:            3030,
		LogLevel:           "warn",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
		Color:              "never",
		Output:             "json",
		Timestamps:         "iso8601",
		Hints:              "always",
		AutoUpdateCLI:      true,
		AutoPull:           true,
		AutoRestart:        true,
		AutoApplyCompose:   true,
		AutoStartAfterWipe: true,
	}

	if err := Save(original); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := Load(tmp)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if loaded.Color != original.Color {
		t.Errorf("Color = %q, want %q", loaded.Color, original.Color)
	}
	if loaded.Output != original.Output {
		t.Errorf("Output = %q, want %q", loaded.Output, original.Output)
	}
	if loaded.Timestamps != original.Timestamps {
		t.Errorf("Timestamps = %q, want %q", loaded.Timestamps, original.Timestamps)
	}
	if loaded.Hints != original.Hints {
		t.Errorf("Hints = %q, want %q", loaded.Hints, original.Hints)
	}
	if loaded.AutoUpdateCLI != original.AutoUpdateCLI {
		t.Errorf("AutoUpdateCLI = %v, want %v", loaded.AutoUpdateCLI, original.AutoUpdateCLI)
	}
	if loaded.AutoPull != original.AutoPull {
		t.Errorf("AutoPull = %v, want %v", loaded.AutoPull, original.AutoPull)
	}
	if loaded.AutoRestart != original.AutoRestart {
		t.Errorf("AutoRestart = %v, want %v", loaded.AutoRestart, original.AutoRestart)
	}
	if loaded.AutoApplyCompose != original.AutoApplyCompose {
		t.Errorf("AutoApplyCompose = %v, want %v", loaded.AutoApplyCompose, original.AutoApplyCompose)
	}
	if loaded.AutoStartAfterWipe != original.AutoStartAfterWipe {
		t.Errorf("AutoStartAfterWipe = %v, want %v", loaded.AutoStartAfterWipe, original.AutoStartAfterWipe)
	}
}

func TestDefaultStateNewFields(t *testing.T) {
	s := DefaultState()
	if s.Color != "" {
		t.Errorf("Color should default to empty, got %q", s.Color)
	}
	if s.Output != "" {
		t.Errorf("Output should default to empty, got %q", s.Output)
	}
	if s.Timestamps != "" {
		t.Errorf("Timestamps should default to empty, got %q", s.Timestamps)
	}
	if s.Hints != "" {
		t.Errorf("Hints should default to empty, got %q", s.Hints)
	}
	if s.AutoUpdateCLI {
		t.Error("AutoUpdateCLI should default to false")
	}
	if s.AutoPull {
		t.Error("AutoPull should default to false")
	}
	if s.AutoRestart {
		t.Error("AutoRestart should default to false")
	}
	if s.AutoApplyCompose {
		t.Error("AutoApplyCompose should default to false")
	}
	if s.AutoStartAfterWipe {
		t.Error("AutoStartAfterWipe should default to false")
	}
}

func FuzzIsValidColorMode(f *testing.F) {
	f.Add("always")
	f.Add("auto")
	f.Add("never")
	f.Add("")
	f.Add("Always")
	f.Add("NEVER")

	valid := map[string]bool{"always": true, "auto": true, "never": true}
	f.Fuzz(func(t *testing.T, s string) {
		got := IsValidColorMode(s)
		want := valid[s]
		if got != want {
			t.Fatalf("IsValidColorMode(%q) = %v, want %v", s, got, want)
		}
	})
}

func FuzzIsValidOutputMode(f *testing.F) {
	f.Add("text")
	f.Add("json")
	f.Add("")
	f.Add("TEXT")
	f.Add("yaml")

	valid := map[string]bool{"text": true, "json": true}
	f.Fuzz(func(t *testing.T, s string) {
		got := IsValidOutputMode(s)
		want := valid[s]
		if got != want {
			t.Fatalf("IsValidOutputMode(%q) = %v, want %v", s, got, want)
		}
	})
}

// TestFineTuneVariantFromIndex covers the TUI-index -> persisted-string
// mapping. The TUI only ever sets index 0 or 1 (toggled via `1 - variant`),
// but the helper has a defensive fallback so an unexpected index produces a
// valid default rather than an invalid variant string.
func TestFineTuneVariantFromIndex(t *testing.T) {
	t.Parallel()

	cases := []struct {
		idx  int
		want string
	}{
		{0, FineTuneVariantGPU},
		{1, FineTuneVariantCPU},
		{-1, FineTuneVariantGPU},
		{2, FineTuneVariantGPU},
		{42, FineTuneVariantGPU},
	}
	for _, tc := range cases {
		if got := FineTuneVariantFromIndex(tc.idx); got != tc.want {
			t.Errorf("FineTuneVariantFromIndex(%d) = %q, want %q", tc.idx, got, tc.want)
		}
	}
}

// TestFineTuneVariantOrDefault covers the persisted-string -> resolved-variant
// mapping. Empty / unknown values resolve to "gpu" so a config that omits or
// misspells the field still loads.
func TestFineTuneVariantOrDefault(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		variant string
		want    string
	}{
		{"empty", "", FineTuneVariantGPU},
		{"gpu", FineTuneVariantGPU, FineTuneVariantGPU},
		{"cpu", FineTuneVariantCPU, FineTuneVariantCPU},
		{"unknown-falls-back-to-gpu", "tpu", FineTuneVariantGPU},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			s := State{FineTuningVariant: tc.variant}
			if got := s.FineTuneVariantOrDefault(); got != tc.want {
				t.Errorf("FineTuneVariantOrDefault() = %q, want %q", got, tc.want)
			}
		})
	}
}

// TestValidate_FineTuningVariant covers State.Validate's variant validation:
// invalid variants are rejected unconditionally (typos in a persisted config
// must not survive silently until someone flips fine_tuning on), while the
// empty string passes (a config may omit the field, resolving to "gpu") and
// the two canonical values ("gpu", "cpu") are always accepted.
//
// Split into arch-independent and amd64-only groups because cross-field
// rules like `fine_tuning=true requires amd64` would trip every enabled
// case on ARM CI runners.
func TestValidate_FineTuningVariant(t *testing.T) {
	t.Parallel()

	base := DefaultState()
	base.JWTSecret = ""   // avoid JWT validation path
	base.SettingsKey = "" // avoid settings-key validation path
	base.MasterKey = ""   // avoid master-key validation path
	base.EncryptSecrets = false
	base.Sandbox = true

	type variantCase struct {
		name       string
		fineTuning bool
		variant    string
		wantErr    bool
	}
	run := func(t *testing.T, cases []variantCase) {
		t.Helper()
		for _, tc := range cases {
			t.Run(tc.name, func(t *testing.T) {
				t.Parallel()
				s := base
				s.FineTuning = tc.fineTuning
				s.FineTuningVariant = tc.variant
				err := s.Validate()
				if tc.wantErr && err == nil {
					t.Errorf("Validate() returned nil, want error for variant=%q", tc.variant)
				}
				if !tc.wantErr && err != nil {
					t.Errorf("Validate() = %v, want nil for variant=%q", err, tc.variant)
				}
			})
		}
	}

	// Arch-independent: variant enum validation runs regardless of
	// FineTuning or GOARCH, so exercise these on every runner.
	run(t, []variantCase{
		{"disabled+empty", false, "", false},
		{"disabled+gpu-accepted", false, FineTuneVariantGPU, false},
		{"disabled+cpu-accepted", false, FineTuneVariantCPU, false},
		{"disabled+invalid-rejected", false, "invalid", true},
		{"disabled+typo-rejected", false, "GPU", true},
	})

	if runtime.GOARCH != "amd64" {
		t.Skip("fine_tuning=true cases require amd64 architecture")
	}
	run(t, []variantCase{
		{"enabled+empty-accepted", true, "", false},
		{"enabled+gpu", true, FineTuneVariantGPU, false},
		{"enabled+cpu", true, FineTuneVariantCPU, false},
		{"enabled+invalid-rejected", true, "tpu", true},
		{"enabled+typo-rejected", true, "GPU", true},
	})
}
