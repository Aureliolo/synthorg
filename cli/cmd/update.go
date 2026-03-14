package cmd

import (
	"fmt"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
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

func runUpdate(cmd *cobra.Command, args []string) error {
	ctx := cmd.Context()
	out := cmd.OutOrStdout()

	// Check for CLI update.
	fmt.Fprintln(out, "Checking for updates...")
	result, err := selfupdate.Check(ctx)
	if err != nil {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not check for updates: %v\n", err)
	} else if result.UpdateAvail {
		fmt.Fprintf(out, "New version available: %s (current: %s)\n", result.LatestVersion, result.CurrentVersion)

		if result.AssetURL != "" {
			fmt.Fprintln(out, "Downloading...")
			binary, err := selfupdate.Download(ctx, result.AssetURL, result.ChecksumURL)
			if err != nil {
				return fmt.Errorf("downloading update: %w", err)
			}

			if err := selfupdate.Replace(binary); err != nil {
				return fmt.Errorf("replacing binary: %w", err)
			}
			fmt.Fprintf(out, "CLI updated to %s\n", result.LatestVersion)
		} else {
			fmt.Fprintln(out, "No binary available for your platform. Download manually from GitHub Releases.")
		}
	} else {
		fmt.Fprintf(out, "CLI is up to date (%s)\n", result.CurrentVersion)
	}

	// Pull new container images.
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
	if err := composeRun(ctx, info, state.DataDir, "pull"); err != nil {
		return fmt.Errorf("pulling images: %w", err)
	}

	// Check if containers are running and offer restart.
	psOut, _ := docker.ComposeExecOutput(ctx, info, state.DataDir, "ps", "-q")
	if psOut != "" {
		var restart bool
		form := huh.NewForm(
			huh.NewGroup(
				huh.NewConfirm().
					Title("Containers are running. Restart with new images?").
					Value(&restart),
			),
		)
		if err := form.Run(); err != nil {
			return err
		}
		if restart {
			fmt.Fprintln(out, "Restarting...")
			if err := composeRun(ctx, info, state.DataDir, "down"); err != nil {
				fmt.Fprintf(cmd.ErrOrStderr(), "Warning: stopping containers failed: %v\n", err)
			}
			if err := composeRun(ctx, info, state.DataDir, "up", "-d"); err != nil {
				return fmt.Errorf("restarting containers: %w", err)
			}
			fmt.Fprintln(out, "Containers restarted with new images.")
		}
	}

	return nil
}
