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
	// Status, summary, and auto-fix all see the same --checks-filtered
	// report so they only ever surface findings from the categories the
	// operator actually requested. Without the filter, a
	// `synthorg doctor --checks=compose` run could land OK/DEGRADED
	// verdicts driven by health/containers/etc. findings the operator
	// never asked about.
	filteredReport := filterReportByDoctorChecks(report)
	status := printDoctorFooter(out, state, filteredReport)

	if doctorChecks != "" {
		out.HintGuidance("Run without --checks to see all diagnostic categories.")
	}

	if doctorFix {
		fixed := doctorAutoFix(ctx, cmd, out, errOut, state, filteredReport, safeDir)
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

// filterReportByDoctorChecks returns a copy of report with every
// category the operator did NOT request via --checks zeroed out.
// Returns the input unchanged when --checks is empty (no filter).
// Status, summary, and auto-fix consume the filtered report so the
// verdict only reflects categories the operator actually asked about;
// renderDoctorFiltered keeps using the unfiltered report because IT
// already gates per-section rendering on doctorCheckEnabled directly.
func filterReportByDoctorChecks(r diagnostics.Report) diagnostics.Report {
	if doctorChecks == "" {
		return r
	}
	filtered := r
	if !doctorCheckEnabled("environment") {
		filtered.DockerVersion = ""
		filtered.ComposeVersion = ""
	}
	if !doctorCheckEnabled("health") {
		filtered.HealthStatus = ""
		filtered.HealthBody = ""
	}
	if !doctorCheckEnabled("containers") {
		filtered.ContainerPS = ""
		filtered.ContainerSummary = nil
	}
	if !doctorCheckEnabled("images") {
		filtered.ImageStatus = nil
	}
	if !doctorCheckEnabled("compose") {
		// "Compose exists" is the OK signal; pretend it does so the
		// doctorComposeError heuristic does not flag a missing file
		// the operator deliberately scoped out.
		filtered.ComposeFileExists = true
		filtered.ComposeFileValid = nil
		filtered.PortConflicts = nil
	}
	if !doctorCheckEnabled("config") {
		filtered.ConfigRedacted = ""
	}
	if !doctorCheckEnabled("disk") {
		filtered.DiskInfo = ""
	}
	if !doctorCheckEnabled("errors") {
		filtered.RecentLogs = ""
		filtered.Errors = nil
	}
	return filtered
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
	needComposeFix, needRestart, unfixable := classifyDoctorIssues(issues)
	if !needComposeFix && !needRestart && len(unfixable) == 0 {
		out.Success("No fixable issues in selected checks")
		return false
	}
	composeFixed := false
	if needComposeFix {
		composeFixed = runDoctorComposeFix(out, errOut, state, safeDir)
	}
	restartDone := false
	if needRestart {
		restartDone = runDoctorRestart(ctx, out, errOut, safeDir)
	}
	for _, issue := range unfixable {
		out.HintNextStep(fmt.Sprintf("No auto-fix available for: %s", issue))
	}
	return composeFixed || restartDone
}

// classifyDoctorIssues sorts issues into the two fixable buckets
// (compose-regeneration and restart) plus an unfixable remainder.
// Each issue is mapped to its originating check via classifyDoctorIssue
// and dropped entirely when that check is excluded by --checks, so a
// `--checks=compose` run never surfaces "No auto-fix available for:
// <unrelated issue>" hints for categories the operator excluded.
func classifyDoctorIssues(issues []string) (needComposeFix, needRestart bool, unfixable []string) {
	for _, issue := range issues {
		c := classifyDoctorIssue(issue)
		if !doctorCheckEnabled(c.category) {
			continue
		}
		switch c.kind {
		case doctorIssueComposeFix:
			needComposeFix = true
		case doctorIssueRestart:
			needRestart = true
		case doctorIssueUnfixable:
			unfixable = append(unfixable, issue)
		}
	}
	return needComposeFix, needRestart, unfixable
}

// doctorIssueKind identifies which auto-fix bucket an issue belongs to.
type doctorIssueKind int

const (
	doctorIssueUnfixable doctorIssueKind = iota
	doctorIssueComposeFix
	doctorIssueRestart
)

// doctorClassification carries the auto-fix bucket alongside the
// originating --checks category so classifyDoctorIssues can honour the
// per-category filter on every kind (fixable AND unfixable).
type doctorClassification struct {
	kind     doctorIssueKind
	category string
}

// doctorIssuePattern is one row in the issue-classification table.
// First-match wins (table order is the precedence chain). Either
// allSubstrings (every entry must be present) or anySubstring (one is
// enough) may be set; both being set is an AND of "every all" plus
// "any one of any".
type doctorIssuePattern struct {
	allSubstrings []string
	anySubstring  []string
	kind          doctorIssueKind
	category      string
}

// doctorIssuePatterns maps issue substrings to the auto-fix bucket and
// the --checks category that produced them. Table-driven (package-
// level) so classifyDoctorIssue stays under the cyclomatic-complexity
// ceiling, and so adding a new issue type is a single struct literal
// rather than a new switch case. Tracks the issue producers in
// collectDoctorErrors / collectDoctorWarnings.
var doctorIssuePatterns = []doctorIssuePattern{
	{allSubstrings: []string{"compose.yml"}, anySubstring: []string{"not found", "invalid"}, kind: doctorIssueComposeFix, category: "compose"},
	{anySubstring: []string{"port conflict"}, kind: doctorIssueUnfixable, category: "compose"},
	{anySubstring: []string{"unhealthy", "exited"}, kind: doctorIssueRestart, category: "containers"},
	{anySubstring: []string{"still starting", "no containers"}, kind: doctorIssueUnfixable, category: "containers"},
	{anySubstring: []string{"backend unreachable", "backend unhealthy"}, kind: doctorIssueUnfixable, category: "health"},
	{anySubstring: []string{": available", ": missing", "digest"}, kind: doctorIssueUnfixable, category: "images"},
}

// classifyDoctorIssue returns the auto-fix bucket and originating
// --checks category for a single issue string. Falls back to the
// {unfixable, "errors"} catch-all for anything not matched by the
// table -- r.Errors entries from collectDoctorErrors typically land
// here.
func classifyDoctorIssue(issue string) doctorClassification {
	for _, p := range doctorIssuePatterns {
		if matchesDoctorIssue(issue, p) {
			return doctorClassification{p.kind, p.category}
		}
	}
	return doctorClassification{doctorIssueUnfixable, "errors"}
}

// matchesDoctorIssue evaluates one pattern row against an issue.
func matchesDoctorIssue(issue string, p doctorIssuePattern) bool {
	for _, s := range p.allSubstrings {
		if !strings.Contains(issue, s) {
			return false
		}
	}
	if len(p.anySubstring) == 0 {
		return true
	}
	for _, s := range p.anySubstring {
		if strings.Contains(issue, s) {
			return true
		}
	}
	return false
}

// runDoctorComposeFix attempts to regenerate compose.yml. Returns true
// on success so the caller (doctorAutoFix) can report an honest fixed-
// flag instead of the prior intent-flag-based approximation.
func runDoctorComposeFix(out, errOut *ui.UI, state config.State, safeDir string) bool {
	out.Step("Regenerating compose.yml from template...")
	if fixErr := doctorFixCompose(state, safeDir); fixErr != nil {
		errOut.Error(fmt.Sprintf("Could not regenerate compose: %v", fixErr))
		return false
	}
	out.Success("Regenerated compose.yml from template")
	return true
}

// runDoctorRestart attempts to restart containers. Returns true on
// success; a Docker-not-available warning and a compose-restart failure
// both report false so the doctorAutoFix summary reflects reality.
func runDoctorRestart(ctx context.Context, out, errOut *ui.UI, safeDir string) bool {
	info, dockerErr := docker.Detect(ctx)
	if dockerErr != nil {
		errOut.Warn(fmt.Sprintf("Cannot restart containers: Docker not available (%v)", dockerErr))
		return false
	}
	out.Step("Restarting containers...")
	if fixErr := composeRunQuiet(ctx, info, safeDir, "restart"); fixErr != nil {
		errOut.Error(fmt.Sprintf("Restart failed: %v", fixErr))
		return false
	}
	out.Success("Containers restarted")
	return true
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
	warnings, errs := collectDoctorWarnings(r), collectDoctorErrors(r)
	if len(errs) > 0 {
		return doctorErrors, errs
	}
	if len(warnings) > 0 {
		return doctorWarnings, warnings
	}
	return doctorHealthy, nil
}

func collectDoctorErrors(r diagnostics.Report) []string {
	var errs []string
	if msg, ok := doctorHealthError(r.HealthStatus); ok {
		errs = append(errs, msg)
	}
	errs = append(errs, doctorContainerErrors(r.ContainerSummary)...)
	if msg, ok := doctorComposeError(r); ok {
		errs = append(errs, msg)
	}
	for _, p := range r.PortConflicts {
		errs = append(errs, fmt.Sprintf("port conflict: %s", p))
	}
	errs = append(errs, r.Errors...)
	return errs
}

func doctorHealthError(status string) (string, bool) {
	switch status {
	case "200", "":
		return "", false
	case "unreachable":
		return "backend unreachable", true
	default:
		return fmt.Sprintf("backend unhealthy (HTTP %s)", status), true
	}
}

func doctorContainerErrors(containers []diagnostics.ContainerDetail) []string {
	var errs []string
	for _, c := range containers {
		if c.Health != "unhealthy" && c.State != "exited" {
			continue
		}
		status := c.Health
		if status == "" {
			status = c.State
		}
		errs = append(errs, fmt.Sprintf("%s %s", c.Name, status))
	}
	return errs
}

func doctorComposeError(r diagnostics.Report) (string, bool) {
	switch {
	case !r.ComposeFileExists:
		return "compose.yml not found", true
	case r.ComposeFileValid != nil && !*r.ComposeFileValid:
		return "compose.yml is invalid", true
	}
	return "", false
}

func collectDoctorWarnings(r diagnostics.Report) []string {
	// Upper bound: at most one "no containers" + one per ContainerSummary
	// + one per ImageStatus + one compose-validity warning.
	warnings := make([]string, 0, 2+len(r.ContainerSummary)+len(r.ImageStatus))
	warnings = append(warnings, doctorNoContainersWarning(r)...)
	warnings = append(warnings, doctorContainerStartingWarnings(r)...)
	warnings = append(warnings, doctorImageStatusWarnings(r)...)
	warnings = append(warnings, doctorComposeValidityWarning(r)...)
	return warnings
}

// doctorNoContainersWarning emits "no containers detected" only when
// the containers category is part of the (possibly --checks-filtered)
// report. filterReportByDoctorChecks zeros ContainerSummary when the
// operator scopes containers out, so without the gate the warning
// surfaces a finding from an excluded category.
func doctorNoContainersWarning(r diagnostics.Report) []string {
	if !doctorCheckEnabled("containers") {
		return nil
	}
	if len(r.ContainerSummary) != 0 || !r.ComposeFileExists {
		return nil
	}
	return []string{"no containers detected"}
}

// doctorContainerStartingWarnings emits a "still starting" warning
// per container caught mid-start. The ContainerSummary slice is
// already empty when filterReportByDoctorChecks scopes containers
// out, so no extra gate is needed.
func doctorContainerStartingWarnings(r diagnostics.Report) []string {
	var warnings []string
	for _, c := range r.ContainerSummary {
		if c.Health == "starting" {
			warnings = append(warnings, fmt.Sprintf("%s still starting", c.Name))
		}
	}
	return warnings
}

// doctorImageStatusWarnings emits each non-"available" image status
// line. ImageStatus is nil when filterReportByDoctorChecks scopes
// images out, so no extra gate is needed.
func doctorImageStatusWarnings(r diagnostics.Report) []string {
	var warnings []string
	for _, img := range r.ImageStatus {
		if !strings.HasSuffix(img, ": available") {
			warnings = append(warnings, img)
		}
	}
	return warnings
}

// doctorComposeValidityWarning emits "compose.yml exists, validity
// not checked" only when the compose category is part of the report.
// filterReportByDoctorChecks forces ComposeFileExists=true and clears
// ComposeFileValid when the operator scopes compose out, so without
// the gate the warning fires for an excluded category.
func doctorComposeValidityWarning(r diagnostics.Report) []string {
	if !doctorCheckEnabled("compose") {
		return nil
	}
	if !r.ComposeFileExists || r.ComposeFileValid != nil {
		return nil
	}
	return []string{"compose.yml exists, validity not checked"}
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
		case c.State == "running":
			// No docker-level healthcheck declared (e.g. NATS). Treat
			// running as healthy so the row matches probed-healthy peers
			// instead of looking indefinitely "in progress".
			out.Success(fmt.Sprintf("%-24s healthy", c.Name))
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
	renderComposeFileStatus(out, r)
	for _, conflict := range r.PortConflicts {
		out.Error(fmt.Sprintf("Port conflict: %s", conflict))
	}
}

func renderComposeFileStatus(out *ui.UI, r diagnostics.Report) {
	if !r.ComposeFileExists {
		out.Error("Compose file: not found")
		return
	}
	valid := composeValidityWord(r.ComposeFileValid)
	if valid == "valid" {
		out.Success(fmt.Sprintf("Compose file: exists, %s", valid))
		return
	}
	out.Warn(fmt.Sprintf("Compose file: exists, %s", valid))
}

func composeValidityWord(valid *bool) string {
	switch {
	case valid == nil:
		return "not checked"
	case *valid:
		return "valid"
	default:
		return "invalid"
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
