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
	"github.com/spf13/cobra"
)

var (
	logFollow bool
	logTail   string
)

// serviceNamePattern validates service names to prevent command injection via
// compose arguments (only alphanumeric, hyphens, and underscores).
var serviceNamePattern = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)

var logsCmd = &cobra.Command{
	Use:   "logs [service]",
	Short: "Show container logs",
	Long:  "Passes through to 'docker compose logs'. Optionally specify a service (backend, web).",
	RunE:  runLogs,
}

func init() {
	logsCmd.Flags().BoolVarP(&logFollow, "follow", "f", false, "follow log output")
	logsCmd.Flags().StringVar(&logTail, "tail", "100", "number of lines to show from end")
	rootCmd.AddCommand(logsCmd)
}

func runLogs(cmd *cobra.Command, args []string) error {
	ctx := cmd.Context()
	dir := resolveDataDir()

	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	composePath := filepath.Join(state.DataDir, "compose.yml")
	if _, err := os.Stat(composePath); errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("compose.yml not found in %s — run 'synthorg init' first", state.DataDir)
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		return err
	}

	// Validate --tail value.
	tail := strings.TrimSpace(logTail)
	if tail != "all" {
		if n, err := strconv.Atoi(tail); err != nil || n <= 0 {
			return fmt.Errorf("--tail must be a positive integer or 'all', got %q", logTail)
		}
	}

	// Validate service name arguments.
	for _, svc := range args {
		if !serviceNamePattern.MatchString(svc) {
			return fmt.Errorf("invalid service name %q: must be alphanumeric, hyphens, or underscores", svc)
		}
	}

	composeArgs := []string{"logs", "--tail", tail}
	if logFollow {
		composeArgs = append(composeArgs, "-f")
	}
	// Use -- separator to prevent service names from being parsed as flags.
	composeArgs = append(composeArgs, "--")
	composeArgs = append(composeArgs, args...)

	return composeRun(ctx, cmd, info, state.DataDir, composeArgs...)
}
