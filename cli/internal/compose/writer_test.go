package compose

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// TestWriteNATSConfig locks down the helper that keeps nats.conf in
// sync with the compose template's `configs.nats-config` reference.
// The compose file ALWAYS expects nats.conf next to it when distributed
// bus mode is on; if a future caller forgets to invoke this helper,
// NATS crash-loops on startup with `open nats.conf: no such file or
// directory`. These cases are the safety net.
func TestWriteNATSConfig(t *testing.T) {
	t.Run("writes file when bus is nats", func(t *testing.T) {
		safeDir := mustAbsDir(t, t.TempDir())
		if err := WriteNATSConfig("nats", safeDir); err != nil {
			t.Fatalf("WriteNATSConfig: %v", err)
		}
		got, err := os.ReadFile(filepath.Join(safeDir, NATSConfigFilename))
		if err != nil {
			t.Fatalf("read written file: %v", err)
		}
		if string(got) != NATSConfig() {
			t.Errorf("file content does not match canonical NATSConfigContent")
		}
	})

	t.Run("removes stale file when bus is internal", func(t *testing.T) {
		safeDir := mustAbsDir(t, t.TempDir())
		stalePath := filepath.Join(safeDir, NATSConfigFilename)
		if err := os.WriteFile(stalePath, []byte("stale contents"), 0o600); err != nil {
			t.Fatalf("seed stale: %v", err)
		}
		if err := WriteNATSConfig("internal", safeDir); err != nil {
			t.Fatalf("WriteNATSConfig: %v", err)
		}
		if _, err := os.Stat(stalePath); !os.IsNotExist(err) {
			t.Errorf("stale nats.conf should have been removed; stat err=%v", err)
		}
	})

	t.Run("noop when bus is internal and no file exists", func(t *testing.T) {
		safeDir := mustAbsDir(t, t.TempDir())
		if err := WriteNATSConfig("internal", safeDir); err != nil {
			t.Errorf("WriteNATSConfig should not error when nothing to remove: %v", err)
		}
	})

	t.Run("rejects non-absolute safeDir", func(t *testing.T) {
		err := WriteNATSConfig("nats", "relative/path")
		if err == nil {
			t.Fatalf("expected error for non-absolute safeDir, got nil")
		}
		// SecurePath raises "path must be absolute" for a relative input;
		// we only assert on "absolute" so the check stays robust if the
		// upstream wording ever changes.
		if !strings.Contains(err.Error(), "absolute") {
			t.Errorf("expected absolute-path error, got: %v", err)
		}
	})

	t.Run("rejects unclean safeDir with trailing separator", func(t *testing.T) {
		base := mustAbsDir(t, t.TempDir())
		// filepath.Clean strips the trailing "." element; the
		// "sanitised != safeDir" guard trips on that difference to
		// fail-fast on a non-normalised input.
		dirty := base + string(filepath.Separator) + "."
		err := WriteNATSConfig("nats", dirty)
		if err == nil {
			t.Fatalf("expected error for unclean safeDir %q, got nil", dirty)
		}
		if !strings.Contains(err.Error(), "not canonical") {
			t.Errorf("expected not-canonical error, got: %v", err)
		}
	})
}

// TestWriteNATSConfig_AtomicRewrite exercises the temp-file + rename
// path and verifies the resulting file has the canonical content and
// (on Unix) the exact 0o600 mask. Guards against a future refactor
// that drops the rename and writes in place again.
func TestWriteNATSConfig_AtomicRewrite(t *testing.T) {
	safeDir := mustAbsDir(t, t.TempDir())
	// Seed an existing file so the write path exercises the rename-over
	// case, not a fresh-create shortcut.
	existing := filepath.Join(safeDir, NATSConfigFilename)
	if err := os.WriteFile(existing, []byte("pre-existing"), 0o600); err != nil {
		t.Fatalf("seed existing nats.conf: %v", err)
	}

	if err := WriteNATSConfig("nats", safeDir); err != nil {
		t.Fatalf("WriteNATSConfig: %v", err)
	}

	info, err := os.Stat(existing)
	if err != nil {
		t.Fatalf("stat nats.conf: %v", err)
	}
	if runtime.GOOS == "windows" {
		if info.Mode().Perm()&0o600 != 0o600 {
			t.Errorf("nats.conf permission mask = %v, want at least owner rw", info.Mode().Perm())
		}
	} else if info.Mode().Perm() != 0o600 {
		t.Errorf("nats.conf permission mask = %v, want exactly 0o600", info.Mode().Perm())
	}
	got, err := os.ReadFile(existing)
	if err != nil {
		t.Fatalf("read nats.conf: %v", err)
	}
	if string(got) != NATSConfig() {
		t.Errorf("nats.conf content mismatch after atomic rewrite")
	}

	// Temp sibling must be gone after the rename; no .tmp leftovers.
	tmp := existing + ".tmp"
	if _, err := os.Stat(tmp); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("temp sibling %s should be cleaned up; stat err=%v", tmp, err)
	}
}

// TestWriteComposeAndNATS locks down the unified compose + nats.conf
// writer. Every regeneration path (init, start digest-pin, config set,
// update) funnels through this helper so the on-disk pair stays
// consistent across the BusBackend transition.
func TestWriteComposeAndNATS(t *testing.T) {
	t.Run("nats branch writes compose and nats.conf together", func(t *testing.T) {
		safeDir := mustAbsDir(t, t.TempDir())
		if err := WriteComposeAndNATS("compose.yml", []byte("services: {}\n"), "nats", safeDir); err != nil {
			t.Fatalf("WriteComposeAndNATS: %v", err)
		}
		if _, err := os.Stat(filepath.Join(safeDir, "compose.yml")); err != nil {
			t.Errorf("compose.yml should exist: %v", err)
		}
		got, err := os.ReadFile(filepath.Join(safeDir, NATSConfigFilename))
		if err != nil {
			t.Fatalf("nats.conf should exist: %v", err)
		}
		if string(got) != NATSConfig() {
			t.Errorf("nats.conf content mismatch")
		}
	})

	t.Run("internal branch writes compose and cleans stale nats.conf", func(t *testing.T) {
		safeDir := mustAbsDir(t, t.TempDir())
		stale := filepath.Join(safeDir, NATSConfigFilename)
		if err := os.WriteFile(stale, []byte("stale"), 0o600); err != nil {
			t.Fatalf("seed stale nats.conf: %v", err)
		}
		if err := WriteComposeAndNATS("compose.yml", []byte("services: {}\n"), "internal", safeDir); err != nil {
			t.Fatalf("WriteComposeAndNATS: %v", err)
		}
		if _, err := os.Stat(filepath.Join(safeDir, "compose.yml")); err != nil {
			t.Errorf("compose.yml should exist: %v", err)
		}
		if _, err := os.Stat(stale); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("stale nats.conf should be gone; stat err=%v", err)
		}
	})
}

// TestAtomicWriteFile verifies the exported helper writes the payload,
// sets 0o600, and cleans up the temp sibling on success.
func TestAtomicWriteFile(t *testing.T) {
	dir := mustAbsDir(t, t.TempDir())
	if err := AtomicWriteFile(dir, "compose.yml", []byte("services: {}\n")); err != nil {
		t.Fatalf("AtomicWriteFile: %v", err)
	}
	target := filepath.Join(dir, "compose.yml")
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("read target: %v", err)
	}
	if string(got) != "services: {}\n" {
		t.Errorf("target content mismatch: %q", got)
	}
	info, err := os.Stat(target)
	if err != nil {
		t.Fatalf("stat target: %v", err)
	}
	if runtime.GOOS == "windows" {
		if info.Mode().Perm()&0o600 != 0o600 {
			t.Errorf("target mask = %v, want at least owner rw", info.Mode().Perm())
		}
	} else if info.Mode().Perm() != 0o600 {
		t.Errorf("target mask = %v, want exactly 0o600", info.Mode().Perm())
	}
	// No leftover .tmp files in the directory.
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("readdir: %v", err)
	}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".tmp") {
			t.Errorf("stray temp file after atomic rename: %s", e.Name())
		}
	}
}

// mustAbsDir resolves a tempdir to its canonical absolute form so
// SecurePath's "clean + absolute" check accepts it on macOS where
// /var -> /private/var and on Windows where 8.3 paths can surface.
func mustAbsDir(t *testing.T, p string) string {
	t.Helper()
	abs, err := filepath.Abs(p)
	if err != nil {
		t.Fatalf("filepath.Abs(%q): %v", p, err)
	}
	return abs
}
