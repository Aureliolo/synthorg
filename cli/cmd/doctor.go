package cmd

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/diagnostics"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var doctorCmd = &cobra.Command{
	Use:   "doctor",
	Short: "Run diagnostics and generate a bug report",
	Long:  "Collects system info, container states, health, and logs. Saves a diagnostic file and prints a pre-filled GitHub issue URL.",
	RunE:  runDoctor,
}

func init() {
	rootCmd.AddCommand(doctorCmd)
}

func runDoctor(cmd *cobra.Command, args []string) error {
	ctx := cmd.Context()
	dir := resolveDataDir()
	out := cmd.OutOrStdout()

	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	fmt.Fprintln(out, "Collecting diagnostics...")
	report := diagnostics.Collect(ctx, state)
	text := report.FormatText()

	// Save to file.
	filename := fmt.Sprintf("synthorg-diagnostic-%s.txt", time.Now().Format("20060102-150405"))
	savePath := filepath.Join(state.DataDir, filename)
	if err := os.WriteFile(savePath, []byte(text), 0o600); err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not save diagnostic file: %v\n", err)
	} else {
		fmt.Fprintf(out, "Saved to: %s\n\n", savePath)
	}

	fmt.Fprintln(out, text)

	// Generate GitHub issue URL (exclude logs — may contain secrets).
	issueTitle := fmt.Sprintf("[CLI] Bug report — %s/%s, CLI %s", report.OS, report.Arch, report.CLIVersion)
	issueBody := fmt.Sprintf(
		"## Environment\n\nOS: %s/%s\nCLI: %s (%s)\nDocker: %s\nCompose: %s\nHealth: %s\n\n"+
			"> Full diagnostics saved to: attach the file from `synthorg doctor` output\n\n"+
			"## Steps to Reproduce\n\n1. \n\n## Expected Behavior\n\n\n## Actual Behavior\n\n",
		report.OS, report.Arch, report.CLIVersion, report.CLICommit,
		report.DockerVersion, report.ComposeVersion, report.HealthStatus,
	)

	// Truncate body if URL would exceed browser limits (~4000 chars).
	encodedBody := url.QueryEscape(issueBody)
	if len(encodedBody) > 3500 {
		issueBody = fmt.Sprintf(
			"## Environment\n\nOS: %s/%s\nCLI: %s\nDocker: %s\nHealth: %s\n\n"+
				"> Attach the full diagnostics file from `synthorg doctor` output\n",
			report.OS, report.Arch, report.CLIVersion,
			report.DockerVersion, report.HealthStatus,
		)
		encodedBody = url.QueryEscape(issueBody)
	}

	issueURL := fmt.Sprintf(
		"%s/issues/new?title=%s&labels=type%%3Abug&body=%s",
		version.RepoURL,
		url.QueryEscape(issueTitle),
		encodedBody,
	)

	fmt.Fprintf(out, "File a bug report:\n  %s\n", issueURL)

	return nil
}
