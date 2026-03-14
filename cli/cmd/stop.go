package cmd

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/spf13/cobra"
)

var stopCmd = &cobra.Command{
	Use:   "stop",
	Short: "Stop the SynthOrg stack",
	RunE:  runStop,
}

func init() {
	rootCmd.AddCommand(stopCmd)
}

func runStop(cmd *cobra.Command, args []string) error {
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

	fmt.Fprintln(cmd.OutOrStdout(), "Stopping containers...")
	if err := composeRun(ctx, cmd, info, state.DataDir, "down"); err != nil {
		return fmt.Errorf("stopping containers: %w", err)
	}

	fmt.Fprintln(cmd.OutOrStdout(), "SynthOrg stopped.")
	return nil
}
