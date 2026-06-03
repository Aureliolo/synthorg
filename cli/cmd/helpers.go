package cmd

import (
	"context"
	"fmt"
	"net/url"
	"os/exec"
	"runtime"
)

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
