package cmd

import (
	"bytes"
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// The cmd test suite shares a single package-level rootCmd. Cobra's
// Execute() mutates that singleton: it writes parsed flags into the
// package-level flag variables (flagDataDir, --confirm, --sort, ...),
// marks those flags Changed (which short-circuits MarkFlagRequired on the
// next call), and retains the SetOut/SetErr/SetArgs writers. Without a
// reset, the next test inherits stale I/O writers, a --data-dir pointing
// at a deleted t.TempDir(), and "already supplied" required flags.
//
// sandboxRootCmd is the single canonical guard against that bleed-through.
// Every test that drives rootCmd must go through it. Because rootCmd is a
// global, such tests must NOT call t.Parallel() -- parallel mutation of its
// writers and flag bindings would race.

// flagSnapshot records a flag's value and Changed bit for later restoration.
type flagSnapshot struct {
	flag    *pflag.Flag
	value   string
	changed bool
}

// snapshotFlags records every flag's current value + Changed bit across cmd
// and all descendants, de-duplicating the inherited persistent flags that
// appear on more than one command. The CLI uses only scalar flags
// (bool/int/string/duration/count), all of which round-trip through
// Value.String() -> Value.Set(), so the snapshot fully captures restorable
// state without per-flag bookkeeping.
func snapshotFlags(cmd *cobra.Command) []flagSnapshot {
	seen := make(map[*pflag.Flag]bool)
	var snaps []flagSnapshot
	record := func(f *pflag.Flag) {
		if seen[f] {
			return
		}
		seen[f] = true
		snaps = append(snaps, flagSnapshot{flag: f, value: f.Value.String(), changed: f.Changed})
	}
	var visit func(c *cobra.Command)
	visit = func(c *cobra.Command) {
		c.Flags().VisitAll(record)
		for _, sub := range c.Commands() {
			visit(sub)
		}
	}
	visit(cmd)
	return snaps
}

// restoreFlags rewinds every snapshotted flag to its captured value and
// Changed bit. The value is only re-Set when it actually drifted, so a flag
// the test never touched is left untouched; the Changed bit is always
// restored so MarkFlagRequired keeps firing for later tests.
func restoreFlags(snaps []flagSnapshot) {
	for _, s := range snaps {
		if s.flag.Value.String() != s.value {
			_ = s.flag.Value.Set(s.value)
		}
		s.flag.Changed = s.changed
	}
}

// sandboxRootCmd snapshots rootCmd's writers and the value+Changed state of
// every flag in the command tree, registers a t.Cleanup that restores them,
// and returns fresh stdout/stderr buffers plus a throwaway temp data dir the
// caller may point --data-dir at (so a developer's real config.json cannot
// poison the test). Callers that drive a specific --data-dir pass their own
// directory and ignore the returned one.
func sandboxRootCmd(t *testing.T) (stdout, stderr *bytes.Buffer, dataDir string) {
	t.Helper()
	root := rootCmd
	prevOut, prevErr := root.OutOrStdout(), root.ErrOrStderr()
	snaps := snapshotFlags(root)

	t.Cleanup(func() {
		root.SetOut(prevOut)
		root.SetErr(prevErr)
		root.SetArgs(nil)
		restoreFlags(snaps)
	})

	stdout, stderr = &bytes.Buffer{}, &bytes.Buffer{}
	root.SetOut(stdout)
	root.SetErr(stderr)
	dataDir = t.TempDir()
	return stdout, stderr, dataDir
}
