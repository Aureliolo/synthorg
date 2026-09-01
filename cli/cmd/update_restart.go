package cmd

import (
	"context"
	"fmt"
	"io"
	"strings"
	"time"

	"charm.land/huh/v2"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/runlock"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// performRestart stops, restarts, and health-checks containers.
func performRestart(ctx context.Context, out io.Writer, info docker.Info, safeDir string, state config.State, uiOpts ui.Options) (bool, error) {
	uiOut := ui.NewUIWithOptions(out, uiOpts)

	// Hold the lifecycle lock across the whole stop+start so a concurrent
	// `synthorg start` cannot bring the stack up in the window between this
	// down and the up below (a split-brain race on the named volumes). The
	// post-down `ps -q` assertion further down then runs under the lock and is
	// therefore reliable rather than point-in-time.
	lock, err := runlock.Acquire(ctx, safeDir)
	if err != nil {
		return false, err
	}
	defer func() {
		if rerr := lock.Release(); rerr != nil {
			uiOut.Warn(fmt.Sprintf("could not release lifecycle lock: %v", rerr))
		}
	}()

	// Re-check under the lock. The caller's running-state check happened before
	// this lock was held, so a concurrent stop/wipe (which holds the same lock)
	// could have brought the stack down while we were prompting. Restarting now
	// would resurrect a stack another command intentionally stopped, so honour
	// that and skip. A query error fails open to the restart (prior behaviour).
	psOut, psErr := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "-q")
	// The compose query resolves to the declared project, so an install
	// still running under the directory-derived one reads as stopped here.
	// Skipping on that would leave the old stack running and never reach
	// the migration below. DISPOSABLE with volume_migrate.go.
	if psErr == nil && strings.TrimSpace(psOut) == "" &&
		!legacyProjectHasContainers(ctx, info, safeDir) {
		uiOut.Step("Containers already stopped; skipping restart.")
		return false, nil
	}

	if err := stopAndVerifyDown(ctx, uiOut, info, safeDir); err != nil {
		return false, err
	}

	// DISPOSABLE: see volume_migrate.go. An update is the likeliest moment
	// for an install to meet the renamed project for the first time, since
	// it is what regenerates the compose file that declares the name.
	if err := migrateLegacyProjectVolumes(ctx, info, safeDir, uiOut); err != nil {
		return false, err
	}

	sp := uiOut.StartSpinner("Starting containers...")
	if err := composeRunQuiet(ctx, info, safeDir, "up", "-d"); err != nil {
		sp.Error("Failed to start containers")
		return false, fmt.Errorf("restarting containers: %w", err)
	}
	sp.Success("Containers started")

	return waitAndAnnounceRestart(ctx, uiOut, state), nil
}

// stopAndVerifyDown brings the compose project down and asserts it is fully
// stopped before the caller restarts it. `compose down` can report success
// while a container lingers (slow stop, an external replica); a subsequent
// `up -d` against a partially-live project races the leftover container and
// can leave a stale instance bound to the published ports, so a non-empty
// `ps -q` here is a hard error that makes the operator clear the stragglers
// rather than starting a split-brain stack.
func stopAndVerifyDown(ctx context.Context, uiOut *ui.UI, info docker.Info, safeDir string) error {
	sp := uiOut.StartSpinner("Stopping containers...")
	if err := composeRunQuiet(ctx, info, safeDir, "down"); err != nil {
		sp.Error("Failed to stop containers")
		return fmt.Errorf("stopping containers: %w", err)
	}
	psOut, psErr := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "-q")
	if psErr != nil {
		// Fail closed: a ps query error means the stack state is unknown, so
		// we cannot confirm it is down. Proceeding to `up -d` here would risk
		// a split-brain stack, exactly what this assertion guards against.
		sp.Error("Could not verify containers stopped")
		return fmt.Errorf("verifying compose is fully stopped: %w", psErr)
	}
	if strings.TrimSpace(psOut) != "" {
		sp.Error("Containers still running after stop")
		return fmt.Errorf(
			"compose down reported success but containers are still running; " +
				"stop them before restarting",
		)
	}
	sp.Success("Containers stopped")
	return nil
}

// restartHealthTimeout resolves the post-restart readiness budget from the
// --timeout flag, falling back to the configured default when the flag is
// absent or unparseable (an unparseable value should not silently become 0).
func restartHealthTimeout() time.Duration {
	if d, err := time.ParseDuration(updateTimeout); err == nil && d > 0 {
		return d
	}
	return config.DefaultHealthWaitTimeout
}

// waitAndAnnounceRestart waits for the restarted backend to become healthy and,
// on success, prints the same "Ready" banner `start` does. It returns true only
// when the backend passed the health check.
func waitAndAnnounceRestart(ctx context.Context, uiOut *ui.UI, state config.State) bool {
	sp := uiOut.StartSpinner("Waiting for backend to become healthy...")
	// Liveness, not readiness: an optional dependency that is not back yet
	// (a local model server, say) must not read as a failed update. The
	// dependency check below reports that separately without failing.
	healthURL := config.APIURL(state.BackendPort, "/healthz")
	healthTimeout := restartHealthTimeout()
	tun := GetGlobalOpts(ctx).Tunables
	if err := health.WaitForHealthy(ctx, healthURL, healthTimeout, tun.HealthPollInterval, tun.HealthInitialDelay); err != nil {
		sp.Warn(fmt.Sprintf("Health check did not pass after restart: %v", err))
		return false
	}
	sp.Success("Backend healthy")
	dependenciesReady := warnIfDependenciesDegraded(ctx, state, uiOut)
	uiOut.Blank()
	// Mirror the banner that `start` prints, title included, so a post-update
	// restart surfaces the same dashboard + API endpoints and never claims
	// "Ready" directly under a warning that a dependency is not. localhost is
	// correct: the restarted stack publishes these ports on the operator's
	// own host.
	readyLines := []string{
		fmt.Sprintf("%-16s%s", "Dashboard", fmt.Sprintf("http://localhost:%d", state.WebPort)),
		fmt.Sprintf("%-16s%s", "API", fmt.Sprintf("http://localhost:%d", state.BackendPort)),
	}
	boxTitle := "Started"
	if dependenciesReady {
		boxTitle = "Ready"
	}
	uiOut.Box(boxTitle, readyLines)
	uiOut.Blank()
	uiOut.Section(fmt.Sprintf("Open http://localhost:%d", state.WebPort))
	return true
}

// confirmRestart prompts the operator to restart running containers after an
// image update. It raises errUpdateCancelled on a dismissal like every other
// prompt in this flow: it runs after the CLI update and the image pull have
// both succeeded, so reporting a dismissal as a failed update would be wrong
// about the two steps that did work.
func confirmRestart(ctx context.Context) (bool, error) {
	restart := true // default yes
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title("Containers are running. Restart with new images?").
				Value(&restart),
		),
	)
	if err := runUpdateConfirm(ctx, form); err != nil {
		return false, err
	}
	return restart, nil
}
