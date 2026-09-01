package cmd

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/spf13/cobra"
)

func TestTargetImageTag(t *testing.T) {
	tests := []struct {
		name    string
		version string
		want    string
	}{
		{name: "with v prefix", version: "v0.2.7", want: "0.2.7"},
		{name: "without prefix", version: "0.2.6", want: "0.2.6"},
		// A source build pins the prerelease tag, matching what `init`
		// wrote. Resolving to `latest` here would pull the last stable
		// release over it and persist that, undoing the pin.
		{name: "dev build", version: "dev", want: "dev"},
		{name: "empty string", version: "", want: "dev"},
		{name: "invalid chars fall back to latest", version: "v1.0.0\n", want: "latest"},
		{name: "shell injection falls back to latest", version: "v1.0.0;rm -rf", want: "latest"},
		{name: "valid semver with pre-release", version: "v1.0.0-rc.1", want: "1.0.0-rc.1"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := targetImageTag(tt.version)
			if got != tt.want {
				t.Errorf("targetImageTag(%q) = %q, want %q", tt.version, got, tt.want)
			}
		})
	}
}

func TestLineDiff(t *testing.T) {
	tests := []struct {
		name         string
		old          string
		updated      string
		wantContains []string
		wantAbsent   []string
		wantEmpty    bool
	}{
		{
			name:      "identical input",
			old:       "line1\nline2\nline3",
			updated:   "line1\nline2\nline3",
			wantEmpty: true,
		},
		{
			name:         "added lines",
			old:          "line1\nline2",
			updated:      "line1\nline2\nline3",
			wantContains: []string{"+ line3"},
			wantAbsent:   []string{"- "},
		},
		{
			name:         "removed lines",
			old:          "line1\nline2\nline3",
			updated:      "line1\nline2",
			wantContains: []string{"- line3"},
			wantAbsent:   []string{"+ "},
		},
		{
			name:         "changed lines",
			old:          "aaa\nbbb",
			updated:      "aaa\nccc",
			wantContains: []string{"- bbb", "+ ccc"},
		},
		{
			name:      "trailing newline identical",
			old:       "line1\nline2\n",
			updated:   "line1\nline2\n",
			wantEmpty: true,
		},
		{
			name:         "trailing newline added",
			old:          "line1\nline2",
			updated:      "line1\nline2\n",
			wantContains: []string{"+ "},
		},
		{
			name:         "trailing newline removed",
			old:          "line1\nline2\n",
			updated:      "line1\nline2",
			wantContains: []string{"- "},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := lineDiff(tt.old, tt.updated)
			if tt.wantEmpty && got != "" {
				t.Errorf("expected empty diff, got %q", got)
			}
			for _, s := range tt.wantContains {
				if !strings.Contains(got, s) {
					t.Errorf("diff should contain %q, got %q", s, got)
				}
			}
			for _, s := range tt.wantAbsent {
				if strings.Contains(got, s) {
					t.Errorf("diff should not contain %q, got %q", s, got)
				}
			}
		})
	}
}

func FuzzLineDiff(f *testing.F) {
	f.Add("line1\nline2", "line1\nline3")
	f.Add("", "new content")
	f.Add("a\nb\nc", "a\nb\nc")
	f.Add("", "")
	f.Fuzz(func(t *testing.T, old, updated string) {
		// Should not panic on any input.
		_ = lineDiff(old, updated)
	})
}

func TestRedactSecret(t *testing.T) {
	tests := []struct {
		name string
		line string
		want string
	}{
		{
			name: "jwt secret redacted",
			line: `      SYNTHORG_JWT_SECRET: "supersecret123"`,
			want: `      SYNTHORG_JWT_SECRET: [REDACTED]`,
		},
		{
			name: "non-secret line unchanged",
			line: `      SYNTHORG_LOG_DIR: "/data/logs"`,
			want: `      SYNTHORG_LOG_DIR: "/data/logs"`,
		},
		{
			name: "case insensitive match",
			line: `      synthorg_jwt_secret: "abc"`,
			want: `      synthorg_jwt_secret: [REDACTED]`,
		},
		{
			name: "token key redacted",
			line: `      AUTH_TOKEN: "mytoken"`,
			want: `      AUTH_TOKEN: [REDACTED]`,
		},
		{
			name: "password key redacted",
			line: `      DB_PASSWORD: "hunter2"`,
			want: `      DB_PASSWORD: [REDACTED]`,
		},
		{
			name: "api key redacted",
			line: `      EXTERNAL_API_KEY: "key123"`,
			want: `      EXTERNAL_API_KEY: [REDACTED]`,
		},
		{
			name: "credentials key redacted",
			line: `      SERVICE_CREDENTIALS: "creds"`,
			want: `      SERVICE_CREDENTIALS: [REDACTED]`,
		},
		// Edge cases
		{
			name: "empty value after colon",
			line: `      JWT_SECRET:`,
			want: `      JWT_SECRET: [REDACTED]`,
		},
		{
			name: "single-quoted value",
			line: `      JWT_SECRET: 'single-quoted'`,
			want: `      JWT_SECRET: [REDACTED]`,
		},
		{
			name: "keyword as substring still redacts",
			line: `      NOT_A_SECRET_KEY: "value"`,
			want: `      NOT_A_SECRET_KEY: [REDACTED]`,
		},
		{
			name: "tab indentation",
			line: "\t\tDB_PASSWORD: \"pass\"",
			want: "\t\tDB_PASSWORD: [REDACTED]",
		},
		{
			name: "value with inline comment",
			line: `      JWT_SECRET: "val" # this is a comment`,
			want: `      JWT_SECRET: [REDACTED]`,
		},
		{
			name: "multiple colons in value",
			line: `      JWT_SECRET: "host:port:extra"`,
			want: `      JWT_SECRET: [REDACTED]`,
		},
		{
			name: "mixed case keyword",
			line: `      My_SeCrEt_Key: "mixed"`,
			want: `      My_SeCrEt_Key: [REDACTED]`,
		},
		{
			name: "no leading whitespace",
			line: `SECRET_KEY: "toplevel"`,
			want: `SECRET_KEY: [REDACTED]`,
		},
		{
			name: "settings key redacted",
			line: `      SYNTHORG_SETTINGS_KEY: "b64value=="`,
			want: `      SYNTHORG_SETTINGS_KEY: [REDACTED]`,
		},
		{
			name: "non-secret with colon in value unchanged",
			line: `      SYNTHORG_HOST: "0.0.0.0"`,
			want: `      SYNTHORG_HOST: "0.0.0.0"`,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := redactSecret(tt.line)
			if got != tt.want {
				t.Errorf("redactSecret(%q) = %q, want %q", tt.line, got, tt.want)
			}
		})
	}
}

func TestErrReexec_Identity(t *testing.T) {
	// Verify sentinel identity via errors.Is.
	if !errors.Is(errReexec, errReexec) {
		t.Fatal("errors.Is(errReexec, errReexec) should be true")
	}
	other := errors.New("other error")
	if errors.Is(other, errReexec) {
		t.Fatal("errors.Is(other, errReexec) should be false")
	}
	// Verify sentinel survives wrapping via %w.
	wrapped := fmt.Errorf("context: %w", errReexec)
	if !errors.Is(wrapped, errReexec) {
		t.Fatal("errors.Is(wrapped, errReexec) should be true")
	}
}

func TestLoadAndGenerate_NoCompose(t *testing.T) {
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	existing, fresh, err := loadAndGenerate(composePath, config.State{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if existing != nil || fresh != nil {
		t.Fatal("expected nil results when compose.yml does not exist")
	}
}

func TestLoadAndGenerate_PermissionError(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("permission-based test not reliable on Windows")
	}
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte("test"), 0o000); err != nil {
		t.Fatalf("setup: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(composePath, 0o600) })

	// Verify the environment actually enforces mode bits (root/containers may bypass).
	if _, readErr := os.ReadFile(composePath); readErr == nil {
		t.Skip("environment bypasses file mode bits (likely running as root)")
	}

	_, _, err := loadAndGenerate(composePath, config.State{})

	// Restore permissions before assertions so temp dir cleanup succeeds
	// even if an assertion panics or fails early.
	if chmodErr := os.Chmod(composePath, 0o600); chmodErr != nil {
		t.Fatalf("restoring permissions: %v", chmodErr)
	}

	if err == nil {
		t.Fatal("expected error for permission-denied compose.yml")
	}
	if !strings.Contains(err.Error(), "reading existing compose") {
		t.Errorf("error should mention reading compose, got: %v", err)
	}
}

// sandboxComposeFixture mirrors the shape compose.yml.tmpl actually renders
// for a sandbox+fine-tuning deployment: sandbox, sidecar, and fine-tune are
// never their own compose service (only backend and web get an `image:`
// line) -- their references live in backend's own environment block as
// SYNTHORG_{SANDBOX,SIDECAR,FINE_TUNE}_IMAGE. A fixture inventing a
// top-level `sandbox:` service would never catch patchComposeImageRefs
// failing to find the real env-var lines.
const sandboxComposeFixture = `# Generated by SynthOrg CLI v0.3.5
services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend@sha256:olddigest111
    ports:
      - "3001:3001"
    environment:
      SYNTHORG_LOG_LEVEL: "debug"
      SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox@sha256:olddigest333"
      SYNTHORG_SIDECAR_IMAGE: "ghcr.io/aureliolo/synthorg-sidecar@sha256:olddigest444"
      SYNTHORG_FINE_TUNE_IMAGE: "ghcr.io/aureliolo/synthorg-fine-tune-gpu@sha256:olddigest555"
  web:
    image: ghcr.io/aureliolo/synthorg-web:0.3.5
    ports:
      - "3000:8080"
`

func TestPatchComposeImageRefs(t *testing.T) {
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(sandboxComposeFixture), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	// verify.FormatImageRef only trusts a digest that matches the real OCI
	// sha256 shape (see verify.IsValidDigest) -- an arbitrary placeholder
	// like "newdigest111" is rejected and silently falls back to the tag,
	// which is correct production behaviour (a real cosign-verified digest
	// always has this shape) but means the fixture has to use it too.
	pins := map[string]string{
		"backend":       "sha256:" + strings.Repeat("1", 64),
		"web":           "sha256:" + strings.Repeat("2", 64),
		"sandbox":       "sha256:" + strings.Repeat("3", 64),
		"sidecar":       "sha256:" + strings.Repeat("4", 64),
		"fine-tune-gpu": "sha256:" + strings.Repeat("5", 64),
	}
	state := config.State{ImageTag: "0.3.6", Sandbox: true, FineTuning: true}
	if err := patchComposeImageRefs(state, pins, dir); err != nil {
		t.Fatalf("patchComposeImageRefs: %v", err)
	}

	result, err := os.ReadFile(composePath)
	if err != nil {
		t.Fatalf("reading patched compose: %v", err)
	}
	got := string(result)

	// Image refs should be updated: the backend/web compose `image:` lines
	// and the sandbox/sidecar/fine-tune env vars on backend.
	for _, want := range []string{
		"ghcr.io/aureliolo/synthorg-backend@" + pins["backend"],
		"ghcr.io/aureliolo/synthorg-web@" + pins["web"],
		`SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox@` + pins["sandbox"] + `"`,
		`SYNTHORG_SIDECAR_IMAGE: "ghcr.io/aureliolo/synthorg-sidecar@` + pins["sidecar"] + `"`,
		`SYNTHORG_FINE_TUNE_IMAGE: "ghcr.io/aureliolo/synthorg-fine-tune-gpu@` + pins["fine-tune-gpu"] + `"`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("expected patched compose to contain %q, got:\n%s", want, got)
		}
	}

	// Non-image lines should be preserved exactly.
	if !strings.Contains(got, "SYNTHORG_LOG_LEVEL") {
		t.Error("non-image config was modified")
	}
	if !strings.Contains(got, "v0.3.5") {
		t.Error("CLI version comment was modified (should be preserved)")
	}
}

// TestPatchComposeImageRefs_FineTuneCPUVariant covers the fine-tune name
// resolving through the configured variant instead of always assuming GPU.
func TestPatchComposeImageRefs_FineTuneCPUVariant(t *testing.T) {
	const fixture = `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend:0.3.5
    environment:
      SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:0.3.5"
      SYNTHORG_SIDECAR_IMAGE: "ghcr.io/aureliolo/synthorg-sidecar:0.3.5"
      SYNTHORG_FINE_TUNE_IMAGE: "ghcr.io/aureliolo/synthorg-fine-tune-cpu:0.3.5"
  web:
    image: ghcr.io/aureliolo/synthorg-web:0.3.5
`
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(fixture), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	state := config.State{ImageTag: "0.3.6", Sandbox: true, FineTuning: true, FineTuningVariant: "cpu"}
	if err := patchComposeImageRefs(state, nil, dir); err != nil {
		t.Fatalf("patchComposeImageRefs: %v", err)
	}

	result, err := os.ReadFile(composePath)
	if err != nil {
		t.Fatalf("reading patched compose: %v", err)
	}
	got := string(result)
	if !strings.Contains(got, "ghcr.io/aureliolo/synthorg-fine-tune-cpu:0.3.6") {
		t.Errorf("expected fine-tune-cpu ref, got: %s", got)
	}
	if strings.Contains(got, "fine-tune-gpu") {
		t.Errorf("expected the CPU variant, not GPU, got: %s", got)
	}
}

// TestPatchComposeImageRefs_MissingSandboxEnv is a regression test: sandbox
// mode enabled but compose.yml carries no SYNTHORG_SANDBOX_IMAGE line (e.g.
// a hand-edited file, or one left behind by a build predating this env-var
// shape) must fail loud rather than silently leaving the sandbox image
// unpatched and reporting success -- the exact failure previously
// misreported as "compose changed" upstream and then bailed out here after
// the operator confirmed the fallback patch.
func TestPatchComposeImageRefs_MissingSandboxEnv(t *testing.T) {
	const fixture = `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend:0.3.5
  web:
    image: ghcr.io/aureliolo/synthorg-web:0.3.5
`
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(fixture), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	state := config.State{ImageTag: "0.3.6", Sandbox: true}
	err := patchComposeImageRefs(state, nil, dir)
	if err == nil {
		t.Fatal("expected error when SYNTHORG_SANDBOX_IMAGE is missing but sandbox is enabled")
	}
	if !strings.Contains(err.Error(), "SYNTHORG_SANDBOX_IMAGE") {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestPatchComposeImageRefs_MissingFineTuneEnv mirrors
// TestPatchComposeImageRefs_MissingSandboxEnv for the third, independent
// completeness branch in requireComposeImageRefsPatched: fine-tuning
// enabled but compose.yml carries no SYNTHORG_FINE_TUNE_IMAGE line.
func TestPatchComposeImageRefs_MissingFineTuneEnv(t *testing.T) {
	const fixture = `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend:0.3.5
    environment:
      SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:0.3.5"
      SYNTHORG_SIDECAR_IMAGE: "ghcr.io/aureliolo/synthorg-sidecar:0.3.5"
  web:
    image: ghcr.io/aureliolo/synthorg-web:0.3.5
`
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(fixture), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	state := config.State{ImageTag: "0.3.6", Sandbox: true, FineTuning: true}
	err := patchComposeImageRefs(state, nil, dir)
	if err == nil {
		t.Fatal("expected error when SYNTHORG_FINE_TUNE_IMAGE is missing but fine-tuning is enabled")
	}
	if !strings.Contains(err.Error(), "SYNTHORG_FINE_TUNE_IMAGE") {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestPatchComposeImageRefs_MatchesRealTemplate is a round-trip regression
// test against the defect class where a hand-written fixture believes
// something false about compose.yml.tmpl's actual shape. It builds the
// "existing compose.yml" input from the REAL template via
// compose.ParamsFromState + compose.Generate, for every (Sandbox,
// FineTuning) combination the template renders differently for, then runs
// patchComposeImageRefs against that genuine output. This also guards
// requireComposeImageRefsPatched's hand-copied conditionals (backend/web
// always; SANDBOX/SIDECAR under Sandbox; FINE_TUNE under Sandbox &&
// FineTuning) from drifting away from the template's own {{if .Sandbox}}
// / {{if and .Sandbox .FineTuning}} gates.
func TestPatchComposeImageRefs_MatchesRealTemplate(t *testing.T) {
	cases := []struct {
		name       string
		sandbox    bool
		fineTuning bool
	}{
		{"no sandbox", false, false},
		{"sandbox only", true, false},
		{"sandbox and fine-tuning", true, true},
	}

	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			state := config.State{
				ImageTag:           "0.9.0",
				BackendPort:        3001,
				WebPort:            3000,
				LogLevel:           "info",
				PersistenceBackend: "sqlite",
				MemoryBackend:      "sqlvector",
				BusBackend:         "internal",
				DockerSock:         "/var/run/docker.sock",
				Sandbox:            tt.sandbox,
				FineTuning:         tt.fineTuning,
			}
			params, err := compose.ParamsFromState(state)
			if err != nil {
				t.Fatalf("ParamsFromState: %v", err)
			}
			rendered, err := compose.Generate(params)
			if err != nil {
				t.Fatalf("Generate: %v", err)
			}

			dir := t.TempDir()
			composePath := filepath.Join(dir, "compose.yml")
			if err := os.WriteFile(composePath, rendered, 0o600); err != nil {
				t.Fatalf("setup: %v", err)
			}

			pins := map[string]string{
				"backend": "sha256:" + strings.Repeat("1", 64),
				"web":     "sha256:" + strings.Repeat("2", 64),
			}
			if tt.sandbox {
				pins["sandbox"] = "sha256:" + strings.Repeat("3", 64)
				pins["sidecar"] = "sha256:" + strings.Repeat("4", 64)
			}
			if tt.fineTuning {
				pins["fine-tune-gpu"] = "sha256:" + strings.Repeat("5", 64)
			}

			newState := state
			newState.ImageTag = "0.9.1"
			if err := patchComposeImageRefs(newState, pins, dir); err != nil {
				t.Fatalf("patchComposeImageRefs against a real-template-rendered compose.yml: %v", err)
			}
		})
	}
}

// FuzzPatchStandaloneImageEnvRefs guards the SYNTHORG_{SANDBOX,SIDECAR,
// FINE_TUNE}_IMAGE parser against arbitrary (potentially hand-edited)
// compose.yml content, matching this file's existing fuzz-coverage
// convention for its other regex-driven parsers (lineDiff,
// isUpdateBoilerplateOnly).
func FuzzPatchStandaloneImageEnvRefs(f *testing.F) {
	f.Add(sandboxComposeFixture)
	f.Add("")
	f.Add(`services:
  backend:
    environment:
      SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:0.3.5"
`)
	f.Add(`SYNTHORG_SANDBOX_IMAGE: "unterminated`)
	f.Add("SYNTHORG_FINE_TUNE_IMAGE:\n")
	f.Fuzz(func(t *testing.T, existing string) {
		state := config.State{ImageTag: "0.3.6", FineTuning: true}
		pins := map[string]string{"sandbox": "sha256:" + strings.Repeat("1", 64)}
		// Should not panic on any input.
		_, _ = patchStandaloneImageEnvRefs(existing, state, pins)
	})
}

func TestPatchComposeImageRefs_TagFallback(t *testing.T) {
	const oldCompose = `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend:0.3.5
  web:
    image: ghcr.io/aureliolo/synthorg-web:0.3.5
`
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(oldCompose), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	// No digest pins -- should fall back to tag.
	state := config.State{ImageTag: "0.3.6"}
	if err := patchComposeImageRefs(state, nil, dir); err != nil {
		t.Fatalf("patchComposeImageRefs: %v", err)
	}

	result, err := os.ReadFile(composePath)
	if err != nil {
		t.Fatalf("reading patched compose: %v", err)
	}
	got := string(result)
	if !strings.Contains(got, "ghcr.io/aureliolo/synthorg-backend:0.3.6") {
		t.Errorf("expected tag-based backend ref, got: %s", got)
	}
	if !strings.Contains(got, "ghcr.io/aureliolo/synthorg-web:0.3.6") {
		t.Errorf("expected tag-based web ref, got: %s", got)
	}
}

func TestPatchComposeImageRefs_NoMatchesError(t *testing.T) {
	const customCompose = `services:
  myapp:
    image: registry.example.com/myapp:latest
`
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(customCompose), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	err := patchComposeImageRefs(config.State{ImageTag: "0.3.6"}, nil, dir)
	if err == nil {
		t.Fatal("expected error when no synthorg image refs found")
	}
	if !strings.Contains(err.Error(), "no synthorg image references found") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestMigrateSettingsKey(t *testing.T) {
	dir := t.TempDir()

	// Simulate a pre-v0.3.9 config without SettingsKey.
	state := config.State{
		DataDir:            dir,
		ImageTag:           "v0.3.8",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		JWTSecret:          "test-jwt-secret-at-least-32-chars-long!!",
		SettingsKey:        "", // intentionally empty
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
	}
	if err := config.Save(state); err != nil {
		t.Fatalf("saving initial config: %v", err)
	}

	// Verify key is initially empty.
	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("loading config: %v", err)
	}
	if loaded.SettingsKey != "" {
		t.Fatalf("expected empty SettingsKey before migration, got %q", loaded.SettingsKey)
	}

	// Run the migration inline (extracted from runUpdate).
	if loaded.SettingsKey == "" {
		key, genErr := generateSecret(32)
		if genErr != nil {
			t.Fatalf("generating settings key: %v", genErr)
		}
		loaded.SettingsKey = key
		if saveErr := config.Save(loaded); saveErr != nil {
			t.Fatalf("saving migrated config: %v", saveErr)
		}
	}

	// Reload and verify the key was persisted.
	reloaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("reloading config: %v", err)
	}
	if reloaded.SettingsKey == "" {
		t.Fatal("expected non-empty SettingsKey after migration")
	}
	// Fernet keys are 44 chars (32 bytes, URL-safe base64 with padding).
	if len(reloaded.SettingsKey) != 44 {
		t.Errorf("SettingsKey length = %d, want 44 (Fernet key)", len(reloaded.SettingsKey))
	}
}

// Fixture conventions for updateBoilerplateOnlyCases and TestExtractImageRepo:
//
//   - Image tags use abstract placeholders (`fixture-tag-A`, `fixture-tag-old`,
//     `fixture-tag-new`, `0.3.9`, `0.3.10`, `1.0`) chosen for clarity, not
//     realism. They flow through string-shape parsers that only inspect tag /
//     digest presence, repo identity, and equality between `existing` and
//     `fresh`; the literal values are arbitrary.
//   - Image digests use short synthetic placeholders (`aaa`, `bbb`, `ccc`,
//     `ddd`, `aaa111`, `bbb222`). The parser only requires the `sha256:`
//     prefix; the hex body is never validated, so 3-char placeholders keep
//     the table compact.
//   - Cases asserting "same tag, different digest" reuse one tag (e.g.
//     `fixture-tag-A`); cases asserting "tag bump" use distinct tags
//     (`fixture-tag-old` vs `fixture-tag-new`).
var updateBoilerplateOnlyCases = []struct {
	name     string
	existing string
	fresh    string
	want     bool
}{
	{
		name:     "identical content",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n",
		want:     true,
	},
	{
		name:     "version comment only diff",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n",
		want:     true,
	},
	{
		name:     "image digest change",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:aaa111\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:bbb222\n",
		want:     true,
	},
	{
		name:     "image tag change",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:0.3.9\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:0.3.10\n",
		want:     true,
	},
	{
		name:     "multiple image changes",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:aaa\n  web:\n    image: ghcr.io/aureliolo/synthorg-web@sha256:bbb\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:ccc\n  web:\n    image: ghcr.io/aureliolo/synthorg-web@sha256:ddd\n",
		want:     true,
	},
	{
		name:     "substantive diff beyond images",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    ports:\n      - 3001:3001\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    ports:\n      - 4001:4001\n",
		want:     false,
	},
	{
		name:     "DHI nats digest bump",
		existing: "# Generated by SynthOrg CLI 0.7.3-dev.11\nservices:\n  nats:\n    image: dhi.io/nats:fixture-tag-A@sha256:aaa\n",
		fresh:    "# Generated by SynthOrg CLI 0.7.3-dev.19\nservices:\n  nats:\n    image: dhi.io/nats:fixture-tag-A@sha256:bbb\n",
		want:     true,
	},
	{
		name:     "DHI postgres tag bump",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  postgres:\n    image: dhi.io/postgres:fixture-tag-old\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  postgres:\n    image: dhi.io/postgres:fixture-tag-new\n",
		want:     true,
	},
	{
		name:     "image repo change rejected",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  postgres:\n    image: dhi.io/postgres:fixture-tag-old\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  postgres:\n    image: docker.io/library/postgres:fixture-tag-old\n",
		want:     false,
	},
	{
		name:     "mixed synthorg + DHI bump",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:aaa\n  nats:\n    image: dhi.io/nats:fixture-tag-A@sha256:bbb\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:ccc\n  nats:\n    image: dhi.io/nats:fixture-tag-A@sha256:ddd\n",
		want:     true,
	},
	{
		name:     "CRLF line endings handled",
		existing: "# Generated by SynthOrg CLI v0.3.9\r\nservices:\r\n  backend:\r\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:aaa\r\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\r\nservices:\r\n  backend:\r\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:ccc\r\n",
		want:     true,
	},
	{
		name:     "pin removed (digest dropped) is substantive",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:aaa\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend\n",
		want:     false,
	},
	{
		name:     "pin added (was unpinned, now digest) is substantive",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:ccc\n",
		want:     false,
	},
	{
		name:     "tag-only gains digest is substantive",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0@sha256:ccc\n",
		want:     false,
	},
	{
		name:     "tag+digest loses digest is substantive",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0@sha256:aaa\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0\n",
		want:     false,
	},
	{
		name:     "tag-only swap to digest-only is substantive",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:ccc\n",
		want:     false,
	},
	{
		name:     "tag+digest digest-only bump (same shape) is boilerplate",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0@sha256:aaa\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  backend:\n    image: ghcr.io/aureliolo/synthorg-backend:1.0@sha256:bbb\n",
		want:     true,
	},
	{
		name:     "new line added",
		existing: "# Generated by SynthOrg CLI v0.3.9\nservices:\n",
		fresh:    "# Generated by SynthOrg CLI v0.3.10\nservices:\n  new_service:\n",
		want:     false,
	},
	{
		name:     "single line no newline",
		existing: "# comment only",
		fresh:    "# different comment",
		want:     false,
	},
	{
		// Empty-to-empty returns true here, but the caller (refreshCompose)
		// hits bytes.Equal first and never reaches isUpdateBoilerplateOnly.
		name:     "empty input",
		existing: "",
		fresh:    "",
		want:     true,
	},
	{
		name:     "different line count",
		existing: "line1\nline2\n",
		fresh:    "line1\n",
		want:     false,
	},
}

func TestIsUpdateBoilerplateOnly(t *testing.T) {
	for _, tt := range updateBoilerplateOnlyCases {
		t.Run(tt.name, func(t *testing.T) {
			got := isUpdateBoilerplateOnly([]byte(tt.existing), []byte(tt.fresh))
			if got != tt.want {
				t.Errorf("isUpdateBoilerplateOnly() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestExtractImageRepo(t *testing.T) {
	tests := []struct {
		name          string
		line          string
		wantOK        bool
		wantRepo      string
		wantHasTag    bool
		wantHasDigest bool
	}{
		{"synthorg digest only", "    image: ghcr.io/aureliolo/synthorg-backend@sha256:abc", true, "ghcr.io/aureliolo/synthorg-backend", false, true},
		{"synthorg tag only", "    image: ghcr.io/aureliolo/synthorg-backend:0.7.3", true, "ghcr.io/aureliolo/synthorg-backend", true, false},
		{"synthorg tag + digest", "    image: ghcr.io/aureliolo/synthorg-backend:0.7.3@sha256:abc", true, "ghcr.io/aureliolo/synthorg-backend", true, true},
		{"DHI nats tag + digest", "    image: dhi.io/nats:fixture-tag-A@sha256:abc", true, "dhi.io/nats", true, true},
		{"DHI postgres tag-only", "    image: dhi.io/postgres:fixture-tag-old", true, "dhi.io/postgres", true, false},
		{"registry with port and tag", "    image: localhost:5000/synthorg-backend:latest", true, "localhost:5000/synthorg-backend", true, false},
		{"registry with port and digest", "    image: localhost:5000/synthorg-backend@sha256:abc", true, "localhost:5000/synthorg-backend", false, true},
		{"registry port differs", "    image: localhost:6000/synthorg-backend:latest", true, "localhost:6000/synthorg-backend", true, false},
		{"tabs indent tag", "\timage: ghcr.io/aureliolo/synthorg-backend:0.7.3", true, "ghcr.io/aureliolo/synthorg-backend", true, false},
		{"trailing comment", "    image: ghcr.io/foo/bar:tag  # pinned", true, "ghcr.io/foo/bar", true, false},
		{"unpinned (no tag, no digest)", "    image: ghcr.io/aureliolo/synthorg-backend", true, "ghcr.io/aureliolo/synthorg-backend", false, false},
		{"unpinned with comment", "    image: dhi.io/nats  # rolling", true, "dhi.io/nats", false, false},
		{"empty value", "    image:", false, "", false, false},
		{"missing image keyword", "    foo: bar", false, "", false, false},
		{"comment line", "# image: ghcr.io/foo:tag", false, "", false, false},
		{"yaml array reject", "    image: [a, b]", false, "", false, false},
		{"empty line", "", false, "", false, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			repo, hasTag, hasDigest, ok := extractImageRepo(tt.line)
			if ok != tt.wantOK {
				t.Fatalf("extractImageRepo(%q) ok = %v, want %v", tt.line, ok, tt.wantOK)
			}
			if repo != tt.wantRepo {
				t.Errorf("extractImageRepo(%q) repo = %q, want %q", tt.line, repo, tt.wantRepo)
			}
			if hasTag != tt.wantHasTag {
				t.Errorf("extractImageRepo(%q) hasTag = %v, want %v", tt.line, hasTag, tt.wantHasTag)
			}
			if hasDigest != tt.wantHasDigest {
				t.Errorf("extractImageRepo(%q) hasDigest = %v, want %v", tt.line, hasDigest, tt.wantHasDigest)
			}
		})
	}
}

func FuzzIsUpdateBoilerplateOnly(f *testing.F) {
	f.Add("# v1\nrest", "# v2\nrest")
	f.Add("", "")
	f.Add("no newline", "also no newline")
	f.Add("# comment\n", "# comment\n")
	f.Add("# Generated by SynthOrg CLI v0.3.9\nservices:\n", "# Generated by SynthOrg CLI v0.3.10\nservices:\n")
	f.Add(
		"# Generated by SynthOrg CLI v0.3.9\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:aaa\n",
		"# Generated by SynthOrg CLI v0.3.10\n    image: ghcr.io/aureliolo/synthorg-backend@sha256:bbb\n",
	)
	f.Fuzz(func(t *testing.T, existing, fresh string) {
		// Should not panic on any input.
		_ = isUpdateBoilerplateOnly([]byte(existing), []byte(fresh))
	})
}

func TestPatchComposeImageRefs_MissingRequiredService(t *testing.T) {
	// Only backend, no web -- should fail validation.
	const partialCompose = `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend:0.3.5
`
	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(partialCompose), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	err := patchComposeImageRefs(config.State{ImageTag: "0.3.6"}, nil, dir)
	if err == nil {
		t.Fatal("expected error when web service not found")
	}
	if !strings.Contains(err.Error(), `"web" not found`) {
		t.Errorf("unexpected error: %v", err)
	}
}

// TestBuildReexecArgs_HealthRecovered pins the installation-health
// verdict carried across the update re-exec: when the parent resolved
// recovered=true (force a pull without re-prompting), the child argv
// must carry --health-recovered; when false it must not. The function
// reads the package-level flag globals, so the case zeroes the ones it
// asserts on and restores them via t.Cleanup (no t.Parallel: shared
// globals).
func TestBuildReexecArgs_HealthRecovered(t *testing.T) {
	origDataDir, origSkipVerify := flagDataDir, flagSkipVerify
	origQuiet, origVerbose := flagQuiet, flagVerbose
	origNoColor, origPlain, origJSON, origYes := flagNoColor, flagPlain, flagJSON, flagYes
	origNoRestart, origImagesOnly, origCLIOnly, origTimeout :=
		updateNoRestart, updateImagesOnly, updateCLIOnly, updateTimeout
	t.Cleanup(func() {
		flagDataDir, flagSkipVerify = origDataDir, origSkipVerify
		flagQuiet, flagVerbose = origQuiet, origVerbose
		flagNoColor, flagPlain, flagJSON, flagYes = origNoColor, origPlain, origJSON, origYes
		updateNoRestart, updateImagesOnly, updateCLIOnly, updateTimeout =
			origNoRestart, origImagesOnly, origCLIOnly, origTimeout
	})

	flagDataDir, flagSkipVerify = "", false
	flagQuiet, flagVerbose = false, 0
	flagNoColor, flagPlain, flagJSON, flagYes = false, false, false, false
	updateNoRestart, updateImagesOnly, updateCLIOnly, updateTimeout = false, false, false, "90s"

	newCmd := func() *cobra.Command {
		c := &cobra.Command{Use: "update"}
		c.Flags().String("timeout", "90s", "")
		c.SetErr(&bytes.Buffer{})
		return c
	}

	recoveredArgs := buildReexecArgs(newCmd(), true)
	if !slices.Contains(recoveredArgs, "--health-recovered") {
		t.Errorf("recovered=true must append --health-recovered; got %v", recoveredArgs)
	}
	if !slices.Contains(recoveredArgs, "--skip-cli-update") {
		t.Errorf("re-exec args must always carry --skip-cli-update; got %v", recoveredArgs)
	}

	plainArgs := buildReexecArgs(newCmd(), false)
	if slices.Contains(plainArgs, "--health-recovered") {
		t.Errorf("recovered=false must not append --health-recovered; got %v", plainArgs)
	}
}

func TestIsDevChannelMismatch(t *testing.T) {
	tests := []struct {
		name    string
		channel string
		version string
		want    bool
	}{
		{"dev build on stable channel", "stable", "0.5.0-dev.8", true},
		{"dev build on dev channel", "dev", "0.5.0-dev.8", false},
		{"stable build on stable channel", "stable", "0.4.9", false},
		{"stable build on dev channel", "dev", "0.4.9", false},
		{"local dev build", "stable", "dev", false},
		{"empty version", "stable", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := isDevChannelMismatch(tt.channel, tt.version)
			if got != tt.want {
				t.Errorf("isDevChannelMismatch(%q, %q) = %v, want %v", tt.channel, tt.version, got, tt.want)
			}
		})
	}
}

// `update --check` prints one line and exits, so that line is the whole of
// what an operator sees before deciding to install: the terse path never
// reaches the changelog walk that scrubs everything else. The target is a
// remote tag name, so it takes the same scrub the walk's labels do.
func TestRunUpdateCheck_scrubsSpoofedTargetVersion(t *testing.T) {
	prev := checkForChannel
	checkForChannel = func(_ context.Context, _ string) (selfupdate.CheckResult, error) {
		return selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.7.4",
			LatestVersion:  spoofedTargetTag,
		}, nil
	}
	t.Cleanup(func() { checkForChannel = prev })

	cmd := &cobra.Command{}
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Hints: "always"}))
	var buf bytes.Buffer
	cmd.SetOut(&buf)

	err := runUpdateCheck(cmd, config.State{Channel: "stable"})

	var exitErr *ExitError
	if !errors.As(err, &exitErr) || exitErr.Code != ExitUpdateAvail {
		t.Fatalf("expected ExitUpdateAvail, got %v", err)
	}
	got := buf.String()
	requireContains(t, got, spoofedTargetSaf)
	requireLacks(t, got, rloRune, zwspRune, wordJoinerRune)
	// The installed version is stamped without the "v" by GoReleaser, so the
	// two halves of this line would otherwise disagree about the prefix.
	requireContains(t, got, "(current: v0.7.4)")
}

// TestUpdateCLI_checkFailureAborts verifies that a failed update-check
// aborts with an ExitRuntime-wrapped ExitError (so Execute does not
// re-print it) and surfaces the recovery hint on stderr, rather than
// continuing into the changelog/download steps.
func TestUpdateCLI_checkFailureAborts(t *testing.T) {
	prev := checkForChannel
	checkForChannel = func(_ context.Context, _ string) (selfupdate.CheckResult, error) {
		return selfupdate.CheckResult{}, errors.New("github API returned 503")
	}
	t.Cleanup(func() { checkForChannel = prev })

	cmd := &cobra.Command{}
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Hints: "always", DataDir: t.TempDir()}))
	cmd.Flags().Bool("skip-cli-update", false, "")
	var out, errOut bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&errOut)

	err := updateCLI(cmd, false)
	if err == nil {
		t.Fatal("expected an error when the update check fails")
	}

	var exitErr *ExitError
	if !errors.As(err, &exitErr) {
		t.Fatalf("expected *ExitError, got %T: %v", err, err)
	}
	if exitErr.Code != ExitRuntime {
		t.Errorf("exit code = %d, want ExitRuntime (%d)", exitErr.Code, ExitRuntime)
	}
	if !strings.Contains(err.Error(), "checking for updates") {
		t.Errorf("error = %q, want it to wrap 'checking for updates'", err.Error())
	}
	if !strings.Contains(errOut.String(), "--images-only") {
		t.Errorf("stderr = %q, want the --images-only recovery hint", errOut.String())
	}
}

// updateCmdForTest builds a bare command carrying the global opts the
// update helpers read, returning it with its stdout buffer.
func updateCmdForTest(t *testing.T) (*cobra.Command, *bytes.Buffer) {
	t.Helper()
	cmd := &cobra.Command{}
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Hints: "always", DataDir: t.TempDir()}))
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&bytes.Buffer{})
	return cmd, &out
}

// Dismissing a confirm ends the run without an error, whatever each call
// site wrapped the sentinel in on the way out. The wrappers are the real
// ones: an equality check here would pass while the command still exited
// non-zero.
func TestFinishUpdateTreatsACancelledPromptAsACleanStop(t *testing.T) {
	tests := []struct {
		name string
		err  error
	}{
		{"cli update confirm", fmt.Errorf("updating CLI binary: %w", fmt.Errorf("confirming CLI update: %w", errUpdateCancelled))},
		{"image update confirm", fmt.Errorf("updating compose and images: %w", fmt.Errorf("confirming image update: %w", errUpdateCancelled))},
		{"compose apply confirm", fmt.Errorf("refreshing compose template: %w", errUpdateCancelled)},
		{"unwrapped", errUpdateCancelled},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cmd, out := updateCmdForTest(t)
			if err := finishUpdate(cmd, tc.err); err != nil {
				t.Fatalf("finishUpdate = %v, want nil", err)
			}
			if !strings.Contains(out.String(), "Update cancelled.") {
				t.Errorf("stdout = %q, want it to say the update was cancelled", out.String())
			}
		})
	}
}

func TestFinishUpdatePassesEveryOtherOutcomeThrough(t *testing.T) {
	genuine := errors.New("pulling updated images: no space left on device")
	cmd, out := updateCmdForTest(t)

	if err := finishUpdate(cmd, genuine); !errors.Is(err, genuine) {
		t.Fatalf("finishUpdate = %v, want the original error", err)
	}
	if strings.Contains(out.String(), "cancelled") {
		t.Errorf("stdout = %q, want no cancellation line for a real failure", out.String())
	}
	if err := finishUpdate(cmd, nil); err != nil {
		t.Errorf("finishUpdate(nil) = %v, want nil", err)
	}
}

// Dismissing a prompt and being killed mid-prompt arrive as one error, so
// the context is what separates them. Getting this wrong costs exit 130 on
// a real signal, which is what makes it worth pinning per outcome.
func TestClassifyPromptOutcomeSeparatesDismissalFromSignal(t *testing.T) {
	tests := []struct {
		name      string
		err       error
		cancelled bool
		want      error
	}{
		{"dismissed", huh.ErrUserAborted, false, errPromptDismissed},
		{"killed mid-prompt", huh.ErrUserAborted, true, context.Canceled},
		{"unrelated failure", errTerminalUnavailable, false, errTerminalUnavailable},
		{"unrelated failure while cancelled", errTerminalUnavailable, true, context.Canceled},
		{"answered", nil, false, nil},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if tc.cancelled {
				cancel()
			}
			if got := classifyPromptOutcome(ctx, tc.err); !errors.Is(got, tc.want) {
				t.Errorf("classifyPromptOutcome = %v, want %v", got, tc.want)
			}
		})
	}
}

var errTerminalUnavailable = errors.New("open /dev/tty: no such device")

// A dismissal must not read as an interruption, and an interruption must
// not read as a dismissal: the first exits 0 through the command's own
// clean stop, the second exits 130 through Execute's ctx.Err() branch.
func TestPromptDismissedIgnoresACancelledContextsError(t *testing.T) {
	if promptDismissed(context.Canceled) {
		t.Error("a cancelled context must not read as a dismissal")
	}
	if !promptDismissed(fmt.Errorf("confirming CLI update: %w", errPromptDismissed)) {
		t.Error("a wrapped dismissal must still read as one")
	}
}

// Declining the CLI update is not cancelling the run: runCLIUpdateStep
// reports done=false with no error, so runUpdateSteps carries on into the
// compose and image steps. Asserted on the step's own contract because
// reaching the image step for real needs a Docker daemon.
func TestDecliningTheCLIUpdateLetsTheRunContinue(t *testing.T) {
	prev := checkForChannel
	checkForChannel = func(_ context.Context, _ string) (selfupdate.CheckResult, error) {
		return selfupdate.CheckResult{CurrentVersion: "v9.9.9", UpdateAvail: false}, nil
	}
	t.Cleanup(func() { checkForChannel = prev })

	cmd, _ := updateCmdForTest(t)
	cmd.Flags().Bool("skip-cli-update", false, "")

	done, err := runCLIUpdateStep(cmd, config.State{}, false)
	if err != nil {
		t.Fatalf("runCLIUpdateStep = %v, want nil", err)
	}
	if done {
		t.Error("done = true, want false so the caller proceeds to compose and images")
	}
}

// The cancellation sentinel can only come from a rendered prompt: with
// prompting off, the confirm answers from the flags and never builds a
// form.
func TestConfirmUpdateAnswersWithoutAFormWhenPromptingIsOff(t *testing.T) {
	ctx := SetGlobalOpts(context.Background(), &GlobalOpts{Yes: true})

	ok, err := confirmUpdateWithDefault(ctx, "Update?", true, false)
	if err != nil || !ok {
		t.Errorf("confirmUpdateWithDefault = (%v, %v), want (true, nil)", ok, err)
	}
	ok, err = confirmUpdateWithDefault(ctx, "Update?", false, false)
	if err != nil || ok {
		t.Errorf("confirmUpdateWithDefault = (%v, %v), want (false, nil)", ok, err)
	}
}
