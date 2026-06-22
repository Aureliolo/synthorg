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

	if err := stopAndVerifyDown(ctx, uiOut, info, safeDir); err != nil {
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
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", state.BackendPort)
	healthTimeout := restartHealthTimeout()
	if err := health.WaitForHealthy(ctx, healthURL, healthTimeout, 2*time.Second, 5*time.Second); err != nil {
		sp.Warn(fmt.Sprintf("Health check did not pass after restart: %v", err))
		return false
	}
	sp.Success("Backend healthy")
	uiOut.Blank()
	// Mirror the "Ready" banner that `start` prints so a post-update restart
	// surfaces the same dashboard + API endpoints. localhost is correct: the
	// restarted stack publishes these ports on the operator's own host.
	readyLines := []string{
		fmt.Sprintf("%-16s%s", "Dashboard", fmt.Sprintf("http://localhost:%d", state.WebPort)),
		fmt.Sprintf("%-16s%s", "API", fmt.Sprintf("http://localhost:%d", state.BackendPort)),
	}
	uiOut.Box("Ready", readyLines)
	uiOut.Blank()
	uiOut.Section(fmt.Sprintf("Open http://localhost:%d", state.WebPort))
	return true
}

// confirmRestart prompts the operator to restart running containers after an
// image update.
func confirmRestart() (bool, error) {
	restart := true // default yes
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title("Containers are running. Restart with new images?").
				Value(&restart),
		),
	)
	if err := form.Run(); err != nil {
		return false, err
	}
	return restart, nil
}
