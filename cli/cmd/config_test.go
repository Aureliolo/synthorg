package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestMaskSecret(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"", "(not set)"},
		{"s3cret", "****"},
		{"x", "****"},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			t.Parallel()
			if got := maskSecret(tt.input); got != tt.want {
				t.Errorf("maskSecret(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestConfigShowNotInitialized(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	var buf bytes.Buffer

	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "show", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	out := buf.String()
	if !bytes.Contains([]byte(out), []byte("Not initialized")) {
		t.Errorf("expected 'Not initialized' in output, got: %s", out)
	}
}

func TestConfigShowDisplaysFields(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.State{
		DataDir:            dir,
		ImageTag:           "v1.2.3",
		BackendPort:        9000,
		WebPort:            4000,
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		LogLevel:           "debug",
		JWTSecret:          "super-secret",
		SettingsKey:        "super-settings-key",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
	}

	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "show", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	out := buf.String()
	for _, want := range []string{
		"v1.2.3",
		"9000",
		"4000",
		"true",
		"debug",
		"/var/run/docker.sock",
		"****",
		"sqlite",
		"mem0",
	} {
		if !bytes.Contains([]byte(out), []byte(want)) {
			t.Errorf("expected %q in output, got: %s", want, out)
		}
	}

	// Secrets must not appear in output.
	if bytes.Contains([]byte(out), []byte("super-secret")) {
		t.Error("JWT secret leaked in output")
	}
	if bytes.Contains([]byte(out), []byte("super-settings-key")) {
		t.Error("Settings key leaked in output")
	}

	// Both secret labels must be present with masked values.
	if !bytes.Contains([]byte(out), []byte("Settings key")) {
		t.Error("expected 'Settings key' label in output")
	}
	if !bytes.Contains([]byte(out), []byte("JWT secret")) {
		t.Error("expected 'JWT secret' label in output")
	}
	// Count "****" occurrences -- must appear at least twice (JWT + Settings key).
	maskCount := bytes.Count([]byte(out), []byte("****"))
	if maskCount < 2 {
		t.Errorf("expected at least 2 masked secrets (****), got %d", maskCount)
	}
}

func TestConfigSetChannel(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	// Create initial config.
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "channel", "dev", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify the channel was persisted.
	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	if loaded.Channel != "dev" {
		t.Errorf("Channel = %q, want dev", loaded.Channel)
	}
}

func TestConfigSetImageTag_ClearsVerifiedDigestsAndImageTag(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.VerifiedDigests = map[string]string{
		"backend":                  "sha256:1111111111111111111111111111111111111111111111111111111111111111",
		"web":                      "sha256:2222222222222222222222222222222222222222222222222222222222222222",
		"dhi:dhi.io/postgres:17.2": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
	}
	state.VerifiedImageTag = state.ImageTag
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "image_tag", "0.0.0-fake", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	if loaded.ImageTag != "0.0.0-fake" {
		t.Errorf("ImageTag = %q, want 0.0.0-fake", loaded.ImageTag)
	}
	if len(loaded.VerifiedDigests) != 0 {
		t.Errorf("VerifiedDigests must be cleared after image_tag change, got %v", loaded.VerifiedDigests)
	}
	if loaded.VerifiedImageTag != "" {
		t.Errorf("VerifiedImageTag must be cleared after image_tag change, got %q", loaded.VerifiedImageTag)
	}
}

func TestConfigSetRejectsInvalidChannel(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "channel", "nightly", "--data-dir", dir})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected error for invalid channel")
	}
}

func TestConfigSetAutoCleanup(t *testing.T) {
	tests := []struct {
		name     string
		initial  bool
		setValue string
		want     bool
	}{
		{"set to true", false, "true", true},
		{"set to false", true, "false", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sandboxRootCmd(t)
			dir := t.TempDir()
			state := config.DefaultState()
			state.EncryptSecrets = false
			state.DataDir = dir
			state.AutoCleanup = tt.initial
			if err := config.Save(state); err != nil {
				t.Fatal(err)
			}

			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "set", "auto_cleanup", tt.setValue, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.AutoCleanup != tt.want {
				t.Errorf("AutoCleanup = %v, want %v", loaded.AutoCleanup, tt.want)
			}
		})
	}
}

func FuzzConfigSetAutoCleanup(f *testing.F) {
	f.Add("true")
	f.Add("false")
	f.Add("TRUE")
	f.Add("1")
	f.Add("yes")
	f.Add("")

	f.Fuzz(func(t *testing.T, value string) {
		sandboxRootCmd(t)
		dir := t.TempDir()
		state := config.DefaultState()
		state.EncryptSecrets = false
		state.DataDir = dir
		if err := config.Save(state); err != nil {
			t.Fatalf("Save: %v", err)
		}

		var buf bytes.Buffer
		rootCmd.SetOut(&buf)
		rootCmd.SetErr(&buf)
		rootCmd.SetArgs([]string{"config", "set", "auto_cleanup", value, "--data-dir", dir})
		err := rootCmd.Execute()

		allowed := value == "true" || value == "false"
		if allowed && err != nil {
			t.Fatalf("unexpected error for %q: %v", value, err)
		}
		if !allowed && err == nil {
			t.Fatalf("expected error for %q", value)
		}
	})
}

func TestConfigSetRejectsInvalidAutoCleanup(t *testing.T) {
	for _, value := range []string{"yes", "1", "YES", "True"} {
		t.Run(value, func(t *testing.T) {
			sandboxRootCmd(t)
			dir := t.TempDir()
			state := config.DefaultState()
			state.EncryptSecrets = false
			state.DataDir = dir
			if err := config.Save(state); err != nil {
				t.Fatal(err)
			}

			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "set", "auto_cleanup", value, "--data-dir", dir})
			if err := rootCmd.Execute(); err == nil {
				t.Errorf("expected error for auto_cleanup=%q", value)
			}
		})
	}
}

func TestConfigShowAutoCleanup(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "show", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	out := buf.String()
	found := false
	for line := range strings.SplitSeq(out, "\n") {
		if strings.Contains(line, "Auto cleanup") {
			found = true
			if !strings.Contains(line, "false") {
				t.Errorf("Auto cleanup line should contain 'false', got: %s", line)
			}
			break
		}
	}
	if !found {
		t.Error("expected 'Auto cleanup' label in output")
	}
}

func TestConfigSetLogLevel(t *testing.T) {
	tests := []struct {
		name  string
		value string
		want  string
	}{
		{"set to debug", "debug", "debug"},
		{"set to info", "info", "info"},
		{"set to warn", "warn", "warn"},
		{"set to error", "error", "error"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sandboxRootCmd(t)
			dir := t.TempDir()
			state := config.DefaultState()
			state.EncryptSecrets = false
			state.DataDir = dir
			if err := config.Save(state); err != nil {
				t.Fatal(err)
			}

			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "set", "log_level", tt.value, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.LogLevel != tt.want {
				t.Errorf("LogLevel = %q, want %q", loaded.LogLevel, tt.want)
			}
		})
	}
}

func TestConfigSetRejectsInvalidLogLevel(t *testing.T) {
	for _, value := range []string{"verbose", "trace", "INFO", "Debug", ""} {
		t.Run(value, func(t *testing.T) {
			sandboxRootCmd(t)
			dir := t.TempDir()
			state := config.DefaultState()
			state.EncryptSecrets = false
			state.DataDir = dir
			if err := config.Save(state); err != nil {
				t.Fatal(err)
			}
			orig := state.LogLevel

			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "set", "log_level", value, "--data-dir", dir})
			if err := rootCmd.Execute(); err == nil {
				t.Errorf("expected error for log_level=%q", value)
			}
			loaded, loadErr := config.Load(dir)
			if loadErr != nil {
				t.Fatalf("Load after rejected %q: %v", value, loadErr)
			}
			if loaded.LogLevel != orig {
				t.Errorf("rejected %q mutated LogLevel: got %q, want %q", value, loaded.LogLevel, orig)
			}
		})
	}
}

func FuzzConfigSetLogLevel(f *testing.F) {
	f.Add("debug")
	f.Add("info")
	f.Add("warn")
	f.Add("error")
	f.Add("verbose")
	f.Add("trace")
	f.Add("")
	f.Add("INFO")

	f.Fuzz(func(t *testing.T, value string) {
		sandboxRootCmd(t)
		dir := t.TempDir()
		state := config.DefaultState()
		state.EncryptSecrets = false
		state.DataDir = dir
		if err := config.Save(state); err != nil {
			t.Fatalf("Save: %v", err)
		}

		var buf bytes.Buffer
		rootCmd.SetOut(&buf)
		rootCmd.SetErr(&buf)
		rootCmd.SetArgs([]string{"config", "set", "log_level", value, "--data-dir", dir})
		err := rootCmd.Execute()

		allowed := value == "debug" || value == "info" || value == "warn" || value == "error"
		if allowed && err != nil {
			t.Fatalf("unexpected error for %q: %v", value, err)
		}
		if !allowed && err == nil {
			t.Fatalf("expected error for %q", value)
		}
	})
}

func TestConfigGet(t *testing.T) {
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.Channel = "dev"
	state.ImageTag = "0.5.0-dev.9"
	state.LogLevel = "debug"
	state.AutoCleanup = true
	state.Sandbox = true
	state.BackendPort = 9000
	state.WebPort = 4000
	state.PersistenceBackend = "sqlite"
	state.MemoryBackend = "mem0"
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		key  string
		want string
	}{
		{"channel", "dev"},
		{"image_tag", "0.5.0-dev.9"},
		{"log_level", "debug"},
		{"auto_cleanup", "true"},
		{"sandbox", "true"},
		{"backend_port", "9000"},
		{"web_port", "4000"},
		{"persistence_backend", "sqlite"},
		{"memory_backend", "mem0"},
	}

	for _, tt := range tests {
		t.Run(tt.key, func(t *testing.T) {
			sandboxRootCmd(t)
			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "get", tt.key, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			got := strings.TrimSpace(buf.String())
			if got != tt.want {
				t.Errorf("config get %s = %q, want %q", tt.key, got, tt.want)
			}
		})
	}
}

func TestConfigGetUnknownKey(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "get", "unknown_key", "--data-dir", dir})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected error for unknown key")
	}
}

func TestConfigGetRejectsSecretKeys(t *testing.T) {
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	for _, key := range []string{"jwt_secret", "settings_key"} {
		t.Run(key, func(t *testing.T) {
			sandboxRootCmd(t)
			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "get", key, "--data-dir", dir})
			err := rootCmd.Execute()
			if err == nil {
				t.Fatalf("expected error for secret key %q", key)
			}
		})
	}
}

func TestConfigGetDefaultChannel(t *testing.T) {
	sandboxRootCmd(t)
	// Seed a config file that omits "channel" so Load's unmarshal-onto-
	// DefaultState fallback supplies the default "stable" value.
	dir := t.TempDir()
	raw, err := json.Marshal(map[string]any{
		"data_dir":            dir,
		"backend_port":        3001,
		"web_port":            3000,
		"log_level":           "info",
		"persistence_backend": "sqlite",
		"memory_backend":      "mem0",
		// encrypt_secrets defaults to true (DefaultState), which now
		// requires master_key. This test targets channel-default
		// resolution, so opt out of the encrypt-secrets invariant.
		"encrypt_secrets": false,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "get", "channel", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := strings.TrimSpace(buf.String())
	if got != "stable" {
		t.Errorf("config get channel = %q, want stable", got)
	}
}

func TestConfigSetRejectsUnknownKey(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "unknown_key", "value", "--data-dir", dir})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected error for unknown key")
	}
}

// TestConfigSetPreInitAutoGeneratesMasterKey covers #5: setting an
// unrelated key before `init`, under the encrypt_secrets=true default,
// must succeed and auto-provision a Fernet master key instead of failing
// the master-key-required invariant.
func TestConfigSetPreInitAutoGeneratesMasterKey(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir() // no config saved -> pre-init state

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "auto_pull", "true", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("pre-init config set should succeed, got: %v", err)
	}

	// Load runs strict Validate; success here proves the persisted master
	// key is valid and the unrelated key landed.
	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after pre-init set: %v", err)
	}
	if !loaded.AutoPull {
		t.Error("AutoPull should be true after set")
	}
	if !loaded.EncryptSecrets {
		t.Error("EncryptSecrets should remain true (default)")
	}
	if len(loaded.MasterKey) != 44 {
		t.Errorf("MasterKey length = %d, want 44 (auto-generated Fernet key)", len(loaded.MasterKey))
	}
}

// TestConfigSetBatchAppliesAll covers #7: multiple key/value pairs in one
// invocation are all applied atomically.
func TestConfigSetBatchAppliesAll(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{
		"config", "set",
		"auto_pull", "true",
		"channel", "dev",
		"log_level", "debug",
		"--data-dir", dir,
	})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("batch config set: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after batch set: %v", err)
	}
	if !loaded.AutoPull || loaded.Channel != "dev" || loaded.LogLevel != "debug" {
		t.Errorf("batch set incomplete: AutoPull=%v Channel=%q LogLevel=%q",
			loaded.AutoPull, loaded.Channel, loaded.LogLevel)
	}
}

// TestConfigSetBatchInvalidPairPersistsNothing asserts the batch is atomic:
// an invalid pair anywhere aborts before anything is written.
func TestConfigSetBatchInvalidPairPersistsNothing(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.Channel = "stable"
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	// Second pair is invalid; the whole set must abort.
	rootCmd.SetArgs([]string{"config", "set", "auto_pull", "true", "channel", "bogus", "--data-dir", dir})
	if err := rootCmd.Execute(); err == nil {
		t.Fatal("expected error for an invalid pair")
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after failed batch: %v", err)
	}
	if loaded.AutoPull {
		t.Error("AutoPull must not be persisted when a later pair fails")
	}
	if loaded.Channel != "stable" {
		t.Errorf("Channel = %q, want stable (unchanged)", loaded.Channel)
	}
}

func TestConfigSetOddArgsUsageError(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "auto_pull", "--data-dir", dir})
	if err := rootCmd.Execute(); err == nil {
		t.Fatal("expected a usage error for an odd argument count")
	}
}

// TestConfigImportAppliesFile covers #7: a key=value file is applied
// atomically, equivalent to a batch set.
func TestConfigImportAppliesFile(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	file := filepath.Join(dir, "seed.conf")
	content := "# pre-seed before init\nauto_pull = true\nchannel=dev\n\nlog_level = warn\n"
	if err := os.WriteFile(file, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "import", file, "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("config import: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after import: %v", err)
	}
	if !loaded.AutoPull || loaded.Channel != "dev" || loaded.LogLevel != "warn" {
		t.Errorf("import incomplete: AutoPull=%v Channel=%q LogLevel=%q",
			loaded.AutoPull, loaded.Channel, loaded.LogLevel)
	}
	if len(loaded.MasterKey) != 44 {
		t.Errorf("import should also auto-provision a master key, got len %d", len(loaded.MasterKey))
	}
}

func TestConfigImportBadLineAborts(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	file := filepath.Join(dir, "bad.conf")
	if err := os.WriteFile(file, []byte("auto_pull = true\nthis-line-has-no-equals\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "import", file, "--data-dir", dir})
	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected an error for a malformed line")
	}
	if !strings.Contains(err.Error(), ":2:") {
		t.Errorf("error should name the offending line (2), got: %v", err)
	}
}

func TestParseConfigImportFile(t *testing.T) {
	dir := t.TempDir()

	t.Run("parses pairs, skips comments and blanks", func(t *testing.T) {
		file := filepath.Join(dir, "ok.conf")
		body := "# comment\n\nauto_pull = true\n  channel=dev  \nlog_level=info\n"
		if err := os.WriteFile(file, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		pairs, err := parseConfigImportFile(file)
		if err != nil {
			t.Fatalf("parseConfigImportFile: %v", err)
		}
		want := []configPair{
			{key: "auto_pull", value: "true"},
			{key: "channel", value: "dev"},
			{key: "log_level", value: "info"},
		}
		if len(pairs) != len(want) {
			t.Fatalf("got %d pairs, want %d: %+v", len(pairs), len(want), pairs)
		}
		for i, p := range pairs {
			if p != want[i] {
				t.Errorf("pair %d = %+v, want %+v", i, p, want[i])
			}
		}
	})

	t.Run("rejects line without equals", func(t *testing.T) {
		file := filepath.Join(dir, "noeq.conf")
		if err := os.WriteFile(file, []byte("nokeyvalue\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := parseConfigImportFile(file); err == nil {
			t.Error("expected error for a line without '='")
		}
	})

	t.Run("rejects empty key", func(t *testing.T) {
		file := filepath.Join(dir, "emptykey.conf")
		if err := os.WriteFile(file, []byte("=value\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := parseConfigImportFile(file); err == nil {
			t.Error("expected error for an empty key")
		}
	})
}
