package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
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
// echoed, after lastProbe has already reduced it to its final line. Both
// probes in this stack put the useful part last: a Python traceback ends
// with the exception, and wget ends with the HTTP status. The cap is for
// the pathological case of a single very long line.
const probeOutputLimit = 400

// containerInspect is the subset of `docker inspect` this diagnostic reads.
// Fields absent from an older daemon's payload decode to their zero value,
// which every consumer below treats as "not reported".
type containerInspect struct {
	Name         string         `json:"Name"`
	RestartCount int            `json:"RestartCount"`
	State        containerState `json:"State"`
	Config       struct {
		// Healthcheck is the effective config, so StartPeriod is read from
		// the container the daemon actually created rather than duplicated
		// as a constant here. The Dockerfile stays the single source of
		// truth for the budget.
		Healthcheck *struct {
			StartPeriod int64 `json:"StartPeriod"` // nanoseconds
		} `json:"Healthcheck"`
	} `json:"Config"`
}

// containerState mirrors `.State` from the inspect payload.
type containerState struct {
	Status    string           `json:"Status"`
	ExitCode  int              `json:"ExitCode"`
	StartedAt string           `json:"StartedAt"`
	OOMKilled bool             `json:"OOMKilled"`
	Health    *containerHealth `json:"Health"`
}

// containerHealth mirrors `.State.Health`, absent on a container with no
// healthcheck.
type containerHealth struct {
	Status        string             `json:"Status"`
	FailingStreak int                `json:"FailingStreak"`
	Log           []healthProbeEntry `json:"Log"`
}

// healthProbeEntry is one recorded probe run.
type healthProbeEntry struct {
	ExitCode int    `json:"ExitCode"`
	Output   string `json:"Output"`
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
		// Truncate on a rune boundary: a probe message can carry a
		// non-ASCII path or quote, and slicing mid-rune would emit
		// invalid UTF-8 into the terminal at the exact moment the
		// operator is reading a failure.
		out = strings.ToValidUTF8(out[:probeOutputLimit], "") + "..."
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
		// Only claim remaining budget when the elapsed time is actually
		// known: with an unusable StartedAt, uptime() is 0 and this would
		// otherwise report the full grace period for a container that has
		// been up for minutes.
		if c.State.Health.Status == "starting" {
			if grace, up := c.startPeriod(), c.uptime(now); grace > 0 && up > 0 {
				fmt.Fprintf(&b, " (still inside its %s start period, %s left)",
					grace.Round(time.Second), max(grace-up, 0).Round(time.Second))
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

// containerIDPattern matches a Docker container ID as `compose ps
// --quiet` emits it. Compose writes warnings (orphan containers, obsolete
// attributes) to stderr, and ComposeExecOutput merges streams, so the
// output cannot be trusted as a bare ID list: an unfiltered token becomes
// a positional argument, and one beginning with `-` would be parsed as a
// Docker persistent flag (`-H` retargets the daemon entirely).
var containerIDPattern = regexp.MustCompile(`^[0-9a-f]{12,64}$`)

// inspectFormat projects only the fields summarise reads. `{{json .}}`
// would materialise the whole container config, including every secret in
// Config.Env (master key, settings key, JWT secret, Postgres DSN), into
// CLI memory for no reason.
//
// The object is assembled from literal text plus `json` calls rather than
// with a map-building helper: `docker inspect --format` exposes only
// Docker's own small FuncMap (json, split, join, title, lower, upper, pad,
// truncate, println) on top of text/template's builtins. Anything else --
// `dict` in particular, which comes from Sprig and reads as though it
// should work -- fails to PARSE, so the command errors out and every
// diagnostic here silently degrades to nothing.
const inspectFormat = `{"Name":{{json .Name}},` +
	`"RestartCount":{{json .RestartCount}},` +
	`"State":{{json .State}},` +
	`"Config":{"Healthcheck":{{json .Config.Healthcheck}}}}`

// composeContainerIDs extracts the container IDs from `compose ps --quiet`
// output, discarding everything else. Anything that is not an ID is
// discarded rather than passed through, because these tokens become argv
// for `docker inspect`.
func composeContainerIDs(raw string) []string {
	fields := strings.Fields(raw)
	ids := make([]string, 0, len(fields))
	for _, field := range fields {
		if containerIDPattern.MatchString(field) {
			ids = append(ids, field)
		}
	}
	return ids
}

// parseInspectLines decodes the JSON-lines payload `docker inspect`
// produces, skipping any line it cannot decode. Returns the containers it
// understood plus the number it could not, so a single malformed line
// degrades to one missing container rather than a blank report.
func parseInspectLines(raw string) (found []containerInspect, skipped int) {
	for line := range strings.SplitSeq(raw, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var c containerInspect
		if json.Unmarshal([]byte(line), &c) != nil {
			skipped++
			continue
		}
		found = append(found, c)
	}
	return found, skipped
}

// inspectComposeContainers returns the inspect payload for every container
// in the stack. It never returns a fatal error: this runs while reporting a
// failure the caller already has, so the worst outcome must be "no extra
// detail", never a second failure stacked on the first. The error it does
// return is advisory, for the caller to surface as a warning, because
// silently producing nothing is indistinguishable from "there were no
// containers" at exactly the moment the operator needs to know which.
func inspectComposeContainers(ctx context.Context, info docker.Info, safeDir string) ([]containerInspect, error) {
	out, err := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "--all", "--quiet")
	if err != nil {
		return nil, fmt.Errorf("compose ps: %w", err)
	}
	ids := composeContainerIDs(out)
	if len(ids) == 0 {
		return nil, nil
	}
	dockerBin := info.DockerPath
	if dockerBin == "" {
		dockerBin = "docker"
	}
	// `--` terminates flag parsing so no ID can ever be read as a flag.
	args := append([]string{"inspect", "--format", inspectFormat, "--"}, ids...)
	raw, err := docker.RunCmd(ctx, dockerBin, args...)
	if err != nil {
		return nil, fmt.Errorf("docker inspect: %w", err)
	}
	found, skipped := parseInspectLines(raw)
	if skipped != 0 {
		return found, fmt.Errorf("%d container(s) returned unparsable inspect output", skipped)
	}
	return found, nil
}

// composeUpProgressInterval is how often the start spinner refreshes its
// elapsed-time and health line. `web` gates on the backend's
// service_healthy condition, so `compose up -d` blocks for the whole cold
// boot: on a first run with every migration applying that is minutes, and
// an unchanging spinner over that window is indistinguishable from a hang.
const composeUpProgressInterval = 10 * time.Second

// crashLoopRestartThreshold is how many restarts of one container mean the
// stack is failing rather than booting. Two is a deliberate floor: a single
// restart is recoverable (a dependency that came up a moment late), while a
// second means the same failure has now repeated.
const crashLoopRestartThreshold = 2

// composeUpWithProgress runs `compose up -d` while reporting elapsed time
// and the backend's health status on the spinner, so a long first boot
// reads as progress rather than as a stall.
//
// It does NOT impose its own deadline. Compose already ends the dependency
// wait when a container reports healthy or unhealthy, and a second, shorter
// CLI timeout could only cut short a boot that was going to succeed.
//
// It does terminate on the one case compose cannot: a crash loop. A
// restart resets the health start period, so a container failing faster
// than the start period is perpetually "starting" and never becomes
// unhealthy -- compose would wait on that dependency indefinitely. The
// restart count is the signal that distinguishes it from a slow boot, and
// aborting on it is a terminal condition tied to the actual failure rather
// than to a clock.
func composeUpWithProgress(ctx context.Context, info docker.Info, safeDir string, sp *ui.Spinner) error {
	upCtx, cancelUp := context.WithCancel(ctx)
	defer cancelUp()

	result := make(chan error, 1)
	go func() {
		result <- composeRunQuiet(upCtx, info, safeDir, "up", "-d")
	}()

	started := time.Now()
	ticker := time.NewTicker(composeUpProgressInterval)
	defer ticker.Stop()
	for {
		select {
		case err := <-result:
			return err
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			message, fatal := composeUpTick(ctx, info, safeDir, time.Since(started))
			if fatal != nil {
				// Stop the compose wait before returning: it would
				// otherwise keep blocking on a dependency that is never
				// going to report healthy.
				cancelUp()
				<-result
				return fatal
			}
			sp.Update(message)
		}
	}
}

// composeUpTick inspects the stack once and returns the progress line to
// show, plus a fatal error when the stack is crash-looping rather than
// booting. Inspection is best-effort: during the first seconds no container
// exists yet, and a failure to inspect reports elapsed time alone rather
// than aborting a start that may be fine.
func composeUpTick(
	ctx context.Context, info docker.Info, safeDir string, elapsed time.Duration,
) (message string, fatal error) {
	base := fmt.Sprintf("Starting containers... (%s elapsed)", elapsed.Round(time.Second))
	inspectCtx, cancel := context.WithTimeout(ctx, GetGlobalOpts(ctx).Tunables.StatusDockerTimeout)
	defer cancel()
	containers, err := inspectComposeContainers(inspectCtx, info, safeDir)
	if err != nil && len(containers) == 0 {
		return base, nil
	}
	// A partial result is still usable, and discarding it would defeat
	// the abort below: one container the daemon described in a way this
	// binary cannot decode would leave a crash loop undetected, and the
	// dependency wait has no other terminal condition.

	if looping := crashLoopingContainer(containers); looping != nil {
		return base, fmt.Errorf(
			"%s has restarted %d times and is not becoming healthy: it is "+
				"crash-looping, not booting. Each restart resets the health "+
				"start period, so waiting longer cannot resolve it",
			strings.TrimPrefix(looping.Name, "/"), looping.RestartCount,
		)
	}

	for _, c := range containers {
		if !strings.Contains(c.Name, "backend") || c.State.Health == nil {
			continue
		}
		if c.State.Health.Status == "starting" {
			return base + ", backend still booting (first run applies all migrations)", nil
		}
		return fmt.Sprintf("%s, backend %s", base, c.State.Health.Status), nil
	}
	return base, nil
}

// crashLoopingContainer returns the first container restarting repeatedly
// without reaching a healthy state, or nil. A container that HAS gone
// healthy is excluded: restarts behind it are a different problem and not
// one that can stall the dependency wait.
func crashLoopingContainer(containers []containerInspect) *containerInspect {
	for i, c := range containers {
		if c.RestartCount < crashLoopRestartThreshold {
			continue
		}
		if c.State.Health != nil && c.State.Health.Status == "healthy" {
			continue
		}
		return &containers[i]
	}
	return nil
}

// reportContainersBeforeTeardown records which containers a teardown is
// about to stop, and the health each was in.
//
// `compose down` terminates with SIGTERM then SIGKILL, so a container it
// stops exits 137 -- indistinguishable afterwards from an OOM kill, which
// is exactly the confusion that made an operator-initiated stop look like
// a crash in an earlier investigation. Recording it at the moment of the
// decision is the only point where the two can still be told apart.
//
// Best-effort and non-fatal in every respect: a teardown must proceed
// whatever the daemon says, so a failure here produces no output and no
// error rather than blocking the command.
func reportContainersBeforeTeardown(ctx context.Context, info docker.Info, safeDir string, out *ui.UI) {
	ctx, cancel := context.WithTimeout(ctx, GetGlobalOpts(ctx).Tunables.StatusDockerTimeout)
	defer cancel()

	containers, err := inspectComposeContainers(ctx, info, safeDir)
	if err != nil || len(containers) == 0 {
		return
	}
	now := time.Now()
	out.Section("Stopping these containers")
	for _, c := range containers {
		out.Step("  " + c.summarise(now))
	}
}

// reportStartFailure writes per-container state to errOut after a start
// failed, so the operator can tell a slow boot from a crash-loop without
// reaching for `docker inspect`.
//
// Bounded by its own deadline: this runs when Docker is already suspect,
// and all callers invoke it BEFORE returning the real error, so an
// unresponsive daemon must not be able to withhold that error indefinitely.
func reportStartFailure(ctx context.Context, info docker.Info, safeDir string, errOut *ui.UI) {
	ctx, cancel := context.WithTimeout(ctx, GetGlobalOpts(ctx).Tunables.StatusDockerTimeout)
	defer cancel()

	containers, err := inspectComposeContainers(ctx, info, safeDir)
	if err != nil {
		errOut.Warn(fmt.Sprintf("Could not collect container diagnostics: %v", err))
	}
	if len(containers) == 0 {
		return
	}
	now := time.Now()
	errOut.Section("Container state at failure")
	for _, c := range containers {
		errOut.Warn("  " + c.summarise(now))
	}
	if hint := startFailureHint(containers); hint != "" {
		errOut.HintError(hint)
	}
}

// startFailureHint returns the recovery advice for a failed start, or ""
// when the container states support none.
//
// This is the answer to the reported problem. Compose says "container ...
// is unhealthy", which reads as broken; a container still inside its
// health start period has not failed at all, it has not finished booting,
// and the operator's correct move is to wait rather than to start
// debugging. A crash loop is checked first because it presents the same
// way -- perpetually "starting", since each restart resets the clock --
// while being the opposite situation.
func startFailureHint(containers []containerInspect) string {
	if looping := crashLoopingContainer(containers); looping != nil {
		return "A container is restarting repeatedly rather than booting. Its last " +
			"health probe above shows why; 'synthorg logs' has the full output. " +
			"Waiting will not help: every restart resets the health start period."
	}
	for _, c := range containers {
		if c.State.Health != nil && c.State.Health.Status == "starting" {
			return "A container is still inside its health start period: it has not failed, " +
				"it has not finished booting. Re-run 'synthorg start', or watch it with " +
				"'synthorg status --watch'."
		}
	}
	return ""
}
