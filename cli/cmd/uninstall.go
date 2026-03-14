package cmd

import (
	"fmt"
	"os"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/charmbracelet/huh"
	"github.com/spf13/cobra"
)

var uninstallCmd = &cobra.Command{
	Use:   "uninstall",
	Short: "Stop containers, remove data, and uninstall SynthOrg",
	RunE:  runUninstall,
}

func init() {
	rootCmd.AddCommand(uninstallCmd)
}

func runUninstall(cmd *cobra.Command, args []string) error {
	ctx := cmd.Context()
	dir := resolveDataDir()
	out := cmd.OutOrStdout()

	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	// Stop containers.
	dockerAvailable := false
	info, err := docker.Detect(ctx)
	if err == nil {
		dockerAvailable = true
		fmt.Fprintln(out, "Stopping containers...")
		if err := composeRun(ctx, info, state.DataDir, "down"); err != nil {
			fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not stop containers: %v\n", err)
		}
	} else {
		fmt.Fprintf(cmd.ErrOrStderr(), "Warning: Docker not available, cannot stop containers: %v\n", err)
	}

	// Confirm volume removal.
	var removeVolumes bool
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title("Remove Docker volumes? (ALL DATA WILL BE LOST)").
				Description("This removes the persistent database and memory data.").
				Value(&removeVolumes),
		),
	)
	if err := form.Run(); err != nil {
		return err
	}

	if removeVolumes {
		if dockerAvailable {
			fmt.Fprintln(out, "Removing volumes...")
			if err := composeRun(ctx, info, state.DataDir, "down", "-v"); err != nil {
				fmt.Fprintf(cmd.ErrOrStderr(), "Warning: volume removal may have failed: %v\n", err)
			}
		} else {
			fmt.Fprintln(cmd.ErrOrStderr(), "Warning: Docker not available, cannot remove volumes. Remove them manually.")
		}
	}

	// Remove data directory.
	var removeData bool
	form = huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title(fmt.Sprintf("Remove data directory? (%s)", state.DataDir)).
				Value(&removeData),
		),
	)
	if err := form.Run(); err != nil {
		return err
	}

	if removeData {
		if err := os.RemoveAll(state.DataDir); err != nil {
			return fmt.Errorf("removing data directory: %w", err)
		}
		fmt.Fprintf(out, "Removed %s\n", state.DataDir)
	}

	// Optionally remove CLI binary.
	var removeBinary bool
	form = huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title("Remove CLI binary?").
				Description("You can reinstall later from GitHub Releases.").
				Value(&removeBinary),
		),
	)
	if err := form.Run(); err != nil {
		return err
	}

	if removeBinary {
		execPath, err := os.Executable()
		if err != nil {
			return fmt.Errorf("finding executable: %w", err)
		}
		if err := os.Remove(execPath); err != nil {
			fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not remove binary: %v\n", err)
			fmt.Fprintf(out, "Manually remove: %s\n", execPath)
		} else {
			fmt.Fprintln(out, "CLI binary removed.")
		}
	}

	fmt.Fprintln(out, "SynthOrg uninstalled.")
	return nil
}
