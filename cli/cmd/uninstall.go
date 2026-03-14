package cmd

import (
	"fmt"
	"os"
	"path/filepath"

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

func runUninstall(cmd *cobra.Command, _ []string) error {
	if !isInteractive() {
		return fmt.Errorf("uninstall requires an interactive terminal (destructive operation)")
	}

	ctx := cmd.Context()
	dir := resolveDataDir()
	out := cmd.OutOrStdout()

	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}

	// Stop containers and optionally remove volumes.
	info, dockerErr := docker.Detect(ctx)
	if dockerErr != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: Docker not available, cannot stop containers: %v\n", dockerErr)
	} else {
		if err := stopAndRemoveVolumes(cmd, info, safeDir); err != nil {
			return err
		}
	}

	// Remove data directory.
	if err := confirmAndRemoveData(cmd, safeDir); err != nil {
		return err
	}

	// Optionally remove CLI binary.
	if err := confirmAndRemoveBinary(cmd); err != nil {
		return err
	}

	_, _ = fmt.Fprintln(out, "SynthOrg uninstalled.")
	return nil
}

func stopAndRemoveVolumes(cmd *cobra.Command, info docker.Info, dataDir string) error {
	ctx := cmd.Context()

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

	_, _ = fmt.Fprintln(cmd.OutOrStdout(), "Stopping containers...")

	// Use "down -v" if removing volumes (handles both stop and volume removal
	// in a single command), otherwise just "down".
	downArgs := []string{"down"}
	if removeVolumes {
		downArgs = append(downArgs, "-v")
		_, _ = fmt.Fprintln(cmd.OutOrStdout(), "Removing volumes...")
	}

	if err := composeRun(ctx, cmd, info, dataDir, downArgs...); err != nil {
		return fmt.Errorf("stopping containers: %w", err)
	}

	return nil
}

func confirmAndRemoveData(cmd *cobra.Command, dataDir string) error {
	var removeData bool
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title(fmt.Sprintf("Remove data directory? (%s)", dataDir)).
				Value(&removeData),
		),
	)
	if err := form.Run(); err != nil {
		return err
	}

	if removeData {
		dir := dataDir
		// Safety: refuse to remove root, home, or empty paths.
		home, _ := os.UserHomeDir()
		if dir == "/" || dir == home || (len(dir) == 3 && dir[1] == ':' && dir[2] == '\\') {
			return fmt.Errorf("refusing to remove %q — does not look like an app data directory", dir)
		}
		if err := os.RemoveAll(dir); err != nil {
			return fmt.Errorf("removing data directory: %w", err)
		}
		_, _ = fmt.Fprintf(cmd.OutOrStdout(), "Removed %s\n", dir)
	}
	return nil
}

func confirmAndRemoveBinary(cmd *cobra.Command) error {
	var removeBinary bool
	form := huh.NewForm(
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
		// Resolve symlinks so we remove the actual binary.
		if resolved, err := filepath.EvalSymlinks(execPath); err == nil {
			execPath = resolved
		}
		if err := os.Remove(execPath); err != nil {
			_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not remove binary: %v\n", err)
			_, _ = fmt.Fprintf(cmd.OutOrStdout(), "Manually remove: %s\n", execPath)
		} else {
			_, _ = fmt.Fprintln(cmd.OutOrStdout(), "CLI binary removed.")
		}
	}
	return nil
}
