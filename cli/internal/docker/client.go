// Package docker provides Docker and Compose detection and execution helpers.
package docker

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
)

const (
	// MinDockerVersion is the minimum supported Docker Engine version.
	MinDockerVersion = "20.10.0"
	// MinComposeVersion is the minimum supported Docker Compose version.
	MinComposeVersion = "2.0.0"
)

// Info holds detected Docker environment details.
type Info struct {
	DockerPath     string
	DockerVersion  string
	ComposeCmd     []string // exec-safe command: ["docker", "compose"] or ["docker-compose"]
	ComposePath    string   // human-readable display string
	ComposeVersion string
	ComposeV2      bool // true if using Compose V2 plugin
}

// Detect checks for Docker and Compose availability and returns diagnostic
// Info. Returns an error only if Docker itself is not found or the daemon is
// not running.
func Detect(ctx context.Context) (Info, error) {
	var info Info

	// 1. Check Docker binary.
	dockerPath, err := exec.LookPath("docker")
	if err != nil {
		return info, fmt.Errorf("docker not found on PATH: %w\n\n%s", err, InstallHint(runtime.GOOS))
	}
	info.DockerPath = dockerPath

	// 2. Verify daemon is running.
	ver, err := RunCmd(ctx, "docker", "info", "--format", "{{.ServerVersion}}")
	if err != nil {
		return info, fmt.Errorf("docker daemon is not running: %w\n\n%s", err, DaemonHint(runtime.GOOS))
	}
	info.DockerVersion = strings.TrimSpace(ver)

	// 3. Try Compose V2 plugin first, then fall back to standalone.
	if cver, err := RunCmd(ctx, "docker", "compose", "version", "--short"); err == nil {
		info.ComposeCmd = []string{"docker", "compose"}
		info.ComposePath = "docker compose"
		info.ComposeVersion = strings.TrimSpace(cver)
		info.ComposeV2 = true
	} else if cver, err := RunCmd(ctx, "docker-compose", "version", "--short"); err == nil {
		info.ComposeCmd = []string{"docker-compose"}
		info.ComposePath = "docker-compose"
		info.ComposeVersion = strings.TrimSpace(cver)
	} else {
		return info, fmt.Errorf("docker compose not found (tried V2 plugin and standalone)\n\n%s", InstallHint(runtime.GOOS))
	}

	return info, nil
}

// CheckMinVersions returns warnings for Docker/Compose versions below minimum.
func CheckMinVersions(info Info) []string {
	var warnings []string
	if !versionAtLeast(info.DockerVersion, MinDockerVersion) {
		warnings = append(warnings, fmt.Sprintf("Docker %s is below minimum %s", info.DockerVersion, MinDockerVersion))
	}
	if !versionAtLeast(info.ComposeVersion, MinComposeVersion) {
		warnings = append(warnings, fmt.Sprintf("Docker Compose %s is below minimum %s", info.ComposeVersion, MinComposeVersion))
	}
	return warnings
}

// composeArgs builds the full argument list for a compose command by prepending
// the compose sub-command parts (e.g. ["compose"]) to the caller's args.
func composeArgs(info Info, args ...string) (string, []string) {
	name := info.ComposeCmd[0]
	fullArgs := make([]string, 0, len(info.ComposeCmd)-1+len(args))
	fullArgs = append(fullArgs, info.ComposeCmd[1:]...)
	fullArgs = append(fullArgs, args...)
	return name, fullArgs
}

// ComposeExec runs a compose command, discarding stdout/stderr.
func ComposeExec(ctx context.Context, info Info, dir string, args ...string) error {
	name, fullArgs := composeArgs(info, args...)

	cmd := exec.CommandContext(ctx, name, fullArgs...)
	cmd.Dir = dir
	return cmd.Run()
}

// ComposeExecOutput runs a compose command and returns combined output.
func ComposeExecOutput(ctx context.Context, info Info, dir string, args ...string) (string, error) {
	name, fullArgs := composeArgs(info, args...)

	cmd := exec.CommandContext(ctx, name, fullArgs...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// RunCmd executes a command and returns stdout. Exported for testing.
func RunCmd(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("%w: %s", err, stderr.String())
	}
	return stdout.String(), nil
}

// InstallHint returns platform-specific Docker installation guidance.
func InstallHint(goos string) string {
	switch goos {
	case "darwin":
		return "Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/"
	case "windows":
		return "Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
	default:
		return "Install Docker Engine: https://docs.docker.com/engine/install/"
	}
}

// DaemonHint returns platform-specific guidance for starting the Docker daemon.
func DaemonHint(goos string) string {
	switch goos {
	case "darwin", "windows":
		return "Start Docker Desktop and try again."
	default:
		return "Start the Docker daemon: sudo systemctl start docker"
	}
}

// versionAtLeast returns true if got >= min using semver-like comparison.
func versionAtLeast(got, min string) bool {
	got = strings.TrimPrefix(got, "v")
	min = strings.TrimPrefix(min, "v")

	gParts := strings.SplitN(got, ".", 3)
	mParts := strings.SplitN(min, ".", 3)

	for i := range 3 {
		var g, m int
		if i < len(gParts) {
			// Strip non-numeric suffixes (e.g. "1-rc1").
			numStr := strings.FieldsFunc(gParts[i], func(r rune) bool {
				return r < '0' || r > '9'
			})
			if len(numStr) > 0 {
				g, _ = strconv.Atoi(numStr[0])
			}
		}
		if i < len(mParts) {
			m, _ = strconv.Atoi(mParts[i])
		}
		if g > m {
			return true
		}
		if g < m {
			return false
		}
	}
	return true // equal
}
