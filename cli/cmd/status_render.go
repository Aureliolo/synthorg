package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

// printPostgresVolumeInfo reports the size of the synthorg-pgdata named
// volume when the Postgres persistence backend is active. Docker calls
// are bounded by StatusDockerTimeout so an unresponsive daemon cannot
// hang the status command.
func printPostgresVolumeInfo(ctx context.Context, out *ui.UI, info docker.Info) {
	timeout := GetGlobalOpts(ctx).Tunables.StatusDockerTimeout
	inspectCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	_, err := docker.RunCmd(
		inspectCtx, info.DockerPath,
		"volume", "inspect", "synthorg-pgdata",
		"--format", "{{.Mountpoint}}",
	)
	if err != nil {
		out.KeyValue("Postgres volume", "synthorg-pgdata (not created yet)")
		return
	}
	dfCtx, dfCancel := context.WithTimeout(ctx, timeout)
	defer dfCancel()
	dfOut, dfErr := docker.RunCmd(
		dfCtx, info.DockerPath,
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

// imageTag extracts the tag from an image string like "ghcr.io/foo/bar:v1.0".
// Handles registry ports correctly (e.g. "registry:5000/image" has no tag).
//
// A container started from a locally built image carries no named reference,
// so Docker reports its image id instead. That is a key, not a name: 64 hex
// characters under a column headed IMAGE answer nothing about which version is
// running and push every other column off the terminal. Such a value is
// shortened and labelled as untagged, which is the fact the reader needs.
func imageTag(image string) string {
	if id, ok := bareImageID(image); ok {
		return "untagged (" + id + ")"
	}
	i := strings.LastIndex(image, ":")
	if i < 0 || i < strings.LastIndex(image, "/") {
		return image
	}
	return image[i+1:]
}

// shortImageIDLen is how much of an image id identifies it in practice, and
// is what `docker images` shows.
const shortImageIDLen = 12

// bareImageID reports whether image is an unnamed image id (optionally
// "sha256:"-prefixed) and returns its short form. A hex TAG on a named image
// is not one: the name is what makes it a reference.
func bareImageID(image string) (string, bool) {
	digest := strings.TrimPrefix(image, "sha256:")
	if strings.ContainsAny(digest, "/:") || len(digest) < shortImageIDLen {
		return "", false
	}
	for _, r := range digest {
		if (r < '0' || r > '9') && (r < 'a' || r > 'f') {
			return "", false
		}
	}
	return digest[:shortImageIDLen], true
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

// bannerIssueWidth is how wide a banner issue line may run before it wraps.
//
// ui.Box sizes itself to its longest line and never wraps, so an unwrapped
// issue decides the width of the whole box: a long one soft-wraps in the
// terminal, where the box drawing does not, and the banner stops looking
// like a banner. This is the content width, so the box lands a few columns
// wider and still fits the narrow end of what people actually run.
const bannerIssueWidth = 96

// wrapBannerIssue renders one issue as its bulleted line plus any
// continuation lines, breaking on spaces where it can.
//
// Wrapping rather than truncating, because the issue text is the diagnostic
// itself: a migration refusal names the constraint AND the relation, and a
// cut that keeps the box tidy costs the operator the half that says what to
// fix.
func wrapBannerIssue(issue string) []string {
	const bullet, indent = "  - ", "    "
	var lines []string
	prefix, remaining := bullet, issue
	for len([]rune(remaining)) > bannerIssueWidth {
		runes := []rune(remaining)
		cut := bannerIssueWidth
		if space := lastSpaceBefore(runes, cut); space > 0 {
			cut = space
		}
		lines = append(lines, prefix+strings.TrimRight(string(runes[:cut]), " "))
		remaining = strings.TrimLeft(string(runes[cut:]), " ")
		prefix = indent
	}
	return append(lines, prefix+remaining)
}

// lastSpaceBefore returns the index of the last space at or before limit, or
// 0 when the run has none to break on (a single unbroken token, which is cut
// mid-word rather than allowed to set the box width).
func lastSpaceBefore(runes []rune, limit int) int {
	for i := limit; i > 0; i-- {
		if runes[i] == ' ' {
			return i
		}
	}
	return 0
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
		lines = append(lines, wrapBannerIssue(issue)...)
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

// renderHealthSection prints the backend health summary. Pulled up
// above the container table so the highest-signal information leads.
func renderHealthSection(out *ui.UI, snap statusSnapshot, jsonOut bool) {
	if jsonOut {
		renderHealthSectionJSON(out, snap)
		return
	}
	renderHealthSectionBackend(out, snap)
	out.Blank()
}

// healthSectionJSON is the --json shape of the health section: a single
// well-formed JSON value (never a label line plus a raw byte dump), so
// callers can decode it directly.
type healthSectionJSON struct {
	Ready bool            `json:"ready"`
	Data  json.RawMessage `json:"data,omitempty"`
	Error string          `json:"error,omitempty"`
}

func renderHealthSectionJSON(out *ui.UI, snap statusSnapshot) {
	section := healthSectionJSON{Ready: snap.isReady()}
	switch {
	case snap.healthErr != nil:
		section.Error = snap.healthErr.Error()
	case snap.healthEnvelopeOK:
		// Marshal the already-unwrapped healthData, not the raw
		// healthBody: healthBody is the full ApiResponse envelope
		// (itself carrying a "data" field), so re-wrapping it under
		// this struct's own "data" tag would double-nest the payload.
		data, err := json.Marshal(snap.healthData)
		if err == nil {
			section.Data = json.RawMessage(data)
		} else {
			section.Error = fmt.Sprintf("failed to encode health data: %v", err)
		}
	case snap.healthBody != nil:
		section.Error = fmt.Sprintf("unparseable response (HTTP %d)", snap.healthStatusCode)
	}
	b, err := json.MarshalIndent(section, "", "  ")
	if err != nil {
		// Only reachable if section itself fails to marshal; degrade
		// to a safe fallback rather than emit a broken document.
		_, _ = fmt.Fprintf(out.Writer(), "{\"ready\":false,\"error\":%q}\n", err.Error())
		return
	}
	_, _ = fmt.Fprintln(out.Writer(), string(b))
}

// renderHealthSectionBackend prints the backend reachability and
// readiness line. A ready backend implies every configured dependency
// (persistence / message bus / providers) passed its health probe; the
// unauthenticated /readyz payload carries no per-component breakdown.
func renderHealthSectionBackend(out *ui.UI, snap statusSnapshot) {
	if snap.healthErr != nil {
		out.Error(fmt.Sprintf("Backend unreachable: %v", snap.healthErr))
		out.HintError("Run 'synthorg logs backend' to see why.")
		return
	}
	if !snap.healthEnvelopeOK {
		out.Warn(fmt.Sprintf("Backend health: unparseable response (HTTP %d)", snap.healthStatusCode))
		return
	}
	hr := snap.healthData
	if snap.isReady() {
		out.Success(fmt.Sprintf(
			"Backend healthy (uptime %s) -- all configured dependencies passing",
			formatUptime(hr.Uptime)))
		return
	}
	out.Error(fmt.Sprintf(
		"Backend not ready (HTTP %d) -- a configured dependency (persistence / message bus / providers) is failing its health probe",
		snap.healthStatusCode))
	out.HintError("Check 'synthorg logs backend' for the failing component's health-check warning.")
}

// renderContainersSectionJSON emits the --json containers section as a
// single well-formed JSON document: a container query failure survives
// into the `error` field instead of rendering as an indistinguishable
// empty array.
// bootFailures rides the JSON container section because the scan runs
// whatever the output format, so a scripted consumer already pays for it.
// Reporting a restart count and withholding the reason for it is the
// operator-facing half of the same defect the human banner had.
func renderContainersSectionJSON(
	out *ui.UI,
	containers []containerInfo,
	containerErr error,
	bootFailures map[string]string,
) {
	section := struct {
		Containers   []containerInfo   `json:"containers"`
		BootFailures map[string]string `json:"boot_failures,omitempty"`
		Error        string            `json:"error,omitempty"`
	}{Containers: containers, BootFailures: bootFailures}
	if containerErr != nil {
		section.Error = containerErr.Error()
	}
	b, err := json.MarshalIndent(section, "", "  ")
	if err != nil {
		out.Warn(fmt.Sprintf("Could not marshal container JSON: %v", err))
		return
	}
	_, _ = fmt.Fprintln(out.Writer(), string(b))
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
		renderContainersSectionJSON(out, containers, snap.containerErr, snap.bootFailures)
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
	out.HintNextStep("Run 'synthorg logs' to view container logs")
	_, _ = fmt.Fprintln(w)
}

// filterByServices filters containers to only those matching the comma-separated
// service names, warning about invalid names. Allocates a fresh slice: the
// input aliases the snapshot's backing array, which other renderers still read.
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
	filtered := make([]containerInfo, 0, len(containers))
	for _, c := range containers {
		if filter[c.Service] {
			filtered = append(filtered, c)
		}
	}
	return filtered
}

func printResourceUsage(ctx context.Context, out *ui.UI, info docker.Info, dataDir string) {
	// Resolve the compose project's container NAMES rather than passing
	// `ps -q` IDs straight to `docker stats -- <ids>`. `docker stats` has
	// no --ignore-errors flag, so a single ID that has exited between the
	// listing and the stats call hard-fails the whole invocation (a TOCTOU
	// race during restarts that blanks the entire resource section). Running
	// `docker stats` over ALL running containers can never fail on a
	// vanished specific target; we then filter the rendered rows to this
	// project's names.
	timeout := GetGlobalOpts(ctx).Tunables.StatusDockerTimeout
	namesCtx, namesCancel := context.WithTimeout(ctx, timeout)
	defer namesCancel()
	namesOut, err := docker.ComposeExecOutput(namesCtx, info, dataDir, "ps", "--format", "{{.Name}}")
	if err != nil || strings.TrimSpace(namesOut) == "" {
		return
	}
	composeNames := make(map[string]struct{})
	for _, name := range strings.Fields(namesOut) {
		composeNames[name] = struct{}{}
	}
	statsCtx, statsCancel := context.WithTimeout(ctx, timeout)
	defer statsCancel()
	statsOut, err := docker.RunCmd(statsCtx, info.DockerPath, "stats", "--no-stream", "--format",
		"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}")
	if err != nil {
		out.Warn(fmt.Sprintf("Could not get resource usage: %v", err))
		return
	}
	filtered := filterStatsByName(statsOut, composeNames)
	if filtered == "" {
		return
	}
	w := out.Writer()
	_, _ = fmt.Fprintln(w, "Resource usage:")
	_, _ = fmt.Fprintln(w, filtered)
}

// filterStatsByName keeps the header row plus the rows of `docker stats`
// table output whose first (NAME) column is in names. Returns "" when no
// data row matches, so the caller can omit an empty resource section. The
// NAME column carries no internal spaces, so the first whitespace-delimited
// token is the exact container name.
func filterStatsByName(statsOut string, names map[string]struct{}) string {
	lines := strings.Split(strings.TrimSuffix(statsOut, "\n"), "\n")
	if len(lines) == 0 || strings.TrimSpace(lines[0]) == "" {
		return ""
	}
	kept := []string{lines[0]} // header row
	matched := false
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		if _, ok := names[fields[0]]; ok {
			kept = append(kept, line)
			matched = true
		}
	}
	if !matched {
		return ""
	}
	return strings.Join(kept, "\n")
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
		fmt.Sprintf("  %-12s %s", "Ready", config.APIURL(state.BackendPort, "/readyz")),
	})
}
