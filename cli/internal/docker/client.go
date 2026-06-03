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
	ok, err := versionAtLeast(info.DockerVersion, MinDockerVersion)
	if err != nil {
		warnings = append(warnings, fmt.Sprintf("could not parse Docker version %q: %v", info.DockerVersion, err))
	} else if !ok {
		warnings = append(warnings, fmt.Sprintf("Docker %s is below minimum %s", info.DockerVersion, MinDockerVersion))
	}
	ok, err = versionAtLeast(info.ComposeVersion, MinComposeVersion)
	if err != nil {
		warnings = append(warnings, fmt.Sprintf("could not parse Compose version %q: %v", info.ComposeVersion, err))
	} else if !ok {
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

	cmd := exec.CommandContext(ctx, name, fullArgs...) //nolint:gosec // G204: compose binary is CLI-detected (info.ComposeCmd), args internally assembled, never attacker-controlled
	cmd.Dir = dir
	return cmd.Run()
}

// ComposeExecOutput runs a compose command and returns combined output.
func ComposeExecOutput(ctx context.Context, info Info, dir string, args ...string) (string, error) {
	name, fullArgs := composeArgs(info, args...)

	cmd := exec.CommandContext(ctx, name, fullArgs...) //nolint:gosec // G204: compose binary is CLI-detected (info.ComposeCmd), args internally assembled, never attacker-controlled
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// RunCmd executes a command and returns stdout. Exported for testing.
func RunCmd(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...) //nolint:gosec // G204: all current callers pass an internally-resolved binary (info.DockerPath / "docker") and internally-assembled args
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

// parseSemverComponents extracts up to three integer components from a
// semver-like version string. The leading "v" is stripped; non-numeric
// suffixes on any component are dropped (e.g. "1.0.0-rc1" -> [1, 0, 0]);
// missing components default to 0. NON-empty components that contain
// no digit run at all (e.g. "abc.def" or "1.x.0") are rejected so a
// malformed input cannot silently coerce to 0.0.0 and be treated as
// "version 0"; empty components ("" or "1.") are accepted as 0 to
// preserve compatibility with relaxed tag schemes.
func parseSemverComponents(ver string) ([3]int, error) {
	ver = strings.TrimPrefix(ver, "v")
	parts := strings.SplitN(ver, ".", 3)
	var components [3]int
	for i, part := range parts {
		if part == "" {
			continue
		}
		numStr := strings.FieldsFunc(part, func(r rune) bool {
			return r < '0' || r > '9'
		})
		if len(numStr) == 0 {
			return [3]int{}, fmt.Errorf("invalid version component %q in %q: no digit run", part, ver)
		}
		v, err := strconv.Atoi(numStr[0])
		if err != nil {
			return [3]int{}, fmt.Errorf("invalid version component %q in %q: %w", numStr[0], ver, err)
		}
		components[i] = v
	}
	return components, nil
}

// compareSemverComponents returns -1 if a<b, 0 if a==b, +1 if a>b.
func compareSemverComponents(a, b [3]int) int {
	for i := range 3 {
		if a[i] > b[i] {
			return 1
		}
		if a[i] < b[i] {
			return -1
		}
	}
	return 0
}

// versionAtLeast returns true if got >= min using semver-like comparison.
func versionAtLeast(got, min string) (bool, error) {
	g, err := parseSemverComponents(got)
	if err != nil {
		return false, err
	}
	m, err := parseSemverComponents(min)
	if err != nil {
		return false, err
	}
	return compareSemverComponents(g, m) >= 0, nil
}
