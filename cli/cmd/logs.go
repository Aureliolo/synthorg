package cmd

import (
	"fmt"
	"os"
	"path/filepath"
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
	if _, err := os.Stat(composePath); os.IsNotExist(err) {
		return fmt.Errorf("compose.yml not found in %s — run 'synthorg init' first", state.DataDir)
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		return err
	}

	// Validate --tail value.
	tail := strings.TrimSpace(logTail)
	if tail != "all" {
		if n, err := strconv.Atoi(tail); err != nil || n < 0 {
			return fmt.Errorf("--tail must be a positive integer or 'all', got %q", logTail)
		}
	}

	composeArgs := []string{"logs", "--tail", tail}
	if logFollow {
		composeArgs = append(composeArgs, "-f")
	}
	composeArgs = append(composeArgs, args...)

	return composeRun(ctx, info, state.DataDir, composeArgs...)
}
