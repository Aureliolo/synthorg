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

// dataSubDir is the leaf directory the default data dir lives in, under the
// per-platform app tree (e.g. %LOCALAPPDATA%\synthorg\data). Keeping data in
// its own subdir means the installed binary (which sits under the sibling
// `bin` tree) is never inside the wipe/uninstall target, so a `rm -r
// <data-dir>` can never delete the running CLI. Only the platform DEFAULT
// carries this subdir; an explicit --data-dir / SYNTHORG_DATA_DIR override is
// honoured verbatim.
const dataSubDir = "data"

// DataDir returns the default data directory for the current platform:
//   - Linux:   $XDG_DATA_HOME/synthorg/data or ~/.local/share/synthorg/data
//   - macOS:   ~/Library/Application Support/synthorg/data
//   - Windows: %LOCALAPPDATA%\synthorg\data
//
// The binary is installed separately (Windows: %LOCALAPPDATA%\synthorg\bin;
// Unix: /usr/local/bin) so it is never inside this directory.
func DataDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		// Fallback to absolute CWD so SecurePath's absolute-path check passes.
		if cwd, cwdErr := os.Getwd(); cwdErr == nil {
			home = cwd
		} else if runtime.GOOS == "windows" {
			home = os.TempDir() // last resort on Windows
		} else {
			home = "/" // last resort on Unix
		}
	}
	return dataDirForOS(runtime.GOOS, home, os.Getenv("LOCALAPPDATA"), os.Getenv("XDG_DATA_HOME"))
}

// dataDirForOS is the testable core of DataDir. Every branch nests the data
// under a `data` subdir so the default data dir is a sibling of the install
// `bin` tree rather than its parent (see dataSubDir).
func dataDirForOS(goos, home, localAppData, xdgDataHome string) string {
	switch goos {
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", appDirName, dataSubDir)
	case "windows":
		if localAppData != "" {
			return filepath.Join(localAppData, appDirName, dataSubDir)
		}
		return filepath.Join(home, "AppData", "Local", appDirName, dataSubDir)
	default: // linux and others
		if xdgDataHome != "" {
			return filepath.Join(xdgDataHome, appDirName, dataSubDir)
		}
		return filepath.Join(home, ".local", "share", appDirName, dataSubDir)
	}
}

// SecurePath validates that a path is absolute and returns a cleaned version.
// This satisfies static analysis (CodeQL go/path-injection) by ensuring
// environment-variable-derived paths are sanitized before filesystem use.
//
// Security note: this validates path format only. The CLI trusts user-provided
// paths (--data-dir, config file) by design -- the user controls their own
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
