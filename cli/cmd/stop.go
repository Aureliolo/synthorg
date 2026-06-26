package cmd

import (
	"fmt"
	"strconv"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/runlock"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	stopTimeout string
	stopVolumes bool
)

var stopCmd = &cobra.Command{
	Use:   "stop",
	Short: "Stop the SynthOrg stack",
	Long: `Stop every container in the SynthOrg compose stack.

Sends SIGTERM and waits for the configured graceful shutdown
window before falling back to SIGKILL. Pass --timeout to override
the wait, or --volumes to also remove named volumes once the stack
is down (destroys persisted data; pair with 'synthorg backup
create' first).`,
	Example: `  synthorg stop                # graceful shutdown
  synthorg stop --timeout 60s  # custom shutdown timeout
  synthorg stop --volumes      # stop and remove volumes`,
	RunE: runStop,
}

func init() {
	stopCmd.Flags().StringVarP(&stopTimeout, "timeout", "t", "", "graceful shutdown timeout (e.g. 30s, 1m)")
	stopCmd.Flags().BoolVar(&stopVolumes, "volumes", false, "also remove named volumes")
	stopCmd.GroupID = "core"
	rootCmd.AddCommand(stopCmd)
}

func runStop(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)

	// Validate the --timeout flag first so a usage error (exit 2)
	// surfaces before any config load or docker detection, mirroring
	// start.go's flag-validation-first ordering. Doing this last meant
	// `synthorg stop --timeout bogus` only failed after detecting
	// docker -- expensive work wasted on an input we could reject up
	// front.
	downArgs, err := buildDownArgs()
	if err != nil {
		return fmt.Errorf("building docker compose down args: %w", err)
	}

	// Confirm Docker is reachable BEFORE any config load, path resolution,
	// or lock acquisition (mirrors start.go::startContainers): a missing
	// Docker is an exit-4 failure, so detecting it up front avoids
	// acquiring the lifecycle lock and doing filesystem work we cannot use.
	info, err := docker.Detect(ctx)
	if err != nil {
		return fmt.Errorf("detecting docker: %w", err)
	}

	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	safeDir, err := safeStateDir(state)
	if err != nil {
		return fmt.Errorf("resolving data directory: %w", err)
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	// Hold the lifecycle lock across the `compose down` so a concurrent start
	// or update-restart cannot bring the stack back up mid-stop.
	lock, err := runlock.Acquire(ctx, safeDir)
	if err != nil {
		return err
	}
	defer func() {
		if rerr := lock.Release(); rerr != nil {
			errOut.Warn(fmt.Sprintf("could not release lifecycle lock: %v", rerr))
		}
	}()
	// Confirm compose.yml exists INSIDE the lock: checking before the lock
	// left a TOCTOU window where a concurrent wipe/uninstall could delete it
	// before composeRunQuiet ran. assertComposeExists also surfaces
	// non-ErrNotExist stat errors (e.g. permission denied) the inline check
	// previously dropped.
	if err := assertComposeExists(safeDir); err != nil {
		return err
	}

	sp := out.StartSpinner("Stopping containers...")
	if err := composeRunQuiet(ctx, info, safeDir, downArgs...); err != nil {
		sp.Error("Failed to stop containers")
		return fmt.Errorf("stopping containers: %w", err)
	}
	sp.Success("SynthOrg stopped")

	// Surface the data-loss warning (or the preserved-data guidance)
	// BEFORE the restart hint so a `--volumes` run does not lead with a
	// reassuring "restart" line ahead of "all your data is gone".
	if stopVolumes {
		out.Warn("Volumes removed -- all persistent data (database, memory) has been deleted.")
	} else {
		out.HintGuidance("Persistent data preserved. Use --volumes to also remove database and memory data.")
	}
	out.HintNextStep("Run 'synthorg start' to restart.")

	return nil
}

func buildDownArgs() ([]string, error) {
	args := []string{"down"}
	if stopTimeout != "" {
		dur, parseErr := time.ParseDuration(stopTimeout)
		if parseErr != nil {
			return nil, fmt.Errorf("invalid --timeout %q: %w", stopTimeout, parseErr)
		}
		if dur < 0 {
			return nil, fmt.Errorf("invalid --timeout %q: must be non-negative", stopTimeout)
		}
		if dur%time.Second != 0 {
			return nil, fmt.Errorf("invalid --timeout %q: must be a whole number of seconds", stopTimeout)
		}
		args = append(args, "--timeout", strconv.Itoa(int(dur.Seconds())))
	}
	if stopVolumes {
		args = append(args, "--volumes")
	}
	return args, nil
}
