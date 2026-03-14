package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/spf13/cobra"
)

var startCmd = &cobra.Command{
	Use:   "start",
	Short: "Pull images and start the SynthOrg stack",
	RunE:  runStart,
}

func init() {
	rootCmd.AddCommand(startCmd)
}

func runStart(cmd *cobra.Command, args []string) error {
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
	fmt.Fprintf(cmd.OutOrStdout(), "Docker %s, Compose %s\n", info.DockerVersion, info.ComposeVersion)

	// Check minimum versions.
	for _, w := range docker.CheckMinVersions(info) {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: %s\n", w)
	}

	// Pull latest images.
	fmt.Fprintln(cmd.OutOrStdout(), "Pulling images...")
	if err := composeRun(ctx, cmd, info, state.DataDir, "pull"); err != nil {
		return fmt.Errorf("pulling images: %w", err)
	}

	// Start containers.
	fmt.Fprintln(cmd.OutOrStdout(), "Starting containers...")
	if err := composeRun(ctx, cmd, info, state.DataDir, "up", "-d"); err != nil {
		return fmt.Errorf("starting containers: %w", err)
	}

	// Wait for health.
	fmt.Fprintln(cmd.OutOrStdout(), "Waiting for backend to become healthy...")
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/health", state.BackendPort)
	if err := health.WaitForHealthy(ctx, healthURL, 90*time.Second, 2*time.Second, 5*time.Second); err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Containers are running but health check failed. Run 'synthorg doctor' for diagnostics.\n")
		return fmt.Errorf("health check did not pass: %w", err)
	}

	fmt.Fprintln(cmd.OutOrStdout(), "SynthOrg is running!")
	fmt.Fprintf(cmd.OutOrStdout(), "  API:       http://localhost:%d/api/v1/health\n", state.BackendPort)
	fmt.Fprintf(cmd.OutOrStdout(), "  Dashboard: http://localhost:%d\n", state.WebPort)
	return nil
}

func composeRun(ctx context.Context, cobraCmd *cobra.Command, info docker.Info, dir string, args ...string) error {
	fullArgs := make([]string, 0, len(info.ComposeCmd)-1+len(args))
	fullArgs = append(fullArgs, info.ComposeCmd[1:]...)
	fullArgs = append(fullArgs, args...)

	c := exec.CommandContext(ctx, info.ComposeCmd[0], fullArgs...)
	c.Dir = dir
	c.Stdout = cobraCmd.OutOrStdout()
	c.Stderr = cobraCmd.ErrOrStderr()
	return c.Run()
}
