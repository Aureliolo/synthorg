package cmd

import (
	"context"
	"fmt"
	"net/url"
	"os/exec"
	"runtime"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// loadForInspection reads config for a command that must keep working on
// a config the strict loader refuses. `doctor` and the `config` inspection
// subcommands are registered as recovery commands precisely so a broken
// file cannot lock the operator out of the tools that diagnose and repair
// it; loading strictly in the command body would undo that, because the
// recovery fallback in setupGlobalOpts only covers the tunables load.
//
// The advisory error is surfaced as a warning, never returned: these
// commands report on a broken config, they do not run the stack on one.
func loadForInspection(dataDir string, errOut *ui.UI) config.State {
	state, advisory := config.LoadTolerant(dataDir)
	if advisory != nil {
		errOut.Warn(fmt.Sprintf(
			"Config could not be fully resolved: %v. Reporting on what could be read.",
			advisory,
		))
	}
	return state
}

// boolToYesNo converts a bool to "yes"/"no" for display.
func boolToYesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}

// openBrowser opens a URL in the default browser. Only localhost HTTP(S)
// URLs are permitted to prevent arbitrary command execution.
func openBrowser(ctx context.Context, rawURL string) error {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("invalid URL %q: %w", rawURL, err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("refusing to open URL with scheme %q -- only http and https are allowed", parsed.Scheme)
	}
	host := parsed.Hostname()
	if host != "localhost" && host != "127.0.0.1" {
		return fmt.Errorf("refusing to open URL with host %q -- only localhost and 127.0.0.1 are allowed", host)
	}

	// Use the re-serialized URL, not the raw input string, to ensure
	// only the normalized, validated URL is passed to the OS launcher.
	normalizedURL := parsed.String()

	var c *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		c = exec.CommandContext(ctx, "rundll32", "url.dll,FileProtocolHandler", normalizedURL) //nolint:gosec // G204: launcher is a constant; only the scheme+host-validated, re-serialised localhost URL is variable
	case "darwin":
		c = exec.CommandContext(ctx, "open", normalizedURL) //nolint:gosec // G204: launcher is a constant; only the scheme+host-validated, re-serialised localhost URL is variable
	default:
		c = exec.CommandContext(ctx, "xdg-open", normalizedURL) //nolint:gosec // G204: launcher is a constant; only the scheme+host-validated, re-serialised localhost URL is variable
	}
	if err := c.Start(); err != nil {
		return fmt.Errorf("starting browser: %w", err)
	}
	go func() { _ = c.Wait() }() // reap child, prevent zombie
	return nil
}
