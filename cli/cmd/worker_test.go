package cmd

import (
	"strings"
	"testing"
)

// sandboxRootCmd (cli/cmd/testhelpers_test.go) guards every rootCmd-driving
// test in this package against Cobra's singleton flag/writer bleed-through.

// TestWorkerCmd_BareInvocationPrintsHelp verifies that `synthorg worker`
// with no subcommand renders the help text (exit 0) instead of silently
// succeeding without doing anything -- the behaviour the audit flagged.
func TestWorkerCmd_BareInvocationPrintsHelp(t *testing.T) {
	out, errOut, dataDir := sandboxRootCmd(t)
	rootCmd.SetArgs([]string{"--data-dir", dataDir, "worker"})

	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("Execute: %v; stderr=%q", err, errOut.String())
	}
	combined := out.String() + errOut.String()
	if !strings.Contains(combined, "Available Commands:") {
		t.Errorf("help output missing 'Available Commands:'; got:\n%s", combined)
	}
	if !strings.Contains(combined, "start") {
		t.Errorf("help output missing 'start' subcommand; got:\n%s", combined)
	}
}

// TestWorkerCmd_UnknownSubcommandRejected verifies that `synthorg worker foo`
// returns a usage error rather than silently succeeding.
func TestWorkerCmd_UnknownSubcommandRejected(t *testing.T) {
	out, errOut, dataDir := sandboxRootCmd(t)
	rootCmd.SetArgs([]string{"--data-dir", dataDir, "worker", "bogus-subcommand"})

	err := rootCmd.Execute()
	if err == nil {
		t.Fatalf("Execute with unknown subcommand: want error, got nil; stdout=%q stderr=%q", out.String(), errOut.String())
	}
	if !strings.Contains(err.Error(), "unknown command") {
		t.Errorf("error %q does not mention 'unknown command'", err.Error())
	}
}
