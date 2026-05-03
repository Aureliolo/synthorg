package cmd

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/diagnostics"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var doctorReportCmd = &cobra.Command{
	Use:   "report",
	Short: "Generate a diagnostic archive and bug report URL",
	Long:  "Collects diagnostics, saves a report file, and prints a pre-filled GitHub issue URL.",
	Example: `  synthorg doctor report           # write archive + print issue URL
  synthorg doctor report --json    # machine-readable summary`,
	RunE: runDoctorReport,
}

func init() {
	doctorCmd.AddCommand(doctorReportCmd)
}

func runDoctorReport(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	out.Step("Collecting diagnostics...")
	report := diagnostics.Collect(ctx, state)

	safeDir, err := safeStateDir(state)
	if err != nil {
		return fmt.Errorf("resolving data directory: %w", err)
	}

	// Save diagnostic report.
	filename := fmt.Sprintf("synthorg-diagnostic-%s.txt",
		time.Now().Format("20060102-150405"))
	savePath := filepath.Join(safeDir, filename)
	text := report.FormatText()
	if err := os.WriteFile(savePath, []byte(text), 0o600); err != nil {
		return fmt.Errorf("saving diagnostic file: %w", err)
	}
	out.Success(fmt.Sprintf("Diagnostic file: %s", savePath))

	// Build issue URL.
	issueURL := buildBugReportURL(report, state)

	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Bug Report")
	out.HintNextStep("1. Open the URL below in your browser")
	out.HintNextStep("2. Attach the diagnostic file to the issue")
	_, _ = fmt.Fprintln(out.Writer())
	_, _ = fmt.Fprintln(out.Writer(), issueURL)

	return nil
}

func buildBugReportURL(report diagnostics.Report, state config.State) string {
	title := fmt.Sprintf("[CLI] Bug report -- %s/%s, CLI %s",
		report.OS, report.Arch, report.CLIVersion)

	// Build container summary lines.
	var containers string
	for _, c := range report.ContainerSummary {
		health := c.Health
		if health == "" {
			health = c.State
		}
		containers += fmt.Sprintf("| %s | %s | %s |\n", c.Name, c.State, health)
	}

	body := fmt.Sprintf(
		"## Environment\n\n"+
			"| Field | Value |\n"+
			"|-------|-------|\n"+
			"| OS | %s/%s |\n"+
			"| CLI | %s (%s) |\n"+
			"| Docker | %s |\n"+
			"| Compose | %s |\n"+
			"| Health | %s |\n"+
			"| Persistence | %s |\n"+
			"| Memory | %s |\n"+
			"| Image tag | %s |\n"+
			"| Sandbox | %v |\n\n",
		report.OS, report.Arch,
		report.CLIVersion, report.CLICommit,
		report.DockerVersion, report.ComposeVersion,
		report.HealthStatus,
		state.PersistenceBackend, state.MemoryBackend,
		state.ImageTag, state.Sandbox,
	)

	if containers != "" {
		body += "## Containers\n\n" +
			"| Name | State | Health |\n" +
			"|------|-------|--------|\n" +
			containers + "\n"
	}

	body += "> Attach the diagnostic file from `synthorg doctor report`\n\n" +
		"## Steps to Reproduce\n\n1. \n\n" +
		"## Expected Behavior\n\n\n\n" +
		"## Actual Behavior\n\n"

	// Truncate body if URL would exceed browser limits (~8000 chars).
	encodedBody := url.QueryEscape(body)
	if len(encodedBody) > 6000 {
		body = fmt.Sprintf(
			"## Environment\n\nOS: %s/%s\nCLI: %s\nDocker: %s\nHealth: %s\n\n"+
				"> Attach the diagnostic file from `synthorg doctor report`\n",
			report.OS, report.Arch, report.CLIVersion,
			report.DockerVersion, report.HealthStatus,
		)
		encodedBody = url.QueryEscape(body)
	}

	return fmt.Sprintf("%s/issues/new?title=%s&labels=type%%3Abug&body=%s",
		version.RepoURL,
		url.QueryEscape(title),
		encodedBody,
	)
}
