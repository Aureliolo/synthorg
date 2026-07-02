package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	statusWatch    bool
	statusInterval string
	statusWide     bool
	statusNoTrunc  bool
	statusServices string
	statusCheck    bool
)

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show container states, health, and versions",
	Long: `Render a one-shot snapshot of the running SynthOrg stack.

Combines a verdict banner (OK / DEGRADED / CRITICAL), the backend
/api/v1/readyz response, the per-container table from
docker compose ps, and live resource usage. Use --watch to refresh
on an interval, --wide for port columns, --services to filter by
name, or --check for a silent exit-code-only run intended for
scripts (0 healthy, 3 unhealthy, 4 unreachable).`,
	Example: `  synthorg status              # show current status
  synthorg status --watch      # continuously poll
  synthorg status --wide       # show extra columns
  synthorg status --check      # exit code only (for scripts)`,
	RunE: runStatus,
}

func init() {
	statusCmd.Flags().BoolVarP(&statusWatch, "watch", "w", false, "continuously poll status")
	statusCmd.Flags().StringVar(&statusInterval, "interval", "2s", "watch polling interval (e.g. 2s, 5s)")
	statusCmd.Flags().BoolVar(&statusWide, "wide", false, "show extra columns (ports)")
	statusCmd.Flags().BoolVar(&statusNoTrunc, "no-trunc", false, "show full image names")
	statusCmd.Flags().StringVar(&statusServices, "services", "", "filter by service names (comma-separated)")
	statusCmd.Flags().BoolVar(&statusCheck, "check", false, "exit code only: 0=healthy, 3=unhealthy, 4=unreachable")
	statusCmd.GroupID = "core"
	rootCmd.AddCommand(statusCmd)
}

func runStatus(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)

	// Validate --interval up front, BEFORE the --check dispatch: a malformed
	// or non-positive interval is a usage error in every mode. Previously
	// --check returned before the interval was ever parsed, so a scripted
	// `status --check --interval bogus` silently ignored the bad value.
	interval, parseErr := time.ParseDuration(statusInterval)
	if parseErr != nil {
		return fmt.Errorf("invalid --interval %q: %w", statusInterval, parseErr)
	}
	if interval <= 0 {
		return fmt.Errorf("invalid --interval %q: must be > 0", statusInterval)
	}

	state, err := config.Load(opts.DataDir)
	if statusCheck {
		// --check is a scripted health probe that must still run when the
		// config cannot be loaded (uninitialised host, unreadable state):
		// fall back to the default backend port and probe anyway rather
		// than aborting with a config error the script cannot act on.
		if err != nil {
			state = config.DefaultState()
		}
		return runStatusCheckExitCode(ctx, state)
	}
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	if statusWatch {
		return runStatusWatch(cmd, state, opts, interval)
	}
	if err := runStatusOnce(cmd, state, opts); err != nil {
		return fmt.Errorf("running status check: %w", err)
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	out.HintGuidance("Use --watch for continuous monitoring, or --check for scripted health checks.")
	return nil
}

// runStatusCheckExitCode implements --check: a silent mode that returns
// an ExitError with the appropriate code (0 healthy, 3 unhealthy, 4
// unreachable). Validates the response body for status="ok" rather than
// trusting the HTTP status alone.
func runStatusCheckExitCode(ctx context.Context, state config.State) error {
	body, statusCode, fetchErr := fetchHealth(ctx, state.BackendPort)
	if fetchErr != nil {
		return NewExitError(ExitUnreachable, fetchErr)
	}
	if statusCode < 200 || statusCode >= 300 {
		return NewExitError(ExitUnhealthy, nil)
	}
	var envelope struct {
		Data healthResponse `json:"data"`
	}
	if json.Unmarshal(body, &envelope) != nil || envelope.Data.Status != "ok" {
		return NewExitError(ExitUnhealthy, nil)
	}
	return nil
}

func runStatusWatch(cmd *cobra.Command, state config.State, opts *GlobalOpts, interval time.Duration) error {
	ctx := cmd.Context()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		// Clear screen (best-effort: ANSI escape for TTY, separator for non-TTY).
		if isInteractive() && !opts.Plain {
			_, _ = fmt.Fprint(cmd.OutOrStdout(), "\033[H\033[2J")
		} else {
			_, _ = fmt.Fprintln(cmd.OutOrStdout(), "---")
		}
		if err := runStatusOnce(cmd, state, opts); err != nil {
			return fmt.Errorf("refreshing status: %w", err)
		}

		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func runStatusOnce(cmd *cobra.Command, state config.State, opts *GlobalOpts) error {
	ctx := cmd.Context()
	jsonOut := opts.JSON
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	printVersionInfo(out, state)

	safeDir, err := safeStateDir(state)
	if err != nil {
		return fmt.Errorf("resolving data directory: %w", err)
	}
	composePath := filepath.Join(safeDir, "compose.yml")
	if _, err := os.Stat(composePath); errors.Is(err, os.ErrNotExist) {
		out.Warn("Not initialized -- run 'synthorg init' first.")
		return nil
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		out.Warn(fmt.Sprintf("Docker not available: %v", err))
		return nil
	}
	out.KeyValue("Docker", info.DockerVersion)
	out.KeyValue("Compose", info.ComposeVersion)
	_, _ = fmt.Fprintln(out.Writer())

	// Gather every signal first so the top banner can summarise
	// before we render per-section detail. Sections below stay fed
	// by the same snapshot so the banner and the body never disagree.
	snap := gatherStatusSnapshot(ctx, info, safeDir, state)

	if !jsonOut {
		renderTopBanner(out, snap)
	}

	renderHealthSection(out, snap, jsonOut)
	renderContainersSection(out, snap, jsonOut)
	printResourceUsage(ctx, out, info, safeDir)
	if state.PersistenceBackend == "postgres" && statusWide {
		printPostgresVolumeInfo(ctx, out, info)
	}
	printLinks(out, state)

	return nil
}
