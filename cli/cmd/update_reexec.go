package cmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"
)

// ChildExitError and ChildExitCode are defined in exitcodes.go.

// reexecUpdate spawns the new binary with the same arguments so the rest
// of the update (compose refresh, image pull) uses the new embedded template.
// The CLI update step already ran, so the new binary will see "up to date"
// and proceed directly to compose + images.
//
// Arguments are reconstructed from known flag values rather than forwarding
// raw os.Args to avoid silently propagating unexpected flags.
//
// Returns a *ChildExitError if the child exits non-zero, so the caller
// can propagate the exit code rather than printing a generic error.
func reexecUpdate(cmd *cobra.Command, recovered bool) error {
	_, _ = fmt.Fprintln(cmd.OutOrStdout(), "Re-launching updated CLI to continue...")
	execPath, err := resolveCurrentExecutable(cmd)
	if err != nil {
		return err
	}
	c := exec.CommandContext(cmd.Context(), execPath, buildReexecArgs(cmd, recovered)...) //nolint:gosec // G204: execPath is the CLI's own resolved binary, args reconstructed from known flags (not raw os.Args)
	c.Stdin = os.Stdin
	c.Stdout = cmd.OutOrStdout()
	c.Stderr = cmd.ErrOrStderr()
	if runErr := c.Run(); runErr != nil {
		// Preserve the child's exit code so the parent can propagate it.
		if exitErr, ok := errors.AsType[*exec.ExitError](runErr); ok {
			return &ChildExitError{Code: normalizeChildExitCode(exitErr.ExitCode())}
		}
		return fmt.Errorf("re-launching updated CLI: %w", runErr)
	}
	return nil
}

// normalizeChildExitCode maps a child's reported exit code onto one the CLI
// actually defines.
//
// A signal-terminated child never carried an exit status, and os/exec says
// so by reporting -1. Propagating that verbatim would reach os.Exit, which
// takes the low byte, so the shell would report 255: a code this CLI does
// not document and no caller can key on. ExitRuntime is the honest answer,
// because a child killed mid-update did fail at runtime. A genuine status
// from the child is passed through untouched.
func normalizeChildExitCode(code int) int {
	if code < 0 {
		return ExitRuntime
	}
	return code
}

// resolveCurrentExecutable returns the absolute, symlink-resolved path
// to the running binary. Failure to resolve symlinks is non-fatal and
// produces a warning (selfupdate.Replace writes to the resolved path,
// so a mismatch surfaces as a stale-binary re-exec).
func resolveCurrentExecutable(cmd *cobra.Command) (string, error) {
	execPath, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("finding executable path: %w", err)
	}
	resolved, resolveErr := filepath.EvalSymlinks(execPath)
	if resolveErr != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not resolve executable symlink: %v\n", resolveErr)
		return execPath, nil
	}
	return resolved, nil
}

// buildReexecArgs reconstructs the argv for the re-exec'd child from
// the known flag set. Forwarding os.Args would silently propagate
// unexpected flags; rebuilding from typed values keeps the contract
// explicit.
func buildReexecArgs(cmd *cobra.Command, recovered bool) []string {
	reArgs := []string{"update", "--skip-cli-update"}
	if recovered {
		// Carry the parent's installation-health verdict so the child
		// forces the image pull without re-running the interactive
		// corruption check.
		reArgs = append(reArgs, "--health-recovered")
	}
	if flagDataDir != "" {
		reArgs = append(reArgs, "--data-dir", flagDataDir)
	}
	if flagSkipVerify {
		reArgs = append(reArgs, "--skip-verify")
		_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "Warning: --skip-verify is being carried forward to the re-launched CLI.")
	}
	if flagQuiet {
		reArgs = append(reArgs, "--quiet")
	}
	for range flagVerbose {
		reArgs = append(reArgs, "-v")
	}
	reArgs = appendBoolFlags(reArgs, []boolFlag{
		{"--no-color", flagNoColor},
		{"--plain", flagPlain},
		{"--json", flagJSON},
		{"--yes", flagYes},
		{"--no-restart", updateNoRestart},
		{"--images-only", updateImagesOnly},
		{"--cli-only", updateCLIOnly},
	})
	if cmd.Flags().Changed("timeout") {
		reArgs = append(reArgs, "--timeout", updateTimeout)
	}
	if cmd.Flags().Changed("verify-timeout") {
		reArgs = append(reArgs, "--verify-timeout", updateVerifyTimeout)
	}
	return reArgs
}

type boolFlag struct {
	name string
	set  bool
}

// appendBoolFlags appends every flag whose set field is true. Keeps
// buildReexecArgs flat instead of carrying a long if-chain.
func appendBoolFlags(args []string, flags []boolFlag) []string {
	for _, f := range flags {
		if f.set {
			args = append(args, f.name)
		}
	}
	return args
}
