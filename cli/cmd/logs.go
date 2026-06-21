package cmd

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	logFollow     bool
	logTail       string
	logSince      string
	logUntil      string
	logTimestamps bool
	logNoPrefix   bool
)

const logsFilterHint = "Filter by service: 'synthorg logs backend'. Use --since 1h for time-based filtering."

// serviceNamePattern validates service names to prevent command injection via
// compose arguments (only alphanumeric, hyphens, and underscores).
var serviceNamePattern = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)

// timeFilterPattern validates --since/--until values (timestamps, durations).
// Must start with an alphanumeric character to prevent flag-shaped values.
var timeFilterPattern = regexp.MustCompile(`^[0-9a-zA-Z][0-9a-zA-Z:.+\-TZ]*$`)

var logsCmd = &cobra.Command{
	Use:   "logs [service]",
	Short: "Show container logs",
	Long:  "Passes through to 'docker compose logs'. Optionally specify a service (backend, web).",
	Example: `  synthorg logs                # show recent logs (last 100 lines)
  synthorg logs -f             # follow log output
  synthorg logs backend        # logs for a specific service
  synthorg logs --since 1h     # logs from the last hour`,
	RunE: runLogs,
}

func init() {
	logsCmd.Flags().BoolVarP(&logFollow, "follow", "f", false, "follow log output")
	logsCmd.Flags().StringVar(&logTail, "tail", "100", "number of lines to show from end")
	logsCmd.Flags().StringVar(&logSince, "since", "", "show logs since timestamp or relative (e.g. 2024-01-01, 1h)")
	logsCmd.Flags().StringVar(&logUntil, "until", "", "show logs until timestamp or relative")
	logsCmd.Flags().BoolVarP(&logTimestamps, "timestamps", "t", false, "show timestamps")
	logsCmd.Flags().BoolVar(&logNoPrefix, "no-log-prefix", false, "don't print service prefix in logs")
	logsCmd.GroupID = "core"
	rootCmd.AddCommand(logsCmd)
}

func runLogs(cmd *cobra.Command, args []string) error {
	if err := validateLogsInput(logTail, logSince, logUntil, args); err != nil {
		return err
	}

	ctx := cmd.Context()
	state, err := config.Load(GetGlobalOpts(ctx).DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}
	composePath := filepath.Join(safeDir, "compose.yml")
	if _, err := os.Stat(composePath); errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("compose.yml not found in %s", safeDir)
	}
	info, err := docker.Detect(ctx)
	if err != nil {
		return err
	}

	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// Emit hints before composeRun in follow mode (-f), since it blocks
	// until Ctrl+C and the post-run code would be unreachable.
	if logFollow {
		out.HintGuidance(logsFilterHint)
	}

	composeArgs := buildLogsArgs(strings.TrimSpace(logTail), args)
	if err := composeRun(ctx, cmd, info, safeDir, composeArgs...); err != nil {
		return err
	}

	out.HintTip("Use -f to follow log output in real time.")
	if !logFollow {
		out.HintGuidance(logsFilterHint)
	}
	return nil
}

// validateLogsInput validates --tail, --since, --until, and service name arguments.
func validateLogsInput(tail, since, until string, services []string) error {
	t := strings.TrimSpace(tail)
	if t != "all" {
		if n, err := strconv.Atoi(t); err != nil || n <= 0 {
			return fmt.Errorf("--tail must be a positive integer or 'all', got %q", tail)
		}
	}
	for _, tv := range []struct{ flag, val string }{{"--since", since}, {"--until", until}} {
		if tv.val != "" && !timeFilterPattern.MatchString(tv.val) {
			return fmt.Errorf("%s value %q contains unexpected characters", tv.flag, tv.val)
		}
	}
	for _, svc := range services {
		if !serviceNamePattern.MatchString(svc) {
			return fmt.Errorf("invalid service name %q: must be alphanumeric, hyphens, or underscores", svc)
		}
	}
	return nil
}

// buildLogsArgs constructs the docker compose logs arguments.
func buildLogsArgs(tail string, services []string) []string {
	composeArgs := []string{"logs", "--tail", tail}
	if logFollow {
		composeArgs = append(composeArgs, "-f")
	}
	if logSince != "" {
		composeArgs = append(composeArgs, "--since", logSince)
	}
	if logUntil != "" {
		composeArgs = append(composeArgs, "--until", logUntil)
	}
	if logTimestamps {
		composeArgs = append(composeArgs, "--timestamps")
	}
	if logNoPrefix {
		composeArgs = append(composeArgs, "--no-log-prefix")
	}
	composeArgs = append(composeArgs, "--")
	composeArgs = append(composeArgs, services...)
	return composeArgs
}
