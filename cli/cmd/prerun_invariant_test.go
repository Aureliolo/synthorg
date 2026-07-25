package cmd

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// TestNoSubcommandDefinesItsOwnPreRun enforces the invariant the root
// command's comment states.
//
// Cobra does NOT chain PersistentPreRunE: it walks up from the executed
// command and runs the FIRST one it finds. A subcommand that defines its
// own therefore silently replaces the root's, and setupGlobalOpts never
// runs -- so GlobalOpts falls back to zero values. Every tunable reverts to
// a zero timeout, --data-dir is ignored, and the recovery-command fallback
// that keeps `doctor` usable on a broken config disappears. Nothing fails
// loudly; the command simply operates on the wrong configuration.
//
// A subcommand that genuinely needs a pre-run hook must call
// setupGlobalOpts(cmd) itself as its first statement. This test lists the
// commands that do, so adding a hook is a deliberate act with a visible
// obligation rather than an invisible regression.
func TestNoSubcommandDefinesItsOwnPreRun(t *testing.T) {
	// Commands whose pre-run hook is known to call setupGlobalOpts itself.
	// Empty today: nothing needs one. An entry here is a promise that the
	// hook's first action is setupGlobalOpts(cmd).
	chainsGlobalOptsItself := map[string]bool{}

	var offenders []string
	var walk func(*cobra.Command)
	walk = func(cmd *cobra.Command) {
		for _, sub := range cmd.Commands() {
			path := sub.CommandPath()
			if !chainsGlobalOptsItself[path] {
				if sub.PersistentPreRunE != nil || sub.PersistentPreRun != nil {
					offenders = append(offenders, path+" (PersistentPreRun)")
				}
				if sub.PreRunE != nil || sub.PreRun != nil {
					offenders = append(offenders, path+" (PreRun)")
				}
			}
			walk(sub)
		}
	}
	walk(rootCmd)

	if len(offenders) != 0 {
		t.Errorf(
			"these subcommands define a pre-run hook, which SUPPRESSES the root's "+
				"PersistentPreRunE and leaves GlobalOpts at its zero value "+
				"(no --data-dir, no tunables, no recovery fallback):\n  %s\n"+
				"Either remove the hook, or call setupGlobalOpts(cmd) as its first "+
				"statement and add the command to chainsGlobalOptsItself above.",
			strings.Join(offenders, "\n  "),
		)
	}
}

// TestRootPreRunIsWired is the other half: the invariant above is only
// worth anything while the root actually defines the hook every subcommand
// is relying on.
func TestRootPreRunIsWired(t *testing.T) {
	if rootCmd.PersistentPreRunE == nil {
		t.Fatal("rootCmd.PersistentPreRunE is nil: no command would resolve GlobalOpts")
	}
}
