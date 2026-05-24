package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
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
	if s.MemoryBackend != "mem0" {
		t.Errorf("MemoryBackend = %q, want mem0", s.MemoryBackend)
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
		MemoryBackend:      "mem0",
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
		MemoryBackend:      "mem0",
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

func TestLoadRejectsInvalidBackends(t *testing.T) {
	tests := []struct {
		name    string
		persist string
		memory  string
	}{
		{"empty persistence", "", "mem0"},
		{"empty memory", "sqlite", ""},
		{"unknown persistence", "mysql", "mem0"},
		{"unknown memory", "sqlite", "redis"},
		{"both empty", "", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmp := t.TempDir()
			raw, _ := json.Marshal(map[string]any{
				"data_dir":            tmp,
				"image_tag":           "latest",
				"backend_port":        3001,
				"web_port":            3000,
				"log_level":           "info",
				"persistence_backend": tt.persist,
				"memory_backend":      tt.memory,
			})
			if err := os.WriteFile(filepath.Join(tmp, stateFileName), raw, 0o600); err != nil {
				t.Fatal(err)
			}
			_, err := Load(tmp)
			if err == nil {
				t.Errorf("expected validation error for persist=%q memory=%q", tt.persist, tt.memory)
			}
		})
	}
}

func TestIsValidChannel(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"stable", true},
		{"dev", true},
		{"", false},
		{"nightly", false},
		{"STABLE", false}, // case-sensitive
		{"Dev", false},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := IsValidChannel(tt.input); got != tt.want {
				t.Errorf("IsValidChannel(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
}

func TestIsValidLogLevel(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"debug", true},
		{"info", true},
		{"warn", true},
		{"error", true},
		{"warning", false}, // "warn" not "warning"
		{"", false},
		{"trace", false},
		{"WARN", false}, // case-sensitive
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := IsValidLogLevel(tt.input); got != tt.want {
				t.Errorf("IsValidLogLevel(%q) = %v, want %v", tt.input, got, tt.want)
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

func TestLoadRejectsInvalidChannelAndLogLevel(t *testing.T) {
	tests := []struct {
		name     string
		channel  string
		logLevel string
		wantErr  bool
	}{
		{"valid channel and log level", "dev", "warn", false},
		{"empty channel is ok", "", "info", false},
		{"invalid channel", "nightly", "info", true},
		{"invalid log level", "stable", "warning", true},
		{"empty log level uses default from DefaultState", "", "", false}, // unmarshals onto defaults
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			tmp := t.TempDir()
			raw, _ := json.Marshal(map[string]any{
				"data_dir":            tmp,
				"image_tag":           "latest",
				"backend_port":        3001,
				"web_port":            3000,
				"log_level":           tt.logLevel,
				"channel":             tt.channel,
				"persistence_backend": "sqlite",
				"memory_backend":      "mem0",
				// encrypt_secrets defaults to true (DefaultState), and
				// the master-key invariant now rejects an empty key in
				// that combination; opt this fixture out since it is
				// targeting channel/log-level validation only.
				"encrypt_secrets": false,
			})
			if err := os.WriteFile(filepath.Join(tmp, stateFileName), raw, 0o600); err != nil {
				t.Fatal(err)
			}
			_, err := Load(tmp)
			if (err != nil) != tt.wantErr {
				t.Errorf("Load() error = %v, wantErr %v", err, tt.wantErr)
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
		MemoryBackend:      "mem0",
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

func TestIsValidColorMode(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"always", true},
		{"auto", true},
		{"never", true},
		{"", false},
		{"Always", false},
		{"AUTO", false},
		{"NEVER", false},
		{"none", false},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := IsValidColorMode(tt.input); got != tt.want {
				t.Errorf("IsValidColorMode(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
}

func TestIsValidOutputMode(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"text", true},
		{"json", true},
		{"", false},
		{"JSON", false},
		{"TEXT", false},
		{"yaml", false},
		{"xml", false},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := IsValidOutputMode(tt.input); got != tt.want {
				t.Errorf("IsValidOutputMode(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
}

func TestIsValidTimestampMode(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"relative", true},
		{"iso8601", true},
		{"", false},
		{"ISO8601", false},
		{"Relative", false},
		{"unix", false},
		{"rfc3339", false},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := IsValidTimestampMode(tt.input); got != tt.want {
				t.Errorf("IsValidTimestampMode(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
}

func TestIsValidHintsMode(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"always", true},
		{"auto", true},
		{"never", true},
		{"", false},
		{"Always", false},
		{"NEVER", false},
		{"none", false},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := IsValidHintsMode(tt.input); got != tt.want {
				t.Errorf("IsValidHintsMode(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
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
		MemoryBackend:      "mem0",
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
// mapping. Empty / unknown values resolve to "gpu" for forward compat with
// pre-split configs.
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
// empty string passes as a forward-compat shim for pre-split configs and the
// two canonical values ("gpu", "cpu") are always accepted.
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

	// Arch-independent: variant enum validation runs regardless of
	// FineTuning or GOARCH, so exercise these on every runner.
	archAgnostic := []struct {
		name       string
		fineTuning bool
		variant    string
		wantErr    bool
	}{
		{"disabled+empty", false, "", false},
		{"disabled+gpu-accepted", false, FineTuneVariantGPU, false},
		{"disabled+cpu-accepted", false, FineTuneVariantCPU, false},
		{"disabled+invalid-rejected", false, "invalid", true},
		{"disabled+typo-rejected", false, "GPU", true},
	}
	for _, tc := range archAgnostic {
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

	if runtime.GOARCH != "amd64" {
		t.Skip("fine_tuning=true cases require amd64 architecture")
	}
	amd64Only := []struct {
		name       string
		fineTuning bool
		variant    string
		wantErr    bool
	}{
		{"enabled+empty-accepted", true, "", false},
		{"enabled+gpu", true, FineTuneVariantGPU, false},
		{"enabled+cpu", true, FineTuneVariantCPU, false},
		{"enabled+invalid-rejected", true, "tpu", true},
		{"enabled+typo-rejected", true, "GPU", true},
	}
	for _, tc := range amd64Only {
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
