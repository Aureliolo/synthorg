package cmd

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestTargetImageTag(t *testing.T) {
	tests := []struct {
		name    string
		version string
		want    string
	}{
		{name: "with v prefix", version: "v0.2.7", want: "0.2.7"},
		{name: "without prefix", version: "0.2.6", want: "0.2.6"},
		{name: "dev build", version: "dev", want: "latest"},
		{name: "empty string", version: "", want: "latest"},
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

func TestPatchComposeImageRefs(t *testing.T) {
	const oldCompose = `# Generated by SynthOrg CLI v0.3.5
services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend@sha256:olddigest111
    ports:
      - "3001:3001"
    environment:
      SYNTHORG_LOG_LEVEL: "debug"
  web:
    image: ghcr.io/aureliolo/synthorg-web:0.3.5
    ports:
      - "3000:8080"
  sandbox:
    image: ghcr.io/aureliolo/synthorg-sandbox@sha256:olddigest333
`

	dir := t.TempDir()
	composePath := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(composePath, []byte(oldCompose), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}

	pins := map[string]string{
		"backend": "sha256:newdigest111",
		"web":     "sha256:newdigest222",
		"sandbox": "sha256:newdigest333",
	}
	if err := patchComposeImageRefs("0.3.6", pins, true, dir); err != nil {
		t.Fatalf("patchComposeImageRefs: %v", err)
	}

	result, err := os.ReadFile(composePath)
	if err != nil {
		t.Fatalf("reading patched compose: %v", err)
	}
	got := string(result)

	// Image refs should be updated.
	if !strings.Contains(got, "ghcr.io/aureliolo/synthorg-backend@sha256:newdigest111") {
		t.Error("backend image not patched")
	}
	if !strings.Contains(got, "ghcr.io/aureliolo/synthorg-web@sha256:newdigest222") {
		t.Error("web image not patched")
	}
	if !strings.Contains(got, "ghcr.io/aureliolo/synthorg-sandbox@sha256:newdigest333") {
		t.Error("sandbox image not patched")
	}

	// Non-image lines should be preserved exactly.
	if !strings.Contains(got, "SYNTHORG_LOG_LEVEL") {
		t.Error("non-image config was modified")
	}
	if !strings.Contains(got, "v0.3.5") {
		t.Error("CLI version comment was modified (should be preserved)")
	}
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
	if err := patchComposeImageRefs("0.3.6", nil, false, dir); err != nil {
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

	err := patchComposeImageRefs("0.3.6", nil, false, dir)
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
		MemoryBackend:      "mem0",
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
		existing: "# Generated by SynthOrg CLI 0.7.3-dev.11\nservices:\n  nats:\n    image: dhi.io/nats:fixture-tag-A@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
		fresh:    "# Generated by SynthOrg CLI 0.7.3-dev.19\nservices:\n  nats:\n    image: dhi.io/nats:fixture-tag-A@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
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

	err := patchComposeImageRefs("0.3.6", nil, false, dir)
	if err == nil {
		t.Fatal("expected error when web service not found")
	}
	if !strings.Contains(err.Error(), `"web" not found`) {
		t.Errorf("unexpected error: %v", err)
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
