package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const stateFileName = "config.json"

// State is the persisted CLI configuration written by `synthorg init`.
type State struct {
	DataDir     string `json:"data_dir"`
	ImageTag    string `json:"image_tag"`
	BackendPort int    `json:"backend_port"`
	WebPort     int    `json:"web_port"`
	Sandbox     bool   `json:"sandbox"`
	DockerSock  string `json:"docker_sock,omitempty"`
	LogLevel    string `json:"log_level"`
	JWTSecret   string `json:"jwt_secret,omitempty"`
}

// DefaultState returns a State with sensible defaults.
func DefaultState() State {
	return State{
		DataDir:     DataDir(),
		ImageTag:    "latest",
		BackendPort: 8000,
		WebPort:     3000,
		LogLevel:    "info",
	}
}

// StatePath returns the path to the config file inside the data directory.
func StatePath(dataDir string) string {
	return filepath.Join(dataDir, stateFileName)
}

// Load reads State from disk. Returns a default state with the given dataDir
// if the file does not exist (so --data-dir is respected on bootstrap).
func Load(dataDir string) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	path := StatePath(safeDir)
	data, err := os.ReadFile(path) //nolint:gosec // path validated by SecurePath
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			defaults := DefaultState()
			defaults.DataDir = safeDir
			return defaults, nil
		}
		return State{}, err
	}
	// Unmarshal onto defaults so missing fields retain default values.
	s := DefaultState()
	if err := json.Unmarshal(data, &s); err != nil {
		return State{}, err
	}
	// Canonicalize and validate DataDir.
	if s.DataDir != "" {
		safeLoaded, err := SecurePath(s.DataDir)
		if err != nil {
			return State{}, fmt.Errorf("data_dir: %w", err)
		}
		s.DataDir = safeLoaded
	}
	return s, nil
}

// Save writes State to disk as indented JSON.
// DataDir is normalized to the SecurePath-cleaned form before persisting.
func Save(s State) error {
	safeDir, err := SecurePath(s.DataDir)
	if err != nil {
		return err
	}
	s.DataDir = safeDir // persist the canonical path
	if err := os.MkdirAll(safeDir, 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(StatePath(safeDir), data, 0o600) //nolint:gosec // path validated by SecurePath
}
