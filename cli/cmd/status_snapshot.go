package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
)

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

// statusSnapshot is the consolidated view used by both the top banner
// and the per-section renderers. Collecting once and rendering N
// times guarantees the summary line never contradicts the detail rows.
type statusSnapshot struct {
	containers          []containerInfo
	containerErr        error
	parseFailures       int
	servicesFilterEmpty bool

	healthFetched    bool
	healthErr        error
	healthStatusCode int
	healthBody       []byte
	healthEnvelopeOK bool
	healthData       healthResponse
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
	}
	return snap
}

// computeVerdict turns a snapshot into the banner verdict. The order of
// checks below dictates which message wins when multiple signals fail
// at once: backend reachability first (everything depends on it), then
// per-container failures, then the readiness outcome.
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

// absorbHealthVerdict folds the /readyz envelope (reach, parseability,
// readiness outcome) into v. The probe is deliberately topology-free
// (the per-component breakdown lives behind authentication on
// GET /health), so its binary outcome already covers every configured
// dependency: persistence, message bus, and providers.
func (v *statusVerdict) absorbHealthVerdict(snap statusSnapshot) {
	switch {
	case snap.healthErr != nil:
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("backend unreachable: %v", snap.healthErr))
		v.hints = append(v.hints, "Confirm backend is up: synthorg logs backend")
		return
	case !snap.healthEnvelopeOK:
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf("backend returned unparseable health (HTTP %d)", snap.healthStatusCode))
		v.hints = append(v.hints, "Backend may be starting or misconfigured: synthorg logs backend")
		return
	}
	if snap.healthStatusCode < 200 || snap.healthStatusCode >= 300 || snap.healthData.Status != "ok" {
		v.level = statusLevelCritical
		v.issues = append(v.issues, fmt.Sprintf(
			"backend not ready (status=%q, HTTP %d): a configured dependency (persistence / message bus / providers) failed its health probe",
			snap.healthData.Status, snap.healthStatusCode))
		v.hints = append(v.hints, "Check 'synthorg logs backend' for the failing component's health-check warning")
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

// healthResponse holds the parsed /readyz payload. The unauthenticated
// probe is topology-free by design: status is "ok" only when every
// configured dependency (persistence / message bus / providers) passed
// its health check, and the per-component breakdown is only available
// behind authentication on GET /health.
type healthResponse struct {
	Status  string  `json:"status"`
	Version string  `json:"version"`
	Uptime  float64 `json:"uptime_seconds"`
}

// fetchHealth is a package var so tests can stub the probe instead of
// depending on whether a real backend happens to listen on the port.
var fetchHealth = func(ctx context.Context, port int) ([]byte, int, error) {
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
	body, err := io.ReadAll(io.LimitReader(resp.Body, config.DefaultHealthResponseLimit))
	if err != nil {
		return nil, 0, fmt.Errorf("health check read error: %w", err)
	}
	return body, resp.StatusCode, nil
}
