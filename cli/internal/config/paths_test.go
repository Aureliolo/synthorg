package config

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestDataDirNonEmpty(t *testing.T) {
	dir := DataDir()
	if dir == "" {
		t.Fatal("DataDir returned empty string")
	}
	if filepath.Base(dir) != appDirName {
		t.Errorf("DataDir base = %q, want %q", filepath.Base(dir), appDirName)
	}
}

func TestDataDirForOS_Darwin(t *testing.T) {
	got := dataDirForOS("darwin", "/Users/test", "", "")
	want := filepath.Join("/Users/test", "Library", "Application Support", appDirName)
	if got != want {
		t.Errorf("darwin: got %q, want %q", got, want)
	}
}

func TestDataDirForOS_WindowsWithLocalAppData(t *testing.T) {
	got := dataDirForOS("windows", `C:\Users\test`, `C:\Users\test\AppData\Local`, "")
	want := filepath.Join(`C:\Users\test\AppData\Local`, appDirName)
	if got != want {
		t.Errorf("windows (LOCALAPPDATA): got %q, want %q", got, want)
	}
}

func TestDataDirForOS_WindowsFallback(t *testing.T) {
	got := dataDirForOS("windows", `C:\Users\test`, "", "")
	want := filepath.Join(`C:\Users\test`, "AppData", "Local", appDirName)
	if got != want {
		t.Errorf("windows (fallback): got %q, want %q", got, want)
	}
}

func TestDataDirForOS_LinuxWithXDG(t *testing.T) {
	got := dataDirForOS("linux", "/home/test", "", "/custom/data")
	want := filepath.Join("/custom/data", appDirName)
	if got != want {
		t.Errorf("linux (XDG): got %q, want %q", got, want)
	}
}

func TestDataDirForOS_LinuxFallback(t *testing.T) {
	got := dataDirForOS("linux", "/home/test", "", "")
	want := filepath.Join("/home/test", ".local", "share", appDirName)
	if got != want {
		t.Errorf("linux (fallback): got %q, want %q", got, want)
	}
}

func TestDataDirForOS_FreeBSD(t *testing.T) {
	// Unknown OS should use the linux/default path.
	got := dataDirForOS("freebsd", "/home/test", "", "")
	want := filepath.Join("/home/test", ".local", "share", appDirName)
	if got != want {
		t.Errorf("freebsd: got %q, want %q", got, want)
	}
}

func absTestPath(parts ...string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(append([]string{`C:\`}, parts...)...)
	}
	return filepath.Join(append([]string{"/"}, parts...)...)
}

func TestSecurePath_Absolute(t *testing.T) {
	input := absTestPath("usr", "local", "bin")
	got, err := SecurePath(input)
	if err != nil {
		t.Fatalf("SecurePath(%q): %v", input, err)
	}
	if got != filepath.Clean(input) {
		t.Errorf("got %q, want cleaned absolute path", got)
	}
}

func TestSecurePath_Relative(t *testing.T) {
	_, err := SecurePath("relative/path")
	if err == nil {
		t.Fatal("expected error for relative path")
	}
}

func TestSecurePath_Empty(t *testing.T) {
	_, err := SecurePath("")
	if err == nil {
		t.Fatal("expected error for empty path")
	}
}

func TestSecurePath_Cleans(t *testing.T) {
	input := absTestPath("usr", "local", "..", "bin")
	got, err := SecurePath(input)
	if err != nil {
		t.Fatalf("SecurePath(%q): %v", input, err)
	}
	want := filepath.Clean(input)
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSecurePath_DotDotRelative(t *testing.T) {
	_, err := SecurePath("../etc/passwd")
	if err == nil {
		t.Fatal("expected error for relative traversal path")
	}
}

func TestEnsureDir(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "nested", "dir")
	if err := EnsureDir(target); err != nil {
		t.Fatalf("EnsureDir: %v", err)
	}
	info, err := os.Stat(target)
	if err != nil {
		t.Fatalf("Stat after EnsureDir: %v", err)
	}
	if !info.IsDir() {
		t.Error("expected directory")
	}
}

func TestEnsureDirIdempotent(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "existing")
	if err := os.MkdirAll(target, 0o700); err != nil {
		t.Fatalf("MkdirAll setup: %v", err)
	}

	if err := EnsureDir(target); err != nil {
		t.Fatalf("EnsureDir on existing dir: %v", err)
	}
}
