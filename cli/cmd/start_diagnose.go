package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// Compose reports a failed `up` as a bare "dependency failed to start:
// container ... is unhealthy". That sentence cannot distinguish a container
// still working through a slow cold boot from one crash-looping on a config
// error, and both are common on a first run. These helpers turn the failure
// into the facts an operator would otherwise have to reach for `docker
// inspect` to get: how long it has been running, whether it is still inside
// its own health start period, how many times it has restarted, and what
// the last probe actually said.

// probeOutputLimit caps how much of a health probe's captured output is
// echoed. A failing probe in this image prints a full Python traceback;
// the first lines carry the cause and the rest is stack frames.
const probeOutputLimit = 400

// containerInspect is the subset of `docker inspect` this diagnostic reads.
// Fields absent from an older daemon's payload decode to their zero value,
// which every consumer below treats as "not reported".
type containerInspect struct {
	Name         string `json:"Name"`
	RestartCount int    `json:"RestartCount"`
	State        struct {
		Status    string `json:"Status"`
		ExitCode  int    `json:"ExitCode"`
		StartedAt string `json:"StartedAt"`
		OOMKilled bool   `json:"OOMKilled"`
		Health    *struct {
			Status        string `json:"Status"`
			FailingStreak int    `json:"FailingStreak"`
			Log           []struct {
				ExitCode int    `json:"ExitCode"`
				Output   string `json:"Output"`
			} `json:"Log"`
		} `json:"Health"`
	} `json:"State"`
	Config struct {
		// Healthcheck is the effective config, so StartPeriod is read from
		// the container the daemon actually created rather than duplicated
		// as a constant here. The Dockerfile stays the single source of
		// truth for the budget.
		Healthcheck *struct {
			StartPeriod int64 `json:"StartPeriod"` // nanoseconds
		} `json:"Healthcheck"`
	} `json:"Config"`
}

// startPeriod returns the container's configured health start period.
func (c containerInspect) startPeriod() time.Duration {
	if c.Config.Healthcheck == nil {
		return 0
	}
	return time.Duration(c.Config.Healthcheck.StartPeriod)
}

// uptime returns how long the container has been running, or 0 when the
// daemon reported no usable start time.
func (c containerInspect) uptime(now time.Time) time.Duration {
	started, err := time.Parse(time.RFC3339Nano, c.State.StartedAt)
	if err != nil || started.IsZero() {
		return 0
	}
	if d := now.Sub(started); d > 0 {
		return d
	}
	return 0
}

// lastProbe returns the most recent health-probe output, trimmed.
func (c containerInspect) lastProbe() string {
	if c.State.Health == nil || len(c.State.Health.Log) == 0 {
		return ""
	}
	last := c.State.Health.Log[len(c.State.Health.Log)-1]
	out := strings.TrimSpace(last.Output)
	if out == "" {
		return ""
	}
	// A Python traceback ends with the exception line, which names the
	// cause; the frames above it are noise in a start-failure summary.
	lines := strings.Split(out, "\n")
	out = strings.TrimSpace(lines[len(lines)-1])
	if len(out) > probeOutputLimit {
		out = out[:probeOutputLimit] + "..."
	}
	return out
}

// summarise renders one container's state as a single diagnostic line.
func (c containerInspect) summarise(now time.Time) string {
	name := strings.TrimPrefix(c.Name, "/")
	var b strings.Builder
	fmt.Fprintf(&b, "%s: %s", name, c.State.Status)

	if up := c.uptime(now); up > 0 {
		fmt.Fprintf(&b, ", running %s", up.Round(time.Second))
	}
	if c.State.Health != nil {
		fmt.Fprintf(&b, ", health %s", c.State.Health.Status)
		// "starting" is the answer to the question compose's message
		// raises: the container has not failed, it has not finished
		// booting. Saying how much budget is left makes the difference
		// between "wait" and "investigate" obvious.
		if c.State.Health.Status == "starting" {
			if grace := c.startPeriod(); grace > 0 {
				remaining := max(grace-c.uptime(now), 0)
				fmt.Fprintf(&b, " (still inside its %s start period, %s left)",
					grace.Round(time.Second), remaining.Round(time.Second))
			}
		}
	}
	// A restart resets the clock the health start period runs on, so a
	// non-zero count explains an otherwise inexplicable "still starting"
	// on a container that has been up for minutes.
	if c.RestartCount > 0 {
		fmt.Fprintf(&b, ", restarted %d time(s)", c.RestartCount)
	}
	if c.State.Status == "exited" {
		fmt.Fprintf(&b, ", exit code %d", c.State.ExitCode)
		if c.State.OOMKilled {
			b.WriteString(" (OOM-killed)")
		}
	}
	if probe := c.lastProbe(); probe != "" {
		fmt.Fprintf(&b, "\n    last health probe: %s", probe)
	}
	return b.String()
}

// inspectComposeContainers returns the inspect payload for every container
// in the stack. Best-effort throughout: this runs while reporting a failure
// the caller already has, so any error here means "no extra detail", never
// a second failure stacked on the first.
func inspectComposeContainers(ctx context.Context, info docker.Info, safeDir string) []containerInspect {
	out, err := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "--all", "--quiet")
	if err != nil {
		return nil
	}
	ids := strings.Fields(out)
	if len(ids) == 0 {
		return nil
	}
	dockerBin := info.DockerPath
	if dockerBin == "" {
		dockerBin = "docker"
	}
	args := append([]string{"inspect", "--format", "{{json .}}"}, ids...)
	raw, err := docker.RunCmd(ctx, dockerBin, args...)
	if err != nil {
		return nil
	}
	var found []containerInspect
	for line := range strings.SplitSeq(raw, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var c containerInspect
		if json.Unmarshal([]byte(line), &c) != nil {
			continue
		}
		found = append(found, c)
	}
	return found
}

// reportStartFailure writes per-container state to errOut after a compose
// `up` failed, so the operator can tell a slow boot from a crash-loop
// without reaching for `docker inspect`.
func reportStartFailure(ctx context.Context, info docker.Info, safeDir string, errOut *ui.UI) {
	containers := inspectComposeContainers(ctx, info, safeDir)
	if len(containers) == 0 {
		return
	}
	now := time.Now()
	errOut.Section("Container state at failure")
	stillStarting := false
	for _, c := range containers {
		errOut.Warn("  " + c.summarise(now))
		if c.State.Health != nil && c.State.Health.Status == "starting" {
			stillStarting = true
		}
	}
	if stillStarting {
		errOut.HintError(
			"A container is still inside its health start period: it has not failed, " +
				"it has not finished booting. Re-run 'synthorg start', or watch it with " +
				"'synthorg status --watch'.")
	}
}
