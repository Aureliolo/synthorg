package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var (
	statusWatch    bool
	statusInterval string
	statusWide     bool
	statusNoTrunc  bool
	statusServices string
	statusCheck    bool
)

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show container states, health, and versions",
	Long: `Render a one-shot snapshot of the running SynthOrg stack.

Combines a verdict banner (OK / DEGRADED / CRITICAL), the backend
/api/v1/readyz response, the per-container table from
docker compose ps, and live resource usage. Use --watch to refresh
on an interval, --wide for port columns, --services to filter by
name, or --check for a silent exit-code-only run intended for
scripts (0 healthy, 3 unhealthy, 4 unreachable).`,
	Example: `  synthorg status              # show current status
  synthorg status --watch      # continuously poll
  synthorg status --wide       # show extra columns
  synthorg status --check      # exit code only (for scripts)`,
	RunE: runStatus,
}

func init() {
	statusCmd.Flags().BoolVarP(&statusWatch, "watch", "w", false, "continuously poll status")
	statusCmd.Flags().StringVar(&statusInterval, "interval", "2s", "watch polling interval (e.g. 2s, 5s)")
	statusCmd.Flags().BoolVar(&statusWide, "wide", false, "show extra columns (ports)")
	statusCmd.Flags().BoolVar(&statusNoTrunc, "no-trunc", false, "show full image names")
	statusCmd.Flags().StringVar(&statusServices, "services", "", "filter by service names (comma-separated)")
	statusCmd.Flags().BoolVar(&statusCheck, "check", false, "exit code only: 0=healthy, 3=unhealthy, 4=unreachable")
	statusCmd.GroupID = "core"
	rootCmd.AddCommand(statusCmd)
}

func runStatus(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	if statusCheck {
		return runStatusCheckExitCode(ctx, state)
	}
	interval, parseErr := time.ParseDuration(statusInterval)
	if parseErr != nil {
		return fmt.Errorf("invalid --interval %q: %w", statusInterval, parseErr)
	}
	if statusWatch {
		if interval <= 0 {
			return fmt.Errorf("invalid --interval %q: must be > 0", statusInterval)
		}
		return runStatusWatch(cmd, state, opts, interval)
	}
	if err := runStatusOnce(cmd, state, opts); err != nil {
		return fmt.Errorf("running status check: %w", err)
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	out.HintGuidance("Use --watch for continuous monitoring, or --check for scripted health checks.")
	return nil
}

// runStatusCheckExitCode implements --check: a silent mode that returns
// an ExitError with the appropriate code (0 healthy, 3 unhealthy, 4
// unreachable). Validates the response body for status="ok" rather than
// trusting the HTTP status alone.
func runStatusCheckExitCode(ctx context.Context, state config.State) error {
	body, statusCode, fetchErr := fetchHealth(ctx, state.BackendPort)
	if fetchErr != nil {
		return NewExitError(ExitUnreachable, fetchErr)
	}
	if statusCode < 200 || statusCode >= 300 {
		return NewExitError(ExitUnhealthy, nil)
	}
	var envelope struct {
		Data healthResponse `json:"data"`
	}
	if json.Unmarshal(body, &envelope) != nil || envelope.Data.Status != "ok" {
		return NewExitError(ExitUnhealthy, nil)
	}
	return nil
}

func runStatusWatch(cmd *cobra.Command, state config.State, opts *GlobalOpts, interval time.Duration) error {
	ctx := cmd.Context()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		// Clear screen (best-effort: ANSI escape for TTY, separator for non-TTY).
		if isInteractive() && !opts.Plain {
			_, _ = fmt.Fprint(cmd.OutOrStdout(), "\033[H\033[2J")
		} else {
			_, _ = fmt.Fprintln(cmd.OutOrStdout(), "---")
		}
		if err := runStatusOnce(cmd, state, opts); err != nil {
			return fmt.Errorf("refreshing status: %w", err)
		}

		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func runStatusOnce(cmd *cobra.Command, state config.State, opts *GlobalOpts) error {
	ctx := cmd.Context()
	jsonOut := opts.JSON
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	printVersionInfo(out, state)

	safeDir, err := safeStateDir(state)
	if err != nil {
		return fmt.Errorf("resolving data directory: %w", err)
	}
	composePath := filepath.Join(safeDir, "compose.yml")
	if _, err := os.Stat(composePath); errors.Is(err, os.ErrNotExist) {
		out.Warn("Not initialized -- run 'synthorg init' first.")
		return nil
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		out.Warn(fmt.Sprintf("Docker not available: %v", err))
		return nil
	}
	out.KeyValue("Docker", info.DockerVersion)
	out.KeyValue("Compose", info.ComposeVersion)
	_, _ = fmt.Fprintln(out.Writer())

	// Gather every signal first so the top banner can summarise
	// before we render per-section detail. Sections below stay fed
	// by the same snapshot so the banner and the body never disagree.
	snap := gatherStatusSnapshot(ctx, info, safeDir, state)

	if !jsonOut {
		renderTopBanner(out, snap)
	}

	renderHealthSection(out, snap, jsonOut)
	renderContainersSection(out, snap, jsonOut)
	printResourceUsage(ctx, out, info, safeDir)
	if state.PersistenceBackend == "postgres" && statusWide {
		printPostgresVolumeInfo(ctx, out, info)
	}
	printLinks(out, state)

	return nil
}

// printPostgresVolumeInfo reports the size of the synthorg-pgdata named
// volume when the Postgres persistence backend is active.
func printPostgresVolumeInfo(ctx context.Context, out *ui.UI, info docker.Info) {
	_, err := docker.RunCmd(
		ctx, info.DockerPath,
		"volume", "inspect", "synthorg-pgdata",
		"--format", "{{.Mountpoint}}",
	)
	if err != nil {
		out.KeyValue("Postgres volume", "synthorg-pgdata (not created yet)")
		return
	}
	dfOut, dfErr := docker.RunCmd(
		ctx, info.DockerPath,
		"system", "df", "-v", "--format", "{{json .Volumes}}",
	)
	if dfErr != nil {
		out.KeyValue("Postgres volume", "synthorg-pgdata (size unavailable)")
		return
	}
	var volumes []struct {
		Name string `json:"Name"`
		Size string `json:"Size"`
	}
	if unmarshalErr := json.Unmarshal([]byte(dfOut), &volumes); unmarshalErr != nil {
		out.KeyValue("Postgres volume", "synthorg-pgdata (size unavailable)")
		return
	}
	for _, v := range volumes {
		if v.Name == "synthorg-pgdata" {
			out.KeyValue("Postgres volume", fmt.Sprintf("synthorg-pgdata (%s)", v.Size))
			return
		}
	}
	out.KeyValue("Postgres volume", "synthorg-pgdata")
}

func printVersionInfo(out *ui.UI, state config.State) {
	out.KeyValue("CLI version", fmt.Sprintf("%s (%s)", version.Version, version.Commit))
	out.KeyValue("Data dir", state.DataDir)
	out.KeyValue("Image tag", state.ImageTag)
	out.KeyValue("Channel", state.DisplayChannel())
	_, _ = fmt.Fprintln(out.Writer())
}

// containerInfo holds parsed container state from docker compose ps.
type containerInfo struct {
	Name    string `json:"Name"`
	Service string `json:"Service"`
	State   string `json:"State"`
	Health  string `json:"Health"`
	Status  string `json:"Status"`
	Ports   string `json:"Ports"`
	Image   string `json:"Image"`
}

// imageTag extracts the tag from an image string like "ghcr.io/foo/bar:v1.0".
// Handles registry ports correctly (e.g. "registry:5000/image" has no tag).
func imageTag(image string) string {
	i := strings.LastIndex(image, ":")
	if i < 0 || i < strings.LastIndex(image, "/") {
		return image
	}
	return image[i+1:]
}

// healthIcon returns a status icon for a container's health/state.
//
// A running container with no Health field (empty string) is treated as
// successful because it declared no Docker-level healthcheck (e.g. NATS,
// where application-level liveness is surfaced via /api/v1/readyz instead).
// Without this, the status table would show an indefinite in-progress spinner,
// misleading the reader into thinking the container is still starting.
func healthIcon(state, health string) string {
	if health == "healthy" {
		return ui.IconSuccess
	}
	if health == "unhealthy" {
		return ui.IconError
	}
	if state == "running" {
		if health == "" {
			return ui.IconSuccess
		}
		return ui.IconInProgress
	}
	if state == "restarting" {
		return ui.IconWarning
	}
	return ui.IconError
}

// parseContainerJSON parses docker compose ps output.
// Handles both JSON array (Compose v2.21+) and NDJSON (older versions).
func parseContainerJSON(psOut string) ([]containerInfo, int) {
	trimmed := strings.TrimSpace(psOut)
	// Try JSON array first (Compose v2.21+).
	if strings.HasPrefix(trimmed, "[") {
		var containers []containerInfo
		if json.Unmarshal([]byte(trimmed), &containers) == nil {
			return containers, 0
		}
	}
	// Fall back to NDJSON (one object per line).
	var containers []containerInfo
	var failures int
	for _, line := range strings.Split(trimmed, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var c containerInfo
		if json.Unmarshal([]byte(line), &c) == nil {
			containers = append(containers, c)
		} else {
			failures++
		}
	}
	return containers, failures
}

// renderContainerTable formats containers as a table.
func renderContainerTable(out *ui.UI, containers []containerInfo, wide, noTrunc bool) {
	headers := []string{"SERVICE", "STATE", "HEALTH", "IMAGE", "STATUS"}
	if wide {
		headers = append(headers, "PORTS")
	}
	rows := make([][]string, 0, len(containers))
	for _, c := range containers {
		icon := healthIcon(c.State, c.Health)
		healthLabel := c.Health
		if healthLabel == "" {
			// Empty Health field on a running container means no
			// docker-level healthcheck declared. "no probe" is clearer
			// than "-" and prevents the reader from assuming the container
			// is broken. "-" is shown for non-running containers.
			if c.State == "running" {
				healthLabel = "no probe"
			} else {
				healthLabel = "-"
			}
		}
		imageDisplay := imageTag(c.Image)
		if noTrunc {
			imageDisplay = c.Image
		}
		row := []string{
			c.Service, icon + " " + c.State, healthLabel,
			imageDisplay, c.Status,
		}
		if wide {
			row = append(row, c.Ports)
		}
		rows = append(rows, row)
	}
	out.Table(headers, rows)
}

// statusSnapshot is the consolidated view used by both the top banner
// and the per-section renderers below. Collecting once and rendering N
// times guarantees the summary line never contradicts the detail rows.
type statusSnapshot struct {
	containers          []containerInfo
	containerErr        error
	parseFailures       int
	servicesFilterEmpty bool

	healthFetched     bool
	healthErr         error
	healthStatusCode  int
	healthBody        []byte
	healthEnvelopeOK  bool
	healthData        healthResponse
	persistenceWired  bool
	messageBusWired   bool
	expectsPersistent bool
	expectsMessageBus bool
}

// statusLevel encodes the overall verdict for the top banner. Order
// matters: callers compare with `>` to escalate (Critical wins).
type statusLevel int

const (
	statusLevelOK statusLevel = iota
	statusLevelDegraded
	statusLevelCritical
)

// statusVerdict is what the top banner ultimately prints: a level, a
// one-line summary, and a list of bulleted issues + recovery hints.
type statusVerdict struct {
	level   statusLevel
	summary string
	issues  []string
	hints   []string
}

// gatherStatusSnapshot collects every signal the status command renders
// from. Any single source failure is recorded on the snapshot rather
// than aborting the call, so the banner can still report partial state.
func gatherStatusSnapshot(ctx context.Context, info docker.Info, safeDir string, state config.State) statusSnapshot {
	snap := statusSnapshot{
		servicesFilterEmpty: statusServices == "",
		expectsPersistent:   state.PersistenceBackend != "",
		expectsMessageBus:   state.BusBackend == "nats",
	}

	psOut, err := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "--format", "json")
	if err != nil {
		snap.containerErr = err
	} else {
		containers, failures := parseContainerJSON(psOut)
		snap.containers = containers
		snap.parseFailures = failures
	}

	body, code, fetchErr := fetchHealth(ctx, state.BackendPort)
	snap.healthFetched = true
	snap.healthStatusCode = code
	snap.healthBody = body
	if fetchErr != nil {
		snap.healthErr = fetchErr
		return snap
	}

	var envelope struct {
		Data healthResponse `json:"data"`
	}
	if json.Unmarshal(body, &envelope) == nil && envelope.Data.Status != "" {
		snap.healthEnvelopeOK = true
		snap.healthData = envelope.Data
		snap.persistenceWired = envelope.Data.Persistence != nil
		snap.messageBusWired = envelope.Data.MessageBus != nil
	}
	return snap
}

// computeVerdict turns a snapshot into the banner verdict. The order of
// checks below dictates which message wins when multiple signals fail
// at once: backend reachability first (everything depends on it), then
// per-container failures, then the half-up persistence/bus signals.
func computeVerdict(snap statusSnapshot) statusVerdict {
	v := statusVerdict{level: statusLevelOK}
	v.absorbContainerVerdict(snap)
	v.absorbHealthVerdict(snap)
	v.finaliseSummary()
	return v
}

// absorbContainerVerdict folds the container-fleet signals (query
// error, unhealthy / restarting counts, empty filter) into v. Critical
// > Degraded; signals never downgrade an already-Critical verdict.
func (v *statusVerdict) absorbContainerVerdict(snap statusSnapshot) {
	if snap.containerErr != nil {
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("could not query containers: %v", snap.containerErr))
		v.hints = append(v.hints, "Check Docker is running: docker ps")
	}
	unhealthy, restarting, total := countContainerStates(snap)
	if total == 0 && snap.containerErr == nil && snap.servicesFilterEmpty {
		if v.level < statusLevelCritical {
			v.level = statusLevelCritical
		}
		v.issues = append(v.issues, "no containers running")
		v.hints = append(v.hints, "Start the stack: synthorg start")
	}
	if unhealthy > 0 {
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("%d container(s) unhealthy", unhealthy))
		v.hints = append(v.hints, "Inspect failing services: synthorg logs <service>")
	}
	if restarting > 0 {
		if v.level < statusLevelDegraded {
			v.level = statusLevelDegraded
		}
		v.issues = append(v.issues, fmt.Sprintf("%d container(s) restarting", restarting))
		v.hints = append(v.hints, "Tail restart-loop logs: synthorg logs <service> --follow")
	}
}

// countContainerStates returns (unhealthy, restarting, total) honouring
// the --services filter.
func countContainerStates(snap statusSnapshot) (unhealthy, restarting, total int) {
	for _, c := range snap.containers {
		if statusServices != "" && !filterAllowsService(c.Service) {
			continue
		}
		total++
		switch {
		case c.Health == "unhealthy":
			unhealthy++
		case c.State == "restarting":
			restarting++
		}
	}
	return unhealthy, restarting, total
}

// absorbHealthVerdict folds the backend `/healthz` envelope and the
// half-up persistence/bus signals into v.
func (v *statusVerdict) absorbHealthVerdict(snap statusSnapshot) {
	if v.absorbHealthEnvelope(snap) {
		return
	}
	v.absorbWiringVerdict(snap)
}

// absorbHealthEnvelope handles the /healthz envelope itself (reach,
// parseability, status field). Returns true when the envelope is
// terminal-bad (caller should NOT continue with wiring checks).
func (v *statusVerdict) absorbHealthEnvelope(snap statusSnapshot) bool {
	switch {
	case snap.healthErr != nil:
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("backend unreachable: %v", snap.healthErr))
		v.hints = append(v.hints, "Confirm backend is up: synthorg logs backend")
		return true
	case !snap.healthEnvelopeOK:
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("backend returned unparseable health (HTTP %d)", snap.healthStatusCode))
		v.hints = append(v.hints, "Backend may be starting or misconfigured: synthorg logs backend")
		return true
	}
	if snap.healthStatusCode < 200 || snap.healthStatusCode >= 300 || snap.healthData.Status != "ok" {
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("backend reports status=%q (HTTP %d)", snap.healthData.Status, snap.healthStatusCode))
		v.hints = append(v.hints, "Run 'synthorg doctor' for diagnostics")
	}
	return false
}

// absorbWiringVerdict handles persistence and message-bus wiring
// signals: persistence not wired is Critical (controllers 503), message
// bus not wired is Degraded.
func (v *statusVerdict) absorbWiringVerdict(snap statusSnapshot) {
	if snap.expectsPersistent && !snap.persistenceWired {
		v.level = statusLevelCritical
		v.issues = append(v.issues, "persistence backend not wired (controllers will return 503)")
		v.hints = append(v.hints, "Backend env or DB URL is wrong: check synthorg logs backend for 'persistence' warnings")
	}
	if snap.expectsMessageBus && !snap.messageBusWired {
		if v.level < statusLevelDegraded {
			v.level = statusLevelDegraded
		}
		v.issues = append(v.issues, "message bus not connected")
		v.hints = append(v.hints, "Check NATS container if distributed bus mode is enabled: synthorg logs nats")
	}
}

func (v *statusVerdict) finaliseSummary() {
	switch v.level {
	case statusLevelOK:
		v.summary = "All systems operational"
	case statusLevelDegraded:
		v.summary = fmt.Sprintf("Degraded: %d issue(s)", len(v.issues))
	case statusLevelCritical:
		v.summary = fmt.Sprintf("CRITICAL: %d issue(s)", len(v.issues))
	}
}

// filterAllowsService mirrors filterByServices' filter logic against a
// single service name. Used by computeVerdict so the banner respects
// --services without rebuilding the filter map.
func filterAllowsService(service string) bool {
	if statusServices == "" {
		return true
	}
	for _, s := range strings.Split(statusServices, ",") {
		if strings.TrimSpace(s) == service {
			return true
		}
	}
	return false
}

// renderTopBanner prints the headline status box. Critical fires a red
// box, degraded fires amber, OK collapses to a single green line so the
// happy path stays compact (the user does not need a banner to tell
// them everything works).
func renderTopBanner(out *ui.UI, snap statusSnapshot) {
	v := computeVerdict(snap)
	if v.level == statusLevelOK {
		out.Success(v.summary)
		out.Blank()
		return
	}

	lines := make([]string, 0, len(v.issues)+len(v.hints)+1)
	lines = append(lines, "  "+v.summary)
	for _, issue := range v.issues {
		lines = append(lines, "  - "+issue)
	}
	if len(v.hints) > 0 {
		lines = append(lines, "")
		lines = append(lines, "  Try:")
		for _, hint := range v.hints {
			lines = append(lines, "    > "+hint)
		}
	}

	if v.level == statusLevelCritical {
		out.BoxError("Status: CRITICAL", lines)
	} else {
		out.BoxWarn("Status: DEGRADED", lines)
	}
	out.Blank()
}

// renderHealthSection prints the backend health summary (with an
// explicit red persistence line when the backend is half-up). Pulled up
// above the container table so the highest-signal information leads.
func renderHealthSection(out *ui.UI, snap statusSnapshot, jsonOut bool) {
	if jsonOut {
		renderHealthSectionJSON(out, snap)
		return
	}
	if !renderHealthSectionBackend(out, snap) {
		return
	}
	renderHealthSectionPersistence(out, snap)
	hr := snap.healthData
	if hr.MessageBus != nil {
		out.KeyValue("Message bus", fmt.Sprintf("%v", hr.MessageBus))
	}
	if hr.Telemetry != "" {
		out.KeyValue("Telemetry", hr.Telemetry)
	}
	out.Blank()
}

func renderHealthSectionJSON(out *ui.UI, snap statusSnapshot) {
	w := out.Writer()
	_, _ = fmt.Fprintln(w, "Health check:")
	if snap.healthBody != nil {
		_, _ = fmt.Fprintf(w, "  %s\n", string(snap.healthBody))
	} else if snap.healthErr != nil {
		_, _ = fmt.Fprintf(w, "  error: %v\n", snap.healthErr)
	}
}

// renderHealthSectionBackend prints the top-level backend reachability
// line. Returns true if the section should continue (envelope parsed)
// or false if the caller should stop here.
func renderHealthSectionBackend(out *ui.UI, snap statusSnapshot) bool {
	if snap.healthErr != nil {
		out.Error(fmt.Sprintf("Backend unreachable: %v", snap.healthErr))
		out.HintError("Run 'synthorg logs backend' to see why.")
		return false
	}
	if !snap.healthEnvelopeOK {
		out.Warn(fmt.Sprintf("Backend health: unparseable response (HTTP %d)", snap.healthStatusCode))
		return false
	}
	hr := snap.healthData
	if snap.healthStatusCode >= 200 && snap.healthStatusCode < 300 && hr.Status == "ok" {
		out.Success(fmt.Sprintf("Backend healthy (v%s, uptime %s)", hr.Version, formatUptime(hr.Uptime)))
		return true
	}
	out.Error(fmt.Sprintf("Backend unhealthy (HTTP %d)", snap.healthStatusCode))
	out.HintError("Run 'synthorg doctor' for diagnostics.")
	return true
}

// renderHealthSectionPersistence prints the persistence-wiring line,
// emitting an explicit "NOT WIRED" error when the backend is half-up.
func renderHealthSectionPersistence(out *ui.UI, snap statusSnapshot) {
	hr := snap.healthData
	switch {
	case snap.expectsPersistent && !snap.persistenceWired:
		out.Error("Persistence: NOT WIRED -- controllers depending on persistence will return 503")
		out.HintError("Check 'synthorg logs backend' for the auto_wire warning that names the missing env var.")
	case hr.Persistence != nil:
		out.KeyValue("Persistence", fmt.Sprintf("%v", hr.Persistence))
	default:
		out.KeyValue("Persistence", "not configured")
	}
}

// renderContainersSection prints the per-container table with health
// already computed by gatherStatusSnapshot.
func renderContainersSection(out *ui.UI, snap statusSnapshot, jsonOut bool) {
	containers := snap.containers
	if statusServices != "" {
		containers = filterByServices(out, containers, statusServices)
	}

	w := out.Writer()
	if jsonOut {
		b, err := json.MarshalIndent(containers, "", "  ")
		if err != nil {
			out.Warn(fmt.Sprintf("Could not marshal container JSON: %v", err))
			return
		}
		_, _ = fmt.Fprintln(w, string(b))
		return
	}
	if snap.containerErr != nil {
		// Already surfaced in the top banner; keep the section concise.
		return
	}
	if snap.parseFailures > 0 {
		out.Warn(fmt.Sprintf("%d container lines could not be parsed", snap.parseFailures))
	}
	if len(containers) == 0 {
		if statusServices != "" {
			out.Warn("No containers match requested services")
		}
		return
	}
	_, _ = fmt.Fprintln(w, "Containers:")
	renderContainerTable(out, containers, statusWide, statusNoTrunc)
	if !statusWide {
		if statusServices == "" {
			out.HintGuidance("Use --wide to show port mappings, or --services to filter by name.")
		} else {
			out.HintGuidance("Use --wide to show port mappings.")
		}
	}
	out.HintTip("Run 'synthorg logs' to view container logs")
	_, _ = fmt.Fprintln(w)
}

// filterByServices filters containers to only those matching the comma-separated
// service names, warning about invalid names.
func filterByServices(out *ui.UI, containers []containerInfo, services string) []containerInfo {
	filter := make(map[string]bool)
	for _, s := range strings.Split(services, ",") {
		s = strings.TrimSpace(s)
		if s == "" {
			continue
		}
		if !serviceNamePattern.MatchString(s) {
			out.Warn(fmt.Sprintf("invalid service name %q in --services: must be alphanumeric, hyphens, or underscores", s))
			continue
		}
		filter[s] = true
	}
	filtered := containers[:0]
	for _, c := range containers {
		if filter[c.Service] {
			filtered = append(filtered, c)
		}
	}
	return filtered
}

func printResourceUsage(ctx context.Context, out *ui.UI, info docker.Info, dataDir string) {
	psOut, err := docker.ComposeExecOutput(ctx, info, dataDir, "ps", "-q")
	if err != nil || strings.TrimSpace(psOut) == "" {
		return
	}
	ids := strings.Fields(strings.TrimSpace(psOut))
	statsArgs := append([]string{"stats", "--no-stream", "--format",
		"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}", "--"}, ids...)
	statsOut, err := docker.RunCmd(ctx, info.DockerPath, statsArgs...)
	if err != nil {
		out.Warn(fmt.Sprintf("Could not get resource usage: %v", err))
		return
	}
	w := out.Writer()
	_, _ = fmt.Fprintln(w, "Resource usage:")
	_, _ = fmt.Fprintln(w, statsOut)
}

// healthResponse holds the parsed health check JSON.
type healthResponse struct {
	Status      string  `json:"status"`
	Version     string  `json:"version"`
	Persistence any     `json:"persistence"`
	MessageBus  any     `json:"message_bus"`
	Telemetry   string  `json:"telemetry"`
	Uptime      float64 `json:"uptime_seconds"`
}

func fetchHealth(ctx context.Context, port int) ([]byte, int, error) {
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", port)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("health check error: %w", err)
	}
	resp, err := health.HTTPClient().Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("backend unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	if err != nil {
		return nil, 0, fmt.Errorf("health check read error: %w", err)
	}
	return body, resp.StatusCode, nil
}

// formatUptime converts seconds to a human-readable duration like "3h 36m".
func formatUptime(seconds float64) string {
	if seconds < 0 {
		return "-" + formatUptime(-seconds)
	}
	d := time.Duration(seconds) * time.Second
	h := int(d.Hours())
	m := int(d.Minutes()) % 60
	if h > 0 {
		return fmt.Sprintf("%dh %dm", h, m)
	}
	if m > 0 {
		return fmt.Sprintf("%dm %ds", m, int(d.Seconds())%60)
	}
	return fmt.Sprintf("%ds", int(d.Seconds()))
}

func printLinks(out *ui.UI, state config.State) {
	out.Blank()
	out.Box("Links", []string{
		fmt.Sprintf("  %-12s http://localhost:%d", "Dashboard", state.WebPort),
		fmt.Sprintf("  %-12s http://localhost:%d/api", "API docs", state.BackendPort),
		fmt.Sprintf("  %-12s http://localhost:%d/api/v1/readyz", "Ready", state.BackendPort),
	})
}
