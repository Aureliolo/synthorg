package cmd

import (
	"fmt"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/charmbracelet/huh"
	"github.com/spf13/cobra"
)

var updateCmd = &cobra.Command{
	Use:   "update",
	Short: "Update CLI binary and pull new container images",
	RunE:  runUpdate,
}

func init() {
	rootCmd.AddCommand(updateCmd)
}

func runUpdate(cmd *cobra.Command, _ []string) error {
	if err := updateCLI(cmd); err != nil {
		return err
	}
	return updateContainerImages(cmd)
}

func updateCLI(cmd *cobra.Command) error {
	ctx := cmd.Context()
	out := cmd.OutOrStdout()

	// Warn on dev builds.
	if version.Version == "dev" {
		fmt.Fprintln(out, "Warning: running a dev build — update check will always report an update available.")
	}

	fmt.Fprintln(out, "Checking for updates...")
	result, err := selfupdate.Check(ctx)
	if err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not check for updates: %v\n", err)
		return nil
	}

	if !result.UpdateAvail {
		fmt.Fprintf(out, "CLI is up to date (%s)\n", result.CurrentVersion)
		return nil
	}

	fmt.Fprintf(out, "New version available: %s (current: %s)\n", result.LatestVersion, result.CurrentVersion)

	if isInteractive() {
		var proceed bool
		form := huh.NewForm(huh.NewGroup(
			huh.NewConfirm().
				Title(fmt.Sprintf("Update CLI from %s to %s?", result.CurrentVersion, result.LatestVersion)).
				Value(&proceed),
		))
		if err := form.Run(); err != nil {
			return err
		}
		if !proceed {
			return nil
		}
	} else {
		fmt.Fprintf(out, "Non-interactive mode: auto-applying update to %s\n", result.LatestVersion)
	}

	fmt.Fprintln(out, "Downloading...")
	binary, err := selfupdate.Download(ctx, result.AssetURL, result.ChecksumURL)
	if err != nil {
		return fmt.Errorf("downloading update: %w", err)
	}

	if err := selfupdate.Replace(binary); err != nil {
		return fmt.Errorf("replacing binary: %w", err)
	}
	fmt.Fprintf(out, "CLI updated to %s\n", result.LatestVersion)
	return nil
}

func updateContainerImages(cmd *cobra.Command) error {
	ctx := cmd.Context()
	out := cmd.OutOrStdout()

	dir := resolveDataDir()
	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: Docker not available, skipping image update: %v\n", err)
		return nil
	}

	fmt.Fprintln(out, "Pulling latest container images...")
	if err := composeRun(ctx, cmd, info, state.DataDir, "pull"); err != nil {
		return fmt.Errorf("pulling images: %w", err)
	}

	// Check if containers are running and offer restart.
	psOut, err := docker.ComposeExecOutput(ctx, info, state.DataDir, "ps", "-q")
	if err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not check container status: %v\n", err)
		return nil
	}

	if psOut == "" {
		return nil
	}

	if !isInteractive() {
		fmt.Fprintln(out, "Non-interactive mode: skipping restart. Run 'synthorg stop && synthorg start' to apply new images.")
		return nil
	}

	restart, err := confirmRestart()
	if err != nil {
		return err
	}
	if !restart {
		return nil
	}

	fmt.Fprintln(out, "Restarting...")
	if err := composeRun(ctx, cmd, info, state.DataDir, "down"); err != nil {
		return fmt.Errorf("stopping containers: %w", err)
	}
	if err := composeRun(ctx, cmd, info, state.DataDir, "up", "-d"); err != nil {
		return fmt.Errorf("restarting containers: %w", err)
	}

	// Health check after restart.
	fmt.Fprintln(out, "Waiting for backend to become healthy...")
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/health", state.BackendPort)
	if err := health.WaitForHealthy(ctx, healthURL, 90*time.Second, 2*time.Second, 5*time.Second); err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: health check did not pass after restart: %v\n", err)
	} else {
		fmt.Fprintln(out, "Containers restarted with new images and healthy.")
	}

	return nil
}

func confirmRestart() (bool, error) {
	var restart bool
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
