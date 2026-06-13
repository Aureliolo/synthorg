package cmd

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// NOTE: Tests in this file share the global rootCmd and must NOT call
// t.Parallel(). runBackupCmd routes through sandboxRootCmd
// (testhelpers_test.go), which snapshots and restores every flag's value AND
// Changed bit, so a prior --confirm / --sort no longer leaks into the next
// test and adding a new backup flag needs no per-flag reset here.

// --- Unit tests for helper functions ---

func TestFormatSize(t *testing.T) {
	tests := []struct {
		bytes int64
		want  string
	}{
		{0, "0 B"},
		{1, "1 B"},
		{512, "512 B"},
		{1023, "1023 B"},
		{1024, "1.0 KB"},
		{1536, "1.5 KB"},
		{1048576, "1.0 MB"},
		{1572864, "1.5 MB"},
		{1073741824, "1.0 GB"},
		{2684354560, "2.5 GB"},
	}
	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			if got := formatSize(tt.bytes); got != tt.want {
				t.Errorf("formatSize(%d) = %q, want %q", tt.bytes, got, tt.want)
			}
		})
	}
}

func TestIsValidBackupID(t *testing.T) {
	tests := []struct {
		name string
		id   string
		want bool
	}{
		{"valid 12-char hex", "abcdef012345", true},
		{"valid all digits", "012345678901", true},
		{"valid all a-f", "aabbccddeeff", true},
		{"uppercase not allowed", "ABCDEF012345", false},
		{"too short (11 chars)", "abcdef01234", false},
		{"too long (13 chars)", "abcdef0123456", false},
		{"empty string", "", false},
		{"non-hex chars", "abcdefghijkl", false},
		{"special char", "abcdef01234!", false},
		{"mixed case", "aBcDeF012345", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isValidBackupID(tt.id); got != tt.want {
				t.Errorf("isValidBackupID(%q) = %v, want %v", tt.id, got, tt.want)
			}
		})
	}
}

func TestComponentsString(t *testing.T) {
	tests := []struct {
		name       string
		components []string
		want       string
	}{
		{"multiple", []string{"persistence", "memory", "config"}, "persistence, memory, config"},
		{"single", []string{"persistence"}, "persistence"},
		{"empty", []string{}, ""},
		{"nil", nil, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := componentsString(tt.components); got != tt.want {
				t.Errorf("componentsString(%v) = %q, want %q", tt.components, got, tt.want)
			}
		})
	}
}

func TestParseAPIResponse(t *testing.T) {
	tests := []struct {
		name    string
		raw     string
		wantErr bool
		errMsg  string
	}{
		{
			name:    "success envelope",
			raw:     `{"data":{"backup_id":"abc123def456"},"error":null,"success":true}`,
			wantErr: false,
		},
		{
			name:    "error envelope",
			raw:     `{"data":null,"error":"something went wrong","success":false}`,
			wantErr: true,
			errMsg:  "something went wrong",
		},
		{
			name:    "error envelope with null error field",
			raw:     `{"data":null,"error":null,"success":false}`,
			wantErr: true,
			errMsg:  "unknown error",
		},
		{
			name:    "malformed JSON",
			raw:     `not json at all`,
			wantErr: true,
			errMsg:  "parsing response",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			data, err := parseAPIResponse([]byte(tt.raw))
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				if !strings.Contains(err.Error(), tt.errMsg) {
					t.Errorf("error %q does not contain %q", err.Error(), tt.errMsg)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if data == nil {
				t.Fatal("expected data, got nil")
			}
		})
	}
}

func TestSanitizeAPIMessage(t *testing.T) {
	tests := []struct {
		name string
		msg  string
		want string
	}{
		{"no escape sequences", "simple error", "simple error"},
		{"with ANSI color", "\x1b[31merror\x1b[0m", "error"},
		{"with cursor move", "\x1b[2Aoverwrite", "overwrite"},
		{"empty string", "", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := sanitizeAPIMessage(tt.msg); got != tt.want {
				t.Errorf("sanitizeAPIMessage(%q) = %q, want %q", tt.msg, got, tt.want)
			}
		})
	}
}

func TestBuildLocalJWT(t *testing.T) {
	token, err := buildLocalJWT("test-secret-that-is-at-least-32-characters-long")
	if err != nil {
		t.Fatalf("buildLocalJWT: %v", err)
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("expected 3 JWT parts, got %d", len(parts))
	}
	// Verify header is valid base64url-encoded JSON.
	headerJSON, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		t.Fatalf("decoding header: %v", err)
	}
	if !strings.Contains(string(headerJSON), `"alg":"HS256"`) {
		t.Errorf("header missing HS256 alg: %s", headerJSON)
	}
	// Verify payload contains expected claims via JSON unmarshal.
	payloadJSON, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		t.Fatalf("decoding payload: %v", err)
	}
	var claims map[string]any
	if err := json.Unmarshal(payloadJSON, &claims); err != nil {
		t.Fatalf("unmarshalling payload: %v", err)
	}
	// Cross-language: sub and iss must match the Python backend constants
	// in src/synthorg/api/auth/system_user.py -- keep in sync.
	if sub, _ := claims["sub"].(string); sub != "system" {
		t.Errorf("sub = %q, want %q", sub, "system")
	}
	if iss, _ := claims["iss"].(string); iss != "synthorg-cli" {
		t.Errorf("iss = %q, want %q", iss, "synthorg-cli")
	}
	// aud is validated by the backend middleware for system tokens
	// (defense-in-depth alongside the iss check).
	if aud, _ := claims["aud"].(string); aud != "synthorg-backend" {
		t.Errorf("aud = %q, want %q", aud, "synthorg-backend")
	}
	// iat and exp must be present with a 60-second window.
	iat, _ := claims["iat"].(float64)
	exp, _ := claims["exp"].(float64)
	if iat == 0 {
		t.Error("payload missing iat claim")
	}
	if exp == 0 {
		t.Error("payload missing exp claim")
	}
	if exp-iat != 60 {
		t.Errorf("expected exp-iat=60, got %v", exp-iat)
	}
}

func TestBuildLocalJWT_TooShort(t *testing.T) {
	_, err := buildLocalJWT("short")
	if err == nil {
		t.Fatal("expected error for short secret, got nil")
	}
	if !strings.Contains(err.Error(), "too short") {
		t.Errorf("error %q does not mention too short", err.Error())
	}
}

// --- Test helper: create temp dir with config.json ---

// writeConfigJSON creates a config.json file in dir with the given backend port.
func writeConfigJSON(t *testing.T, dir string, backendPort int) {
	t.Helper()
	cfg := map[string]any{
		"data_dir":            dir,
		"image_tag":           "latest",
		"backend_port":        backendPort,
		"web_port":            3000,
		"log_level":           "info",
		"persistence_backend": "sqlite",
		"memory_backend":      "mem0",
		"jwt_secret":          "test-backup-secret-at-least-32-chars",
		// encrypt_secrets defaults to true (DefaultState), which now
		// requires a master_key. These tests target backup behaviour,
		// not encryption, so opt out.
		"encrypt_secrets": false,
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		t.Fatalf("marshaling config: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), data, 0o600); err != nil {
		t.Fatalf("writing config: %v", err)
	}
}

// setupBackupTest creates an HTTP test server and a temp dir with config.json
// pointing at the server's port.
func setupBackupTest(t *testing.T, handler http.HandlerFunc) string {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)

	// Extract port from server URL.
	u, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatalf("parsing test server URL: %v", err)
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatalf("parsing test server port: %v", err)
	}

	dir := t.TempDir()
	writeConfigJSON(t, dir, port)
	return dir
}

// writeTestConfig creates a temp dir with a config.json file pointing at the
// given backend port. Used for tests that don't need a real HTTP server.
func writeTestConfig(t *testing.T, backendPort int) string {
	t.Helper()
	dir := t.TempDir()
	writeConfigJSON(t, dir, backendPort)
	return dir
}

// runBackupCmd executes a backup subcommand and returns combined stdout+stderr.
// sandboxRootCmd snapshots the writers and the value+Changed bit of every flag
// (including --confirm and --sort) and restores them on test cleanup, so each
// test sees a hermetic rootCmd and MarkFlagRequired keeps firing.
func runBackupCmd(t *testing.T, dir string, args ...string) (string, error) {
	t.Helper()
	stdout, stderr, _ := sandboxRootCmd(t)
	fullArgs := append([]string{"backup"}, args...)
	if dir != "" {
		fullArgs = append([]string{"--data-dir", dir}, fullArgs...)
	}
	rootCmd.SetArgs(fullArgs)
	err := rootCmd.Execute()
	return stdout.String() + stderr.String(), err
}

// --- Integration tests: backup create ---

func TestBackupCreate_Success(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/admin/backups" {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": {
				"backup_id": "abcdef012345",
				"synthorg_version": "0.3.5",
				"timestamp": "2026-03-18T10:00:00Z",
				"trigger": "manual",
				"components": ["persistence", "memory", "config"],
				"size_bytes": 1048576,
				"checksum": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
			},
			"error": null,
			"success": true
		}`))
	})

	out, err := runBackupCmd(t, dir)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for _, want := range []string{
		"Backup created successfully",
		"abcdef012345",
		"2026-03-18T10:00:00Z",
		"manual",
		"persistence, memory, config",
		"1.0 MB",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestBackupCreate_Conflict(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"data":null,"error":"A backup is already in progress","success":false}`))
	})

	out, err := runBackupCmd(t, dir)
	if err == nil {
		t.Fatal("expected error for conflict response")
	}
	if !strings.Contains(out, "already in progress") {
		t.Errorf("output missing conflict message:\n%s", out)
	}
}

func TestBackupCreate_ServerError(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"data":null,"error":"Backup operation failed","success":false}`))
	})

	out, err := runBackupCmd(t, dir)
	if err == nil {
		t.Fatal("expected error for server error response")
	}
	if !strings.Contains(out, "Backup operation failed") {
		t.Errorf("output missing error message:\n%s", out)
	}
}

func TestBackupCreate_Unreachable(t *testing.T) {
	// Use a port where nothing is listening.
	dir := writeTestConfig(t, 19999)

	_, err := runBackupCmd(t, dir)
	if err == nil {
		t.Fatal("expected error for unreachable backend")
	}
	if !strings.Contains(err.Error(), "backend unreachable") {
		t.Errorf("error %q does not mention unreachable backend", err.Error())
	}
}

// --- Integration tests: backup list ---

func TestBackupList_Success(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/api/v1/admin/backups" {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": [
				{
					"backup_id": "abcdef012345",
					"timestamp": "2026-03-18T10:00:00Z",
					"trigger": "manual",
					"components": ["persistence", "memory", "config"],
					"size_bytes": 1048576,
					"compressed": true
				},
				{
					"backup_id": "123456abcdef",
					"timestamp": "2026-03-17T08:00:00Z",
					"trigger": "scheduled",
					"components": ["persistence"],
					"size_bytes": 512,
					"compressed": false
				}
			],
			"error": null,
			"success": true
		}`))
	})

	out, err := runBackupCmd(t, dir, "list")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for _, want := range []string{
		"ID",
		"TIMESTAMP",
		"TRIGGER",
		"COMPONENTS",
		"SIZE",
		"COMPRESSED",
		"abcdef012345",
		"123456abcdef",
		"manual",
		"scheduled",
		"1.0 MB",
		"512 B",
		"yes",
		"no",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestBackupList_Empty(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[],"error":null,"success":true}`))
	})

	out, err := runBackupCmd(t, dir, "list")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "No backups found") {
		t.Errorf("output missing empty list message:\n%s", out)
	}
}

func TestBackupList_ServerError(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"data":null,"error":"Failed to list backups","success":false}`))
	})

	out, err := runBackupCmd(t, dir, "list")
	if err == nil {
		t.Fatal("expected error for server error response")
	}
	if !strings.Contains(out, "Failed to list backups") {
		t.Errorf("output missing error message:\n%s", out)
	}
}

func TestBackupList_InvalidSort(t *testing.T) {
	// --sort is validated in validateBackupListFlags; an unknown value
	// must be rejected before any API call.
	dir := writeTestConfig(t, 19999)

	out, err := runBackupCmd(t, dir, "list", "--sort", "alphabetical")
	if err == nil {
		t.Fatal("expected error for invalid --sort value")
	}
	if !strings.Contains(err.Error(), "invalid --sort") {
		t.Errorf("error %q does not mention invalid --sort", err.Error())
	}
	_ = out
}

func TestBackupList_SortCompletionRegistered(t *testing.T) {
	// Cobra exposes registered completions via __complete; if a fixed
	// completion is registered the values come back on stdout. We invoke
	// the special completion subcommand to confirm the three values are
	// surfaced. sandboxRootCmd restores the writers AND the flag value +
	// Changed state that Execute() otherwise leaks between tests.
	sandboxRootCmd(t)

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"__complete", "backup", "list", "--sort", ""})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("__complete: %v", err)
	}
	out := buf.String()
	for _, want := range []string{"newest", "oldest", "size"} {
		if !strings.Contains(out, want) {
			t.Errorf("completion output missing %q:\n%s", want, out)
		}
	}
}

// --- Integration tests: backup restore ---

func TestBackupRestore_Success(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/admin/backups/restore" {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		// Verify request body.
		var req restoreRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		if req.BackupID != "abcdef012345" || !req.Confirm {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": {
				"manifest": {
					"backup_id": "abcdef012345",
					"synthorg_version": "0.3.5",
					"timestamp": "2026-03-18T10:00:00Z",
					"trigger": "manual",
					"components": ["persistence", "memory", "config"],
					"size_bytes": 1048576,
					"checksum": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
				},
				"restored_components": ["persistence", "memory", "config"],
				"safety_backup_id": "fedcba543210",
				"restart_required": false
			},
			"error": null,
			"success": true
		}`))
	})

	out, err := runBackupCmd(t, dir, "restore", "abcdef012345", "--confirm")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for _, want := range []string{
		"Restore completed successfully",
		"fedcba543210",
		"persistence, memory, config",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestBackupRestore_NotFound(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"data":null,"error":"Backup not found: abcdef012345","success":false}`))
	})

	out, err := runBackupCmd(t, dir, "restore", "abcdef012345", "--confirm")
	if err == nil {
		t.Fatal("expected error for not-found response")
	}
	if !strings.Contains(out, "not found") {
		t.Errorf("output missing not-found message:\n%s", out)
	}
	if !strings.Contains(out, "backup list") {
		t.Errorf("output missing hint about backup list:\n%s", out)
	}
}

func TestBackupRestore_Conflict(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"data":null,"error":"A backup or restore is already in progress","success":false}`))
	})

	out, err := runBackupCmd(t, dir, "restore", "abcdef012345", "--confirm")
	if err == nil {
		t.Fatal("expected error for conflict response")
	}
	if !strings.Contains(out, "already in progress") {
		t.Errorf("output missing conflict message:\n%s", out)
	}
}

func TestBackupRestore_InvalidManifestPayload(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = w.Write([]byte(`{"data":null,"error":"Manifest validation failed","success":false}`))
	})

	out, err := runBackupCmd(t, dir, "restore", "abcdef012345", "--confirm")
	if err == nil {
		t.Fatal("expected error for unprocessable entity response")
	}
	if !strings.Contains(out, "Manifest validation failed") {
		t.Errorf("output missing invalid manifest message:\n%s", out)
	}
}

func TestBackupRestore_InvalidID(t *testing.T) {
	_, err := runBackupCmd(t, "", "restore", "not-valid-id", "--confirm")
	if err == nil {
		t.Fatal("expected error for invalid backup ID")
	}
	if !strings.Contains(err.Error(), "invalid backup ID") {
		t.Errorf("error %q does not mention invalid backup ID", err.Error())
	}
}

func TestBackupRestore_MissingConfirm(t *testing.T) {
	// No server needed -- --confirm is gated by Cobra's MarkFlagRequired
	// before the RunE handler is reached. rootCmd has SilenceUsage and
	// SilenceErrors set, so cobra's auto-emitted usage block never reaches
	// the buffer; we assert on the error value only.
	dir := writeTestConfig(t, 19999)

	_, err := runBackupCmd(t, dir, "restore", "abcdef012345")
	if err == nil {
		t.Fatal("expected error for missing --confirm flag")
	}
	// Cobra's required-flag error reads exactly: required flag(s) "confirm" not set
	const wantErr = `required flag(s) "confirm" not set`
	if !strings.Contains(err.Error(), wantErr) {
		t.Errorf("error %q does not contain %q", err.Error(), wantErr)
	}
}

func TestBackupRestore_RestartRequired(t *testing.T) {
	dir := setupBackupTest(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/admin/backups/restore" {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"data": {
				"manifest": {
					"backup_id": "abcdef012345",
					"synthorg_version": "0.3.5",
					"timestamp": "2026-03-18T10:00:00Z",
					"trigger": "manual",
					"components": ["persistence"],
					"size_bytes": 1024,
					"checksum": "sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
				},
				"restored_components": ["persistence"],
				"safety_backup_id": "fedcba543210",
				"restart_required": true
			},
			"error": null,
			"success": true
		}`))
	})

	out, err := runBackupCmd(t, dir, "restore", "abcdef012345", "--confirm")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for _, want := range []string{
		"Restore completed successfully",
		"fedcba543210",
		"Restart required",
		"yes",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}
