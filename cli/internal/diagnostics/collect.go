// Package diagnostics collects system information for bug reports.
package diagnostics

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/images"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"go.yaml.in/yaml/v3"
)

// ContainerDetail summarises a single container's state from compose ps JSON.
type ContainerDetail struct {
	Name   string `json:"Name"`
	State  string `json:"State"`
	Status string `json:"Status"`
	Health string `json:"Health,omitempty"`
}

// Report contains collected diagnostic information.
type Report struct {
	Timestamp      string   `json:"timestamp"`
	OS             string   `json:"os"`
	Arch           string   `json:"arch"`
	CLIVersion     string   `json:"cli_version"`
	CLICommit      string   `json:"cli_commit"`
	DockerVersion  string   `json:"docker_version,omitempty"`
	ComposeVersion string   `json:"compose_version,omitempty"`
	HealthStatus   string   `json:"health_status,omitempty"`
	HealthBody     string   `json:"health_body,omitempty"`
	ContainerPS    string   `json:"container_ps,omitempty"`
	RecentLogs     string   `json:"recent_logs,omitempty"`
	ConfigRedacted string   `json:"config_redacted,omitempty"`
	DiskInfo       string   `json:"disk_info,omitempty"`
	Errors         []string `json:"errors,omitempty"`

	ComposeFileExists bool              `json:"compose_file_exists"`
	ComposeFileValid  *bool             `json:"compose_file_valid,omitempty"`
	PortConflicts     []string          `json:"port_conflicts,omitempty"`
	ImageStatus       []string          `json:"image_status,omitempty"`
	ContainerSummary  []ContainerDetail `json:"container_summary,omitempty"`
}

// Collect gathers diagnostics from the system and running containers.
func Collect(ctx context.Context, state config.State) Report {
	r := Report{
		Timestamp:  time.Now().UTC().Format(time.RFC3339),
		OS:         runtime.GOOS,
		Arch:       runtime.GOARCH,
		CLIVersion: version.Version,
		CLICommit:  version.Commit,
	}

	safeDir, pathErr := config.SecurePath(state.DataDir)
	if pathErr != nil {
		r.Errors = append(r.Errors, fmt.Sprintf("path: %v", pathErr))
	}

	info := collectDocker(ctx, &r, safeDir, pathErr)
	collectHealth(ctx, &r, state.BackendPort)
	collectConfig(&r, state)
	collectInfra(ctx, &r, info, state, safeDir, pathErr)

	if pathErr == nil {
		r.DiskInfo = diskInfo(ctx, safeDir)
	}

	return r
}

func collectDocker(ctx context.Context, r *Report, safeDir string, pathErr error) docker.Info {
	info, err := docker.Detect(ctx)
	if err != nil {
		r.Errors = append(r.Errors, fmt.Sprintf("docker: %v", err))
		return docker.Info{} // zero value signals detection failure to downstream checks
	}
	r.DockerVersion = info.DockerVersion
	r.ComposeVersion = info.ComposeVersion

	for _, w := range docker.CheckMinVersions(info) {
		r.Errors = append(r.Errors, fmt.Sprintf("version: %s", w))
	}

	if pathErr == nil {
		if ps, err := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "--format", "json"); err == nil {
			r.ContainerPS = strings.TrimSpace(ps)
		}
		if logs, err := docker.ComposeExecOutput(ctx, info, safeDir, "logs", "--tail", "50", "--no-color"); err == nil {
			r.RecentLogs = truncate(logs, 4000)
		}
	}
	return info
}

func collectHealth(ctx context.Context, r *Report, backendPort int) {
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", backendPort)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		r.HealthStatus = "unreachable"
		r.Errors = append(r.Errors, fmt.Sprintf("health request: %v", err))
		return
	}
	resp, err := health.HTTPClient().Do(req)
	if err != nil {
		r.HealthStatus = "unreachable"
		r.Errors = append(r.Errors, fmt.Sprintf("health: %v", err))
		return
	}
	defer func() { _ = resp.Body.Close() }()
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	if readErr != nil {
		r.Errors = append(r.Errors, fmt.Sprintf("health read: %v", readErr))
	}
	r.HealthStatus = fmt.Sprintf("%d", resp.StatusCode)
	if resp.StatusCode == http.StatusTooManyRequests {
		// A 429 means the backend is alive but throttling; annotate so the
		// report does not read as an outright failure.
		r.Errors = append(
			r.Errors,
			"health: rate-limited (429); backend alive but throttling",
		)
	}
	// Pretty-print JSON health body for readability.
	var parsed map[string]any
	if json.Unmarshal(body, &parsed) == nil {
		if pretty, err := json.MarshalIndent(parsed, "", "  "); err == nil {
			r.HealthBody = truncate(string(pretty), 2000)
		} else {
			r.HealthBody = truncate(string(body), 1000)
		}
	} else {
		r.HealthBody = truncate(string(body), 1000)
	}
}

func collectConfig(r *Report, state config.State) {
	redacted := state
	if redacted.JWTSecret != "" {
		redacted.JWTSecret = "[REDACTED]"
	}
	if redacted.SettingsKey != "" {
		redacted.SettingsKey = "[REDACTED]"
	}
	if redacted.MasterKey != "" {
		redacted.MasterKey = "[REDACTED]"
	}
	if redacted.PostgresPassword != "" {
		redacted.PostgresPassword = "[REDACTED]"
	}
	if redacted.CursorSecret != "" {
		redacted.CursorSecret = "[REDACTED]"
	}
	if b, err := json.MarshalIndent(redacted, "", "  "); err == nil { //nolint:gosec // G117: every secret-bearing State field (JWTSecret, SettingsKey, MasterKey, PostgresPassword, CursorSecret) is replaced with [REDACTED] above before marshalling
		r.ConfigRedacted = string(b)
	}
}

func collectInfra(ctx context.Context, r *Report, info docker.Info, state config.State, safeDir string, pathErr error) {
	var composePath string
	if pathErr == nil {
		composePath = checkComposeFile(ctx, r, info, safeDir)
	}
	if r.ContainerPS != "" {
		r.ContainerSummary = parseContainerDetails(r.ContainerPS)
	}
	if !hasRunningContainers(r.ContainerSummary) {
		r.PortConflicts = checkPorts(ctx, state.BackendPort, state.WebPort)
	}
	if info.DockerPath != "" {
		r.ImageStatus = checkImages(ctx, info.DockerPath, state.ImageTag, state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault(), composePath, state.VerifiedDigests)
	}
}

// FormatText returns a human-readable text report.
func (r Report) FormatText() string {
	var b strings.Builder
	b.WriteString("=== SynthOrg Diagnostic Report ===\n\n")
	fmt.Fprintf(&b, "Timestamp: %s\n", r.Timestamp)
	fmt.Fprintf(&b, "OS:        %s/%s\n", r.OS, r.Arch)
	fmt.Fprintf(&b, "CLI:       %s (%s)\n", r.CLIVersion, r.CLICommit)
	fmt.Fprintf(&b, "Docker:    %s\n", r.DockerVersion)
	fmt.Fprintf(&b, "Compose:   %s\n\n", r.ComposeVersion)

	fmt.Fprintf(&b, "--- Health ---\nStatus: %s\n%s\n\n", r.HealthStatus, r.HealthBody)
	r.formatComposeSection(&b)
	r.formatInfraSection(&b)
	fmt.Fprintf(&b, "--- Config (redacted) ---\n%s\n\n", r.ConfigRedacted)
	fmt.Fprintf(&b, "--- Disk ---\n%s\n\n", r.DiskInfo)
	formatList(&b, "Errors", r.Errors)

	return b.String()
}

func (r Report) formatComposeSection(b *strings.Builder) {
	b.WriteString("--- Compose File ---\n")
	if !r.ComposeFileExists {
		b.WriteString("Not found\n\n")
		return
	}
	fmt.Fprintf(b, "Exists: yes  Valid: %s\n\n", composeValidityLabel(r.ComposeFileValid))
}

// composeValidityLabel renders the tri-state validity flag (nil = not
// checked, *true = yes, *false = no) as a display string.
func composeValidityLabel(valid *bool) string {
	switch {
	case valid == nil:
		return "not checked"
	case *valid:
		return "yes"
	default:
		return "no"
	}
}

func (r Report) formatInfraSection(b *strings.Builder) {
	if len(r.ContainerSummary) > 0 {
		b.WriteString("--- Container Summary ---\n")
		for _, c := range r.ContainerSummary {
			line := fmt.Sprintf("  %s: %s", c.Name, c.State)
			if c.Health != "" {
				line += fmt.Sprintf(" (%s)", c.Health)
			}
			fmt.Fprintf(b, "%s\n", line)
		}
		b.WriteString("\n")
	}
	formatBulletList(b, "Port Conflicts", r.PortConflicts)
	formatBulletList(b, "Docker Images", r.ImageStatus)
}

func formatBulletList(b *strings.Builder, title string, items []string) {
	if len(items) == 0 {
		return
	}
	fmt.Fprintf(b, "--- %s ---\n", title)
	for _, s := range items {
		fmt.Fprintf(b, "  - %s\n", s)
	}
	b.WriteString("\n")
}

func formatList(b *strings.Builder, title string, items []string) {
	if len(items) == 0 {
		return
	}
	fmt.Fprintf(b, "--- %s ---\n", title)
	for _, e := range items {
		fmt.Fprintf(b, "  - %s\n", e)
	}
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "\n... (truncated)"
}

// composeFileNames are the default Compose file names in search order.
// Matches Docker Compose's documented preference: .yaml before .yml.
var composeFileNames = []string{
	"compose.yaml", "compose.yml",
	"docker-compose.yaml", "docker-compose.yml",
}

// checkComposeFile verifies that a compose file exists and is valid.
// Returns the resolved compose file path (empty if not found).
func checkComposeFile(ctx context.Context, r *Report, info docker.Info, dataDir string) string {
	for _, name := range composeFileNames {
		composePath := filepath.Join(dataDir, name)
		if _, err := os.Stat(composePath); err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			r.Errors = append(r.Errors, fmt.Sprintf("compose: %s: %v", composePath, err))
			continue
		}
		r.ComposeFileExists = true
		if info.DockerPath != "" {
			valid := docker.ComposeExec(ctx, info, dataDir, "config", "--quiet") == nil
			r.ComposeFileValid = &valid
		}
		return composePath
	}
	return ""
}

// checkPorts tests whether configured ports are already bound.
func checkPorts(ctx context.Context, backendPort, webPort int) []string {
	dialer := net.Dialer{Timeout: 1 * time.Second}
	var conflicts []string
	for _, p := range []struct {
		name string
		port int
	}{
		{"backend", backendPort},
		{"web", webPort},
	} {
		addr := fmt.Sprintf("127.0.0.1:%d", p.port)
		conn, err := dialer.DialContext(ctx, "tcp", addr)
		if err == nil {
			_ = conn.Close()
			conflicts = append(conflicts, fmt.Sprintf("port %d (%s) is already in use", p.port, p.name))
		}
	}
	return conflicts
}

// checkImages reports whether required Docker images exist locally.
// It reads the actual compose file to get the image references (which
// may be digest-pinned), falling back to verified digests from state,
// then to tag-based lookup if neither is available.
func checkImages(ctx context.Context, dockerPath, imageTag string, sandbox, fineTuning bool, fineTuneVariant, composePath string, verifiedDigests map[string]string) []string {
	// Build a map of service name -> image ref from the compose file.
	composeRefs := parseComposeImageRefs(composePath)

	var status []string
	for _, name := range images.ServiceNames(sandbox, fineTuning, fineTuneVariant) {
		// Priority: compose file ref > verified digest > tag-based fallback.
		image := composeRefs[name]
		if image == "" {
			image = images.RefForService(name, imageTag, verifiedDigests)
		}
		id, err := images.InspectID(ctx, dockerPath, image)
		switch {
		case err != nil:
			status = append(status, fmt.Sprintf("%s: inspect failed: %v", image, err))
		case id == "":
			status = append(status, fmt.Sprintf("%s: not pulled", image))
		default:
			status = append(status, fmt.Sprintf("%s: available", image))
		}
	}
	return status
}

// composeFile is the minimal subset of a Docker Compose file needed to
// extract image references. Only the services.*.image field is used.
type composeFile struct {
	Services map[string]struct {
		Image string `yaml:"image"`
	} `yaml:"services"`
}

// parseComposeImageRefs extracts image references from a compose file
// using YAML parsing. Returns a map of service name (backend, web,
// sandbox) to full image ref for SynthOrg images only.
//
// All errors (missing file, unreadable, bad YAML) are silently ignored --
// the caller falls back to images.RefForService when no compose ref is
// found. This is intentional: diagnostics must never fail due to a
// corrupt compose file.
func parseComposeImageRefs(composePath string) map[string]string {
	refs := make(map[string]string)
	if composePath == "" {
		return refs
	}

	// Canonicalize the path before reading. The composePath is always
	// constructed internally (filepath.Join of the data dir), never from
	// direct user input, so no adversarial traversal is possible.
	cleaned := filepath.Clean(composePath)
	if _, err := os.Stat(cleaned); err != nil {
		return refs
	}

	data, err := os.ReadFile(cleaned)
	if err != nil {
		return refs
	}

	var cf composeFile
	if err := yaml.Unmarshal(data, &cf); err != nil {
		return refs
	}

	prefix := images.RepoPrefix()
	for svcName, svc := range cf.Services {
		if !strings.HasPrefix(svc.Image, prefix) {
			continue
		}
		// Validate structural format: must contain a tag (:) or digest (@)
		// separator after the prefix to be a well-formed image reference.
		suffix := svc.Image[len(prefix):]
		if !strings.Contains(suffix, ":") && !strings.Contains(suffix, "@") {
			continue
		}
		refs[svcName] = svc.Image
	}
	return refs
}

// parseContainerDetails parses docker compose ps --format json output.
// Tries JSON array first, falls back to NDJSON (one object per line).
func parseContainerDetails(psJSON string) []ContainerDetail {
	// Try JSON array first (newer Compose versions).
	var details []ContainerDetail
	if err := json.Unmarshal([]byte(psJSON), &details); err == nil {
		filtered := details[:0]
		for _, d := range details {
			if d.Name != "" {
				filtered = append(filtered, d)
			}
		}
		return filtered
	}

	// Fallback: NDJSON (one JSON object per line).
	for _, line := range strings.Split(psJSON, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var d ContainerDetail
		if err := json.Unmarshal([]byte(line), &d); err != nil {
			continue
		}
		if d.Name != "" {
			details = append(details, d)
		}
	}
	return details
}

// hasRunningContainers returns true if any container is in "running" state.
func hasRunningContainers(details []ContainerDetail) bool {
	for _, d := range details {
		if d.State == "running" {
			return true
		}
	}
	return false
}

// diskInfo returns disk usage for the given path using native Go syscalls.
// Platform-specific implementations are in disk_unix.go and disk_windows.go.
func diskInfo(_ context.Context, dataDir string) string {
	total, free, err := diskUsage(dataDir)
	if err != nil {
		return fmt.Sprintf("unavailable: %v", err)
	}
	used := total - free
	pct := 0.0
	if total > 0 {
		pct = float64(used) / float64(total) * 100
	}
	return fmt.Sprintf("Total: %s  Used: %s  Free: %s  (%.0f%% used)",
		humanBytes(total), humanBytes(used), humanBytes(free), pct)
}

func humanBytes(b uint64) string {
	const (
		unit     = 1024
		suffixes = "KMGTPE"
	)
	if b < unit {
		return fmt.Sprintf("%d B", b)
	}
	div, exp := uint64(unit), 0
	for n := b / unit; n >= unit && exp < len(suffixes)-1; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(b)/float64(div), suffixes[exp])
}
