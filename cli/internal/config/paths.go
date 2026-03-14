// Package config handles CLI configuration, data directory resolution, and
// persisted state.
package config

import (
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
		home = "." // fallback to current directory
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

// EnsureDir creates the directory (and parents) if it does not exist.
func EnsureDir(path string) error {
	return os.MkdirAll(path, 0o700)
}
