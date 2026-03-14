// Package config handles CLI configuration, data directory resolution, and
// persisted state.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

const appDirName = "synthorg"

// DataDir returns the default data directory for the current platform:
//   - Linux:   $XDG_DATA_HOME/synthorg or ~/.local/share/synthorg
//   - macOS:   ~/Library/Application Support/synthorg
//   - Windows: %LOCALAPPDATA%\synthorg
func DataDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		// Fallback to absolute CWD so SecurePath's absolute-path check passes.
		if cwd, cwdErr := os.Getwd(); cwdErr == nil {
			home = cwd
		} else {
			home = "/" // last resort — will be valid on Unix, best-effort on Windows
		}
	}
	return dataDirForOS(runtime.GOOS, home, os.Getenv("LOCALAPPDATA"), os.Getenv("XDG_DATA_HOME"))
}

// dataDirForOS is the testable core of DataDir.
func dataDirForOS(goos, home, localAppData, xdgDataHome string) string {
	switch goos {
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", appDirName)
	case "windows":
		if localAppData != "" {
			return filepath.Join(localAppData, appDirName)
		}
		return filepath.Join(home, "AppData", "Local", appDirName)
	default: // linux and others
		if xdgDataHome != "" {
			return filepath.Join(xdgDataHome, appDirName)
		}
		return filepath.Join(home, ".local", "share", appDirName)
	}
}

// SecurePath validates that a path is absolute and returns a cleaned version.
// This satisfies static analysis (CodeQL go/path-injection) by ensuring
// environment-variable-derived paths are sanitized before filesystem use.
//
// Security note: this validates path format only. The CLI trusts user-provided
// paths (--data-dir, config file) by design — the user controls their own
// installation directory. No filesystem containment is enforced.
func SecurePath(path string) (string, error) {
	clean := filepath.Clean(path)
	if !filepath.IsAbs(clean) {
		return "", fmt.Errorf("path must be absolute, got %q", path)
	}
	return clean, nil
}

// EnsureDir creates the directory (and parents) if it does not exist.
// The path must be absolute.
func EnsureDir(path string) error {
	safe, err := SecurePath(path)
	if err != nil {
		return err
	}
	return os.MkdirAll(safe, 0o700)
}
