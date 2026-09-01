package config

import (
	"errors"
	"os"
	"testing"
)

func TestPinnedKeysReportsEveryKeyTheFileNames(t *testing.T) {
	dir := t.TempDir()
	state := DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := Save(state); err != nil {
		t.Fatal(err)
	}

	pinned, err := PinnedKeys(dir)
	if err != nil {
		t.Fatalf("PinnedKeys: %v", err)
	}

	// Serialised unconditionally, so a default Save pins them.
	for _, key := range []string{"auto_start_after_wipe", "auto_update_cli", "sandbox", "backend_port", "log_level", "image_tag"} {
		if !pinned[key] {
			t.Errorf("%s: expected pinned, file names it", key)
		}
	}
	// Omitted when empty, so a default Save leaves them absent.
	for _, key := range []string{"color", "hints", "output", "timestamps", "changelog_view", "registry_host"} {
		if pinned[key] {
			t.Errorf("%s: expected unpinned, file omits it", key)
		}
	}
}

// A value set to the same string as the compiled-in default is still
// pinned: presence in the file is the question, not the value.
func TestPinnedKeysSeesAKeyHoldingItsDefaultValue(t *testing.T) {
	dir := t.TempDir()
	state := DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.Color = state.ColorOrDefault()
	if err := Save(state); err != nil {
		t.Fatal(err)
	}

	pinned, err := PinnedKeys(dir)
	if err != nil {
		t.Fatalf("PinnedKeys: %v", err)
	}
	if !pinned["color"] {
		t.Error("color: expected pinned after being written at its default value")
	}
}

func TestPinnedKeysOnAbsentFilePinsNothing(t *testing.T) {
	pinned, err := PinnedKeys(t.TempDir())
	if err != nil {
		t.Fatalf("PinnedKeys: %v", err)
	}
	if len(pinned) != 0 {
		t.Errorf("expected an empty set for an uninitialised data dir, got %d keys", len(pinned))
	}
}

func TestPinnedKeysRejectsUnparseableFile(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(StatePath(dir), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}

	_, err := PinnedKeys(dir)
	if !errors.Is(err, ErrParsing) {
		t.Errorf("error = %v, want one wrapping ErrParsing", err)
	}
}

func TestPinnedKeysRejectsRelativeDataDir(t *testing.T) {
	if _, err := PinnedKeys("relative/dir"); err == nil {
		t.Error("expected a relative data dir to be rejected")
	}
}
