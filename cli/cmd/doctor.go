package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/diagnostics"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	doctorChecks string
	doctorFix    bool
)

// validDoctorChecks lists the known check names for --checks validation.
var validDoctorChecks = map[string]bool{
	"environment": true,
	"health":      true,
	"containers":  true,
	"images":      true,
	"compose":     true,
	"config":      true,
	"disk":        true,
	"errors":      true,
	"all":         true,
}

var doctorCmd = &cobra.Command{
	Use:   "doctor",
	Short: "Run diagnostics and generate a bug report",
	Long:  "Collects system info, container states, health, and logs. Saves a diagnostic file and prints a pre-filled GitHub issue URL.",
	Args:  cobra.NoArgs,
	Example: `  synthorg doctor                          # full diagnostics
  synthorg doctor --checks health,containers  # run specific checks only
  synthorg doctor --fix                    # auto-fix detected issues`,
	RunE: runDoctor,
}

func init() {
	doctorCmd.Flags().StringVar(&doctorChecks, "checks", "", "comma-separated checks to run (environment,health,containers,images,compose,config,disk,errors,all)")
	doctorCmd.Flags().BoolVar(&doctorFix, "fix", false, "auto-fix detected issues")
	doctorCmd.GroupID = "diagnostics"
	rootCmd.AddCommand(doctorCmd)
}

func validateDoctorFlags() error {
	if doctorChecks == "" {
		return nil
	}
	for _, name := range strings.Split(doctorChecks, ",") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		if !validDoctorChecks[name] {
			return fmt.Errorf("unknown check %q: valid checks are %s", name, validDoctorCheckNames())
		}
	}
	return nil
}

// validDoctorCheckNames returns a sorted, comma-separated list of valid check
// names for use in error messages. Excludes "all" (a keyword, not a check).
func validDoctorCheckNames() string {
	names := make([]string, 0, len(validDoctorChecks)-1)
	for k := range validDoctorChecks {
		if k != "all" {
			names = append(names, k)
		}
	}
	sort.Strings(names)
	return strings.Join(names, ", ")
}

// doctorCheckEnabled returns true if the named check should be rendered.
func doctorCheckEnabled(name string) bool {
	if doctorChecks == "" {
		return true // no filter = show all
	}
	for _, c := range strings.Split(doctorChecks, ",") {
		c = strings.TrimSpace(c)
		if c == "all" || c == name {
			return true
		}
	}
	return false
}

func runDoctor(cmd *cobra.Command, _ []string) error {
	if err := validateDoctorFlags(); err != nil {
		return fmt.Errorf("validating doctor flags: %w", err)
	}

	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

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
	saveDiagnosticFile(out, safeDir, report)
	_, _ = fmt.Fprintln(out.Writer())

	renderDoctorFiltered(out, report, state)
	status := printDoctorFooter(out, state, report)

	if doctorChecks != "" {
		out.HintGuidance("Run without --checks to see all diagnostic categories.")
	}

	if doctorFix {
		fixed := doctorAutoFix(ctx, cmd, out, errOut, state, report, safeDir)
		if fixed {
			out.HintGuidance("Run 'synthorg doctor' again to verify fixes.")
		}
	}

	if status != doctorHealthy && !doctorFix {
		out.HintTip("Run 'synthorg doctor --fix' to auto-fix detected issues.")
	}

	_, _ = fmt.Fprintln(out.Writer())
	out.HintNextStep("Run 'synthorg doctor report' to file a bug report")
	out.HintNextStep("Run 'synthorg logs' to view container logs")
	return nil
}

// saveDiagnosticFile writes the plain-text report to a timestamped file.
func saveDiagnosticFile(out *ui.UI, safeDir string, report diagnostics.Report) {
	filename := fmt.Sprintf("synthorg-diagnostic-%s.txt", time.Now().Format("20060102-150405"))
	savePath := filepath.Join(safeDir, filename)
	text := report.FormatText()
	if err := os.WriteFile(savePath, []byte(text), 0o600); err != nil {
		out.Warn(fmt.Sprintf("Could not save diagnostic file: %v", err))
	} else {
		out.Success(fmt.Sprintf("Saved to: %s", savePath))
	}
}

// printDoctorFooter renders links and summary below the diagnostic sections.
// Returns the overall doctor status to avoid redundant classifyDoctor calls.
func printDoctorFooter(out *ui.UI, state config.State, report diagnostics.Report) doctorStatus {
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Links")
	out.Link("Dashboard", fmt.Sprintf("http://localhost:%d", state.WebPort))
	out.Link("API docs", fmt.Sprintf("http://localhost:%d/docs/api", state.BackendPort))
	_, _ = fmt.Fprintln(out.Writer())
	return renderDoctorSummary(out, report)
}

// renderDoctorFiltered renders diagnostic sections gated by --checks filter.
func renderDoctorFiltered(out *ui.UI, report diagnostics.Report, state config.State) {
	if doctorCheckEnabled("environment") {
		renderDoctorEnvironment(out, report)
	}
	if doctorCheckEnabled("health") {
		renderDoctorHealth(out, report)
	}
	if doctorCheckEnabled("containers") {
		renderDoctorContainers(out, report)
	}
	if doctorCheckEnabled("images") {
		renderDoctorImages(out, report)
	}
	if doctorCheckEnabled("compose") {
		renderDoctorInfra(out, report)
	}
	if doctorCheckEnabled("config") {
		renderDoctorConfig(out, state)
	}
	if doctorCheckEnabled("disk") {
		renderDoctorDisk(out, report)
	}
	if doctorCheckEnabled("errors") {
		renderDoctorErrors(out, report)
	}
}

// doctorAutoFix attempts to fix detected issues. Scans all issues first,
// then executes fixes in correct order (compose first, restart once after).
// Only acts on issues matching the --checks filter. Non-fatal: prints
// results but does not return errors.
func doctorAutoFix(ctx context.Context, _ *cobra.Command, out, errOut *ui.UI, state config.State, report diagnostics.Report, safeDir string) bool {
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Auto-fix")

	status, issues := classifyDoctor(report)
	if status == doctorHealthy {
		out.Success("All systems healthy -- nothing to fix")
		return false
	}

	// Phase 1: scan issues and determine needed actions.
	var needComposeFix, needRestart bool
	var unfixable []string
	for _, issue := range issues {
		switch {
		case strings.Contains(issue, "compose.yml") && (strings.Contains(issue, "not found") || strings.Contains(issue, "invalid")):
			if doctorCheckEnabled("compose") {
				needComposeFix = true
			}
		case strings.Contains(issue, "unhealthy") || strings.Contains(issue, "exited"):
			if doctorCheckEnabled("containers") || doctorCheckEnabled("health") {
				needRestart = true
			}
		default:
			unfixable = append(unfixable, issue)
		}
	}

	if !needComposeFix && !needRestart && len(unfixable) == 0 {
		out.Success("No fixable issues in selected checks")
		return false
	}

	// Phase 2: execute fixes in correct order (compose before restart).
	if needComposeFix {
		out.Step("Regenerating compose.yml from template...")
		if fixErr := doctorFixCompose(state, safeDir); fixErr != nil {
			errOut.Error(fmt.Sprintf("Could not regenerate compose: %v", fixErr))
		} else {
			out.Success("Regenerated compose.yml from template")
		}
	}

	if needRestart {
		info, dockerErr := docker.Detect(ctx)
		if dockerErr != nil {
			errOut.Warn(fmt.Sprintf("Cannot restart containers: Docker not available (%v)", dockerErr))
		} else {
			out.Step("Restarting containers...")
			if fixErr := composeRunQuiet(ctx, info, safeDir, "restart"); fixErr != nil {
				errOut.Error(fmt.Sprintf("Restart failed: %v", fixErr))
			} else {
				out.Success("Containers restarted")
			}
		}
	}

	for _, issue := range unfixable {
		out.HintNextStep(fmt.Sprintf("No auto-fix available for: %s", issue))
	}
	return needComposeFix || needRestart
}

// doctorFixCompose regenerates compose.yml from the embedded template.
func doctorFixCompose(state config.State, safeDir string) error {
	params, err := compose.ParamsFromState(state)
	if err != nil {
		return fmt.Errorf("building compose params: %w", err)
	}
	// ParamsFromState decides DigestPins based on the resolved
	// tunables (only honoured when on the default registry).
	// Overriding here would let `doctor --fix` regenerate a
	// custom-registry compose file with stale default-registry
	// digest pins.
	generated, err := compose.Generate(params)
	if err != nil {
		return fmt.Errorf("generating compose: %w", err)
	}
	return compose.WriteComposeAndNATS("compose.yml", generated, state.BusBackend, safeDir)
}

// doctorStatus classifies the overall health of the system from a diagnostic report.
type doctorStatus int

const (
	doctorHealthy doctorStatus = iota
	doctorWarnings
	doctorErrors
)

// classifyDoctor inspects the report to determine the overall status.
func classifyDoctor(r diagnostics.Report) (doctorStatus, []string) {
	var warnings, errs []string

	// Backend health.
	switch r.HealthStatus {
	case "200":
		// ok
	case "unreachable":
		errs = append(errs, "backend unreachable")
	case "":
		// not checked
	default:
		errs = append(errs, fmt.Sprintf("backend unhealthy (HTTP %s)", r.HealthStatus))
	}

	// Container states.
	if len(r.ContainerSummary) == 0 && r.ComposeFileExists {
		warnings = append(warnings, "no containers detected")
	}
	for _, c := range r.ContainerSummary {
		switch {
		case c.Health == "unhealthy", c.State == "exited":
			status := c.Health
			if status == "" {
				status = c.State
			}
			errs = append(errs, fmt.Sprintf("%s %s", c.Name, status))
		case c.Health == "starting":
			warnings = append(warnings, fmt.Sprintf("%s still starting", c.Name))
		}
	}

	// Image availability.
	for _, img := range r.ImageStatus {
		if !strings.HasSuffix(img, ": available") {
			warnings = append(warnings, img)
		}
	}

	// Compose file.
	switch {
	case !r.ComposeFileExists:
		errs = append(errs, "compose.yml not found")
	case r.ComposeFileValid == nil:
		warnings = append(warnings, "compose.yml exists, validity not checked")
	case !*r.ComposeFileValid:
		errs = append(errs, "compose.yml is invalid")
	}

	// Port conflicts.
	for _, p := range r.PortConflicts {
		errs = append(errs, fmt.Sprintf("port conflict: %s", p))
	}

	// Explicit errors from collection.
	errs = append(errs, r.Errors...)

	if len(errs) > 0 {
		return doctorErrors, errs
	}
	if len(warnings) > 0 {
		return doctorWarnings, warnings
	}
	return doctorHealthy, nil
}

// renderDoctorSummary prints a final summary box showing overall system status.
// Returns the classification to avoid redundant classifyDoctor calls.
func renderDoctorSummary(out *ui.UI, r diagnostics.Report) doctorStatus {
	status, issues := classifyDoctor(r)

	switch status {
	case doctorHealthy:
		out.Box("Status", []string{
			fmt.Sprintf("  %s All systems healthy", ui.IconSuccess),
		})
	case doctorWarnings, doctorErrors:
		count := len(issues)
		plural := "s"
		if count == 1 {
			plural = ""
		}

		var title string
		if status == doctorWarnings {
			title = fmt.Sprintf("  %s %d warning%s detected", ui.IconWarning, count, plural)
		} else {
			title = fmt.Sprintf("  %s %d issue%s found", ui.IconError, count, plural)
		}

		lines := make([]string, 1, count+1)
		lines[0] = title
		for _, issue := range issues {
			lines = append(lines, fmt.Sprintf("    %s %s", ui.IconHint, issue))
		}
		out.Box("Status", lines)
	}
	return status
}

func renderDoctorEnvironment(out *ui.UI, r diagnostics.Report) {
	out.Section("Environment")
	out.KeyValue("OS", fmt.Sprintf("%s/%s", r.OS, r.Arch))
	out.KeyValue("CLI", fmt.Sprintf("%s (%s)", r.CLIVersion, r.CLICommit))
	out.KeyValue("Docker", r.DockerVersion)
	out.KeyValue("Compose", r.ComposeVersion)
	_, _ = fmt.Fprintln(out.Writer())
}

func renderDoctorHealth(out *ui.UI, r diagnostics.Report) {
	if r.HealthStatus == "" {
		return
	}
	switch r.HealthStatus {
	case "200":
		out.Success(fmt.Sprintf("Backend healthy (HTTP %s)", r.HealthStatus))
	case "unreachable":
		out.Error("Backend unreachable")
	default:
		out.Error(fmt.Sprintf("Backend unhealthy (HTTP %s)", r.HealthStatus))
	}
}

func renderDoctorContainers(out *ui.UI, r diagnostics.Report) {
	if len(r.ContainerSummary) == 0 {
		out.Warn("No containers detected")
		return
	}
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Containers")
	for _, c := range r.ContainerSummary {
		switch {
		case c.Health == "healthy":
			out.Success(fmt.Sprintf("%-24s healthy", c.Name))
		case c.Health == "unhealthy", c.State == "exited":
			status := c.Health
			if status == "" {
				status = c.State
			}
			out.Error(fmt.Sprintf("%-24s %s", c.Name, status))
		case c.Health != "":
			out.Warn(fmt.Sprintf("%-24s %s (%s)", c.Name, c.State, c.Health))
		default:
			out.Step(fmt.Sprintf("%-24s %s", c.Name, c.State))
		}
	}
}

func renderDoctorImages(out *ui.UI, r diagnostics.Report) {
	if len(r.ImageStatus) == 0 {
		return
	}
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Images")
	for _, img := range r.ImageStatus {
		if strings.HasSuffix(img, ": available") {
			out.Success(img)
		} else {
			out.Error(img)
		}
	}
}

func renderDoctorInfra(out *ui.UI, r diagnostics.Report) {
	_, _ = fmt.Fprintln(out.Writer())
	if r.ComposeFileExists {
		valid := "not checked"
		if r.ComposeFileValid != nil {
			if *r.ComposeFileValid {
				valid = "valid"
			} else {
				valid = "invalid"
			}
		}
		if valid == "valid" {
			out.Success(fmt.Sprintf("Compose file: exists, %s", valid))
		} else {
			out.Warn(fmt.Sprintf("Compose file: exists, %s", valid))
		}
	} else {
		out.Error("Compose file: not found")
	}
	for _, conflict := range r.PortConflicts {
		out.Error(fmt.Sprintf("Port conflict: %s", conflict))
	}
}

func renderDoctorConfig(out *ui.UI, state config.State) {
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Config")
	out.KeyValue("Data dir", state.DataDir)
	out.KeyValue("Image tag", state.ImageTag)
	out.KeyValue("Backend port", fmt.Sprintf("%d", state.BackendPort))
	out.KeyValue("Web port", fmt.Sprintf("%d", state.WebPort))
	out.KeyValue("Sandbox", fmt.Sprintf("%v", state.Sandbox))
	out.KeyValue("Persistence", state.PersistenceBackend)
	out.KeyValue("Memory", state.MemoryBackend)
	out.KeyValue("Log level", state.LogLevel)
	out.KeyValue("JWT secret", maskSecret(state.JWTSecret))
	out.KeyValue("Settings key", maskSecret(state.SettingsKey))
}

func renderDoctorDisk(out *ui.UI, r diagnostics.Report) {
	if r.DiskInfo == "" {
		return
	}
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Disk")
	// DiskInfo is a single line like "Total: 930.6 GiB  Used: 596.8 GiB  Free: 333.7 GiB  (64% used)"
	_, _ = fmt.Fprintf(out.Writer(), "  %s\n", r.DiskInfo)
}

func renderDoctorErrors(out *ui.UI, r diagnostics.Report) {
	if len(r.Errors) == 0 {
		return
	}
	_, _ = fmt.Fprintln(out.Writer())
	out.Section("Errors")
	for _, e := range r.Errors {
		out.Error(e)
	}
}
