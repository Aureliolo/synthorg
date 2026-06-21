package cmd

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/spf13/cobra"
)

// newTeardownTestCmd returns a cobra.Command whose context carries default
// GlobalOpts and whose output streams are discarded, suitable for driving
// the teardown helpers in a test.
func newTeardownTestCmd() *cobra.Command {
	c := &cobra.Command{}
	c.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Hints: "auto"}))
	c.SetOut(io.Discard)
	c.SetErr(io.Discard)
	return c
}

// TestRemoveDataDir_RemovesEverything asserts the simplified data-dir
// removal nukes the whole tree. The installed binary now lives under a
// sibling `bin` tree, never inside the data dir, so a plain RemoveAll is
// correct and there is no skip-self branch to preserve.
func TestRemoveDataDir_RemovesEverything(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte("{}"), 0o600); err != nil {
		t.Fatalf("seed config: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(dir, "logs"), 0o700); err != nil {
		t.Fatalf("seed logs: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "logs", "app.log"), []byte("x"), 0o600); err != nil {
		t.Fatalf("seed log: %v", err)
	}

	if err := removeDataDir(newTeardownTestCmd(), dir); err != nil {
		t.Fatalf("removeDataDir: %v", err)
	}
	if _, err := os.Stat(dir); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("data dir should be gone, got err=%v", err)
	}
}

// TestStopAndRemoveVolumes_NoCompose asserts uninstall's stop step skips
// cleanly (returns nil, never touching Docker) when no compose.yml exists,
// so teardown reaches data/binary removal instead of hard-failing.
func TestStopAndRemoveVolumes_NoCompose(t *testing.T) {
	dataDir := t.TempDir() // no compose.yml
	cmd := newTeardownTestCmd()
	// A zero docker.Info is never used: the no-compose guard returns first.
	if err := stopAndRemoveVolumes(cmd, docker.Info{}, dataDir, discardUI(), discardUI(), true, false); err != nil {
		t.Errorf("stopAndRemoveVolumes (no compose) = %v, want nil", err)
	}
}
