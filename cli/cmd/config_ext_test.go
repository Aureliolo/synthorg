package cmd

import (
	"bytes"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestConfigSetBackendPort(t *testing.T) {
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	sandboxRootCmd(t)
	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "backend_port", "9000", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	if loaded.BackendPort != 9000 {
		t.Errorf("BackendPort = %d, want 9000", loaded.BackendPort)
	}
}

func TestConfigSetBackendPortRejectsInvalid(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	for _, value := range []string{"0", "-1", "65536", "abc", ""} {
		var buf bytes.Buffer
		rootCmd.SetOut(&buf)
		rootCmd.SetErr(&buf)
		rootCmd.SetArgs([]string{"config", "set", "backend_port", value, "--data-dir", dir})
		if err := rootCmd.Execute(); err == nil {
			t.Errorf("expected error for backend_port=%q", value)
		}
	}
}

func TestConfigSetPortUniqueness(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	// Try setting backend_port to same as web_port.
	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "backend_port", "3000", "--data-dir", dir})
	if err := rootCmd.Execute(); err == nil {
		t.Fatal("expected error when backend_port == web_port")
	}

	// Try setting web_port to same as backend_port.
	buf.Reset()
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "web_port", "3001", "--data-dir", dir})
	if err := rootCmd.Execute(); err == nil {
		t.Fatal("expected error when web_port == backend_port")
	}
}

func TestConfigSetWebPort(t *testing.T) {
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
	rootCmd.SetArgs([]string{"config", "set", "web_port", "4000", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	if loaded.WebPort != 4000 {
		t.Errorf("WebPort = %d, want 4000", loaded.WebPort)
	}
}

func TestConfigSetSandbox(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.Sandbox = false
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", "sandbox", "true", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	if !loaded.Sandbox {
		t.Error("Sandbox should be true after set")
	}
}

func TestConfigSetImageTag(t *testing.T) {
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
	rootCmd.SetArgs([]string{"config", "set", "image_tag", "v1.2.3", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	if loaded.ImageTag != "v1.2.3" {
		t.Errorf("ImageTag = %q, want v1.2.3", loaded.ImageTag)
	}
}

func TestConfigSetColor(t *testing.T) {
	sandboxRootCmd(t)
	for _, value := range []string{"always", "auto", "never"} {
		t.Run(value, func(t *testing.T) {
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
			rootCmd.SetArgs([]string{"config", "set", "color", value, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.Color != value {
				t.Errorf("Color = %q, want %q", loaded.Color, value)
			}
		})
	}
}

func TestConfigSetColorRejectsInvalid(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	for _, value := range []string{"Always", "NEVER", "none", ""} {
		var buf bytes.Buffer
		rootCmd.SetOut(&buf)
		rootCmd.SetErr(&buf)
		rootCmd.SetArgs([]string{"config", "set", "color", value, "--data-dir", dir})
		if err := rootCmd.Execute(); err == nil {
			t.Errorf("expected error for color=%q", value)
		}
	}
}

func TestConfigSetOutput(t *testing.T) {
	sandboxRootCmd(t)
	for _, value := range []string{"text", "json"} {
		t.Run(value, func(t *testing.T) {
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
			rootCmd.SetArgs([]string{"config", "set", "output", value, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.Output != value {
				t.Errorf("Output = %q, want %q", loaded.Output, value)
			}
		})
	}
}

func TestConfigSetTimestamps(t *testing.T) {
	sandboxRootCmd(t)
	for _, value := range []string{"relative", "iso8601"} {
		t.Run(value, func(t *testing.T) {
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
			rootCmd.SetArgs([]string{"config", "set", "timestamps", value, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.Timestamps != value {
				t.Errorf("Timestamps = %q, want %q", loaded.Timestamps, value)
			}
		})
	}
}

func TestConfigSetHints(t *testing.T) {
	sandboxRootCmd(t)
	for _, value := range []string{"always", "auto", "never"} {
		t.Run(value, func(t *testing.T) {
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
			rootCmd.SetArgs([]string{"config", "set", "hints", value, "--data-dir", dir})
			if err := rootCmd.Execute(); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.Hints != value {
				t.Errorf("Hints = %q, want %q", loaded.Hints, value)
			}
		})
	}
}

// execConfigSet runs a config set command and returns the loaded state.
func execConfigSet(t *testing.T, dir, key, value string) config.State {
	t.Helper()
	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "set", key, value, "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("config set %s %s: %v", key, value, err)
	}
	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after set: %v", err)
	}
	return loaded
}

// seedConfig creates a temp dir with a default config saved and returns the dir.
func seedConfig(t *testing.T) (string, config.State) {
	t.Helper()
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}
	return dir, state
}

func TestConfigSetAutoBehaviorKeys(t *testing.T) {
	sandboxRootCmd(t)
	tests := []struct {
		key   string
		field func(config.State) bool
	}{
		{"auto_update_cli", func(s config.State) bool { return s.AutoUpdateCLI }},
		{"auto_pull", func(s config.State) bool { return s.AutoPull }},
		{"auto_restart", func(s config.State) bool { return s.AutoRestart }},
		{"auto_apply_compose", func(s config.State) bool { return s.AutoApplyCompose }},
		{"auto_start_after_wipe", func(s config.State) bool { return s.AutoStartAfterWipe }},
	}
	for _, tt := range tests {
		t.Run(tt.key, func(t *testing.T) {
			dir, _ := seedConfig(t)
			loaded := execConfigSet(t, dir, tt.key, "true")
			if !tt.field(loaded) {
				t.Errorf("%s should be true", tt.key)
			}
			loaded = execConfigSet(t, dir, tt.key, "false")
			if tt.field(loaded) {
				t.Errorf("%s should be false", tt.key)
			}
		})
	}
}

func TestConfigUnsetChannel(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.Channel = "dev"
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "unset", "channel", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after unset: %v", err)
	}
	if loaded.Channel != "stable" {
		t.Errorf("Channel = %q, want stable (default)", loaded.Channel)
	}
}

func TestConfigUnsetBackendPort(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.BackendPort = 9000
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "unset", "backend_port", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after unset: %v", err)
	}
	if loaded.BackendPort != 3001 {
		t.Errorf("BackendPort = %d, want 3001 (default)", loaded.BackendPort)
	}
}

func TestConfigUnsetRejectsUnknownKey(t *testing.T) {
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
	rootCmd.SetArgs([]string{"config", "unset", "unknown_key", "--data-dir", dir})
	if err := rootCmd.Execute(); err == nil {
		t.Fatal("expected error for unknown key")
	}
}

func TestConfigListShowsAllKeys(t *testing.T) {
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
	rootCmd.SetArgs([]string{"config", "list", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	out := buf.String()
	for _, key := range []string{"backend_port", "web_port", "channel", "log_level", "color", "hints", "memory_backend", "persistence_backend"} {
		if !strings.Contains(out, key) {
			t.Errorf("expected %q in config list output", key)
		}
	}
}

func TestConfigListSourceDefault(t *testing.T) {
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
	rootCmd.SetArgs([]string{"config", "list", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	out := buf.String()
	if !strings.Contains(out, "default") {
		t.Error("expected 'default' source in config list output for default values")
	}
}

func TestConfigPathPrintsPath(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "path", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	got := strings.TrimSpace(buf.String())
	// resolveDataDir calls filepath.EvalSymlinks, resolving macOS symlinks
	// like /var -> /private/var. Match this in the expected path.
	resolved := dir
	if r, err := filepath.EvalSymlinks(dir); err == nil {
		resolved = r
	}
	want := config.StatePath(resolved)
	if got != want {
		t.Errorf("config path = %q, want %q", got, want)
	}
}

func TestConfigGetNewKeys(t *testing.T) {
	sandboxRootCmd(t)
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.Color = "never"
	state.Output = "json"
	state.Timestamps = "iso8601"
	state.Hints = "always"
	state.AutoUpdateCLI = true
	state.AutoPull = true
	state.AutoRestart = true
	state.AutoApplyCompose = true
	state.AutoStartAfterWipe = true
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		key  string
		want string
	}{
		{"color", "never"},
		{"output", "json"},
		{"timestamps", "iso8601"},
		{"hints", "always"},
		{"auto_update_cli", "true"},
		{"auto_pull", "true"},
		{"auto_restart", "true"},
		{"auto_apply_compose", "true"},
		{"auto_start_after_wipe", "true"},
		{"docker_sock", ""},
	}

	for _, tt := range tests {
		t.Run(tt.key, func(t *testing.T) {
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

func FuzzConfigSetBackendPort(f *testing.F) {
	f.Add("3001")
	f.Add("9000")
	f.Add("0")
	f.Add("65536")
	f.Add("abc")
	f.Add("")
	f.Add("-1")

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
		rootCmd.SetArgs([]string{"config", "set", "backend_port", value, "--data-dir", dir})
		err := rootCmd.Execute()

		port, parseErr := strconv.Atoi(value)
		valid := parseErr == nil && port >= 1 && port <= 65535 && port != 3000 // 3000 is default web_port
		if valid && err != nil {
			t.Fatalf("unexpected error for %q: %v", value, err)
		}
		if !valid && err == nil {
			t.Fatalf("expected error for %q", value)
		}
	})
}

func FuzzConfigSetColor(f *testing.F) {
	f.Add("always")
	f.Add("auto")
	f.Add("never")
	f.Add("")
	f.Add("Always")
	f.Add("NEVER")

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
		rootCmd.SetArgs([]string{"config", "set", "color", value, "--data-dir", dir})
		err := rootCmd.Execute()

		valid := value == "always" || value == "auto" || value == "never"
		if valid && err != nil {
			t.Fatalf("unexpected error for %q: %v", value, err)
		}
		if !valid && err == nil {
			t.Fatalf("expected error for %q", value)
		}
	})
}
