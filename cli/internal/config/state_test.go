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
	if s.BackendPort != 8000 {
		t.Errorf("BackendPort = %d, want 8000", s.BackendPort)
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
	if s.DataDir == "" {
		t.Error("DataDir should not be empty")
	}
}

func TestSaveAndLoad(t *testing.T) {
	tmp := t.TempDir()
	s := State{
		DataDir:     tmp,
		ImageTag:    "v0.1.5",
		BackendPort: 9000,
		WebPort:     3001,
		LogLevel:    "debug",
		JWTSecret:   "test-secret",
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
	if loaded.WebPort != s.WebPort {
		t.Errorf("WebPort = %d, want %d", loaded.WebPort, s.WebPort)
	}
	if loaded.LogLevel != s.LogLevel {
		t.Errorf("LogLevel = %q, want %q", loaded.LogLevel, s.LogLevel)
	}
}

func TestSaveCreatesDirectory(t *testing.T) {
	tmp := t.TempDir()
	nested := filepath.Join(tmp, "nested", "deep")
	s := State{
		DataDir:     nested,
		ImageTag:    "latest",
		BackendPort: 8000,
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
	s := State{DataDir: tmp, ImageTag: "latest", BackendPort: 8000, WebPort: 3000, LogLevel: "info", JWTSecret: "secret"}

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

	// Verify file permissions (0600 — owner read/write only).
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
	if s.BackendPort != 8000 {
		t.Errorf("expected default BackendPort 8000, got %d", s.BackendPort)
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

func TestStatePath(t *testing.T) {
	path := StatePath("/some/dir")
	if filepath.Base(path) != stateFileName {
		t.Errorf("StatePath base = %q, want %q", filepath.Base(path), stateFileName)
	}
}

func TestSaveLoadRoundTrip(t *testing.T) {
	tmp := t.TempDir()
	original := State{
		DataDir:     tmp,
		ImageTag:    "v2.0.0",
		BackendPort: 8080,
		WebPort:     3030,
		Sandbox:     true,
		DockerSock:  "/custom/docker.sock",
		LogLevel:    "warn",
		JWTSecret:   "super-secret-key",
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
}
