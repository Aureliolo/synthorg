// Package diagnostics collects system information for bug reports.
package diagnostics

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

// Report contains collected diagnostic information.
type Report struct {
	Timestamp      string `json:"timestamp"`
	OS             string `json:"os"`
	Arch           string `json:"arch"`
	CLIVersion     string `json:"cli_version"`
	CLICommit      string `json:"cli_commit"`
	DockerVersion  string `json:"docker_version,omitempty"`
	ComposeVersion string `json:"compose_version,omitempty"`
	HealthStatus   string `json:"health_status,omitempty"`
	HealthBody     string `json:"health_body,omitempty"`
	ContainerPS    string `json:"container_ps,omitempty"`
	RecentLogs     string `json:"recent_logs,omitempty"`
	ConfigRedacted string `json:"config_redacted,omitempty"`
	DiskInfo       string `json:"disk_info,omitempty"`
	Errors         []string `json:"errors,omitempty"`
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

	// Docker info.
	info, err := docker.Detect(ctx)
	if err != nil {
		r.Errors = append(r.Errors, fmt.Sprintf("docker: %v", err))
	} else {
		r.DockerVersion = info.DockerVersion
		r.ComposeVersion = info.ComposeVersion

		// Container states.
		if ps, err := docker.ComposeExecOutput(ctx, info, state.DataDir, "ps", "--format", "json"); err == nil {
			r.ContainerPS = strings.TrimSpace(ps)
		}

		// Recent logs (last 50 lines).
		if logs, err := docker.ComposeExecOutput(ctx, info, state.DataDir, "logs", "--tail", "50", "--no-color"); err == nil {
			r.RecentLogs = truncate(logs, 4000)
		}
	}

	// Health endpoint.
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/health", state.BackendPort)
	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		r.HealthStatus = "unreachable"
		r.Errors = append(r.Errors, fmt.Sprintf("health request: %v", err))
	} else if resp, err := client.Do(req); err != nil {
		r.HealthStatus = "unreachable"
		r.Errors = append(r.Errors, fmt.Sprintf("health: %v", err))
	} else {
		defer resp.Body.Close()
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
		if readErr != nil {
			r.Errors = append(r.Errors, fmt.Sprintf("health read: %v", readErr))
		}
		r.HealthStatus = fmt.Sprintf("%d", resp.StatusCode)
		r.HealthBody = truncate(string(body), 1000)
	}

	// Redacted config.
	redacted := state
	if redacted.JWTSecret != "" {
		redacted.JWTSecret = "[REDACTED]"
	}
	if b, err := json.MarshalIndent(redacted, "", "  "); err == nil {
		r.ConfigRedacted = string(b)
	}

	// Disk space for data directory (best-effort).
	r.DiskInfo = diskInfo(ctx, state.DataDir)

	return r
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
	fmt.Fprintf(&b, "--- Containers ---\n%s\n\n", r.ContainerPS)
	fmt.Fprintf(&b, "--- Config (redacted) ---\n%s\n\n", r.ConfigRedacted)
	fmt.Fprintf(&b, "--- Disk ---\n%s\n\n", r.DiskInfo)
	fmt.Fprintf(&b, "--- Recent Logs (may contain sensitive data — review before sharing) ---\n%s\n\n", r.RecentLogs)

	if len(r.Errors) > 0 {
		fmt.Fprintf(&b, "--- Errors ---\n")
		for _, e := range r.Errors {
			fmt.Fprintf(&b, "  - %s\n", e)
		}
	}

	return b.String()
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "\n... (truncated)"
}

func diskInfo(ctx context.Context, dataDir string) string {
	var name string
	var args []string

	// Check the partition containing the data directory rather than root.
	target := dataDir
	if target == "" {
		target = "/"
	}

	switch runtime.GOOS {
	case "windows":
		// Use fsutil on the drive letter of the data dir (or C: as fallback).
		drive := "C:"
		if len(target) >= 2 && target[1] == ':' {
			drive = target[:2]
		}
		name = "fsutil"
		args = []string{"volume", "diskfree", drive}
	default:
		name = "df"
		args = []string{"-h", target}
	}
	cmd := exec.CommandContext(ctx, name, args...)
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		return fmt.Sprintf("unavailable: %v", err)
	}
	return strings.TrimSpace(out.String())
}
