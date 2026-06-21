package cmd

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/completion"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	uninstallKeepData   bool
	uninstallKeepImages bool
)

var uninstallCmd = &cobra.Command{
	Use:   "uninstall",
	Short: "Stop containers, remove data, and uninstall SynthOrg",
	Long: `Tear down the SynthOrg installation.

Stops every container, removes named volumes, deletes the data
directory, and removes pulled images. Each destructive step is
confirmed interactively unless --yes is set. Pass --keep-data to
preserve the data directory and config (useful before a clean
re-install) or --keep-images to leave pulled images on disk for
faster re-init later.`,
	Example: `  synthorg uninstall                # interactive uninstall (prompts for each step)
  synthorg uninstall --yes          # non-interactive full uninstall
  synthorg uninstall --keep-data    # uninstall but preserve config and data
  synthorg uninstall --keep-images  # uninstall but preserve container images`,
	RunE: runUninstall,
}

func init() {
	uninstallCmd.Flags().BoolVar(&uninstallKeepData, "keep-data", false, "preserve data directory")
	uninstallCmd.Flags().BoolVar(&uninstallKeepImages, "keep-images", false, "preserve container images")
	uninstallCmd.GroupID = "lifecycle"
	rootCmd.AddCommand(uninstallCmd)
}

func runUninstall(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	if !isInteractive() && !opts.Yes {
		return fmt.Errorf("uninstall requires an interactive terminal or --yes flag (destructive operation)")
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errUI := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	// Uninstall is a teardown path: a broken, invalid, or absent on-disk
	// config must never block the operator from removing whatever IS there.
	// LoadForTeardown parses what it can (never runs strict Validate) and
	// returns an advisory-only error.
	state, loadErr := config.LoadForTeardown(opts.DataDir)
	if loadErr != nil {
		errUI.Warn(fmt.Sprintf("Could not fully load config (%v); continuing teardown with what could be read.", loadErr))
	}
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}
	autoAccept := opts.Yes
	if err := uninstallContainers(cmd, ctx, safeDir, out, errUI, autoAccept); err != nil {
		return err
	}
	if err := uninstallData(cmd, safeDir, autoAccept, out); err != nil {
		return err
	}
	removeAllShellCompletions(ctx, out, errUI)
	if err := confirmAndRemoveBinary(cmd, autoAccept); err != nil {
		return err
	}
	out.Blank()
	out.Success("SynthOrg uninstalled")
	out.HintNextStep("Reinstall from GitHub Releases: https://github.com/Aureliolo/synthorg/releases")
	return nil
}

// uninstallContainers stops the containers + (optionally) volumes, and
// (optionally) removes the SynthOrg images. Skipped entirely when
// Docker is not available; warns to errUI in that case.
func uninstallContainers(cmd *cobra.Command, ctx context.Context, safeDir string, out, errUI *ui.UI, autoAccept bool) error {
	info, dockerAvailable := detectDockerForTeardown(ctx, errUI)
	if !dockerAvailable {
		return nil
	}
	if err := stopAndRemoveVolumes(cmd, info, safeDir, out, autoAccept, uninstallKeepData); err != nil {
		return err
	}
	if uninstallKeepImages {
		out.Success("Container images preserved (--keep-images)")
		out.HintNextStep("Container images still on disk. Run 'docker rmi' to free space later.")
		return nil
	}
	return confirmAndRemoveImages(cmd, info, out, errUI, autoAccept)
}

// uninstallData removes the data directory unless --keep-data is set.
func uninstallData(cmd *cobra.Command, safeDir string, autoAccept bool, out *ui.UI) error {
	if !uninstallKeepData {
		return confirmAndRemoveData(cmd, safeDir, autoAccept)
	}
	out.Success(fmt.Sprintf("Data directory preserved (--keep-data): %s", safeDir))
	out.HintGuidance(fmt.Sprintf("Config and data preserved at %s. Reinstall will reuse this data.", safeDir))
	return nil
}

// shouldRemoveVolumes decides whether `compose down` should pass -v.
// --keep-data forces false (volumes hold app data we must preserve);
// --yes accepts without prompting; otherwise we prompt interactively.
func shouldRemoveVolumes(keepData, autoAccept bool) (bool, error) {
	if keepData {
		return false, nil
	}
	if autoAccept {
		return true, nil
	}
	var remove bool
	form := huh.NewForm(
		huh.NewGroup(
			huh.NewConfirm().
				Title("Remove Docker volumes? (ALL DATA WILL BE LOST)").
				Description("This removes the persistent database and memory data.").
				Value(&remove),
		),
	)
	if err := form.Run(); err != nil {
		return false, err
	}
	return remove, nil
}

// removeAllShellCompletions removes the SynthOrg snippet from every
// supported shell profile (the user may have installed completions for
// multiple shells).
func removeAllShellCompletions(ctx context.Context, out, errUI *ui.UI) {
	sp := out.StartSpinner("Removing shell completions...")
	for _, shell := range []completion.ShellType{
		completion.Bash, completion.Zsh, completion.Fish, completion.PowerShell,
	} {
		if err := completion.Uninstall(ctx, shell); err != nil {
			errUI.Warn(fmt.Sprintf("Could not remove %s completions: %v", shell, err))
		}
	}
	sp.Success("Shell completions removed")
}

func stopAndRemoveVolumes(cmd *cobra.Command, info docker.Info, dataDir string, out *ui.UI, autoAccept bool, keepData bool) error {
	ctx := cmd.Context()
	// No compose.yml means an uninitialised install: there is nothing to
	// stop, so skip `down` and let teardown continue to data/binary removal
	// rather than hard-failing before it. A non-not-found stat error (e.g.
	// permission) warns but still skips, keeping teardown best-effort.
	composePath, statErr := composeFilePath(dataDir)
	if statErr != nil {
		out.Warn(fmt.Sprintf("Could not check for compose.yml: %v; skipping container teardown.", statErr))
		return nil
	}
	if composePath == "" {
		out.Step(msgNothingToStop)
		return nil
	}
	removeVolumes, err := shouldRemoveVolumes(keepData, autoAccept)
	if err != nil {
		return err
	}
	downArgs := []string{"down"}
	if removeVolumes {
		downArgs = append(downArgs, "-v")
	}

	sp := out.StartSpinner("Stopping containers...")
	if err := composeRunQuiet(ctx, info, dataDir, downArgs...); err != nil {
		// A compose file that vanished mid-teardown, or any docker "no
		// configuration file provided" error, means there was nothing to
		// stop. Translate the jargon and keep tearing down.
		if isNotInitialisedErr(err) {
			sp.Success(msgNothingToStop)
			return nil
		}
		sp.Error("Failed to stop containers")
		return fmt.Errorf("stopping containers: %w", err)
	}
	msg := "Containers stopped"
	if removeVolumes {
		msg += " and volumes removed"
	}
	sp.Success(msg)

	return nil
}

// confirmAndRemoveImages offers to remove SynthOrg container images.
// Lists all images (not just old ones) deduplicated by Docker ID.
func confirmAndRemoveImages(cmd *cobra.Command, info docker.Info, out, errUI *ui.UI, autoAccept bool) error {
	ctx := cmd.Context()

	// List all SynthOrg images (pass empty currentIDs to include everything).
	images, err := listNonCurrentImages(ctx, errUI.Writer(), info, nil)
	if err != nil || len(images) == 0 {
		if len(images) == 0 && err == nil {
			out.Success("No SynthOrg images found locally.")
		}
		return nil
	}

	var lines []string
	for _, img := range images {
		lines = append(lines, img.display)
	}
	out.Box("SynthOrg Images", lines)
	out.Blank()

	removeImages := autoAccept
	if !autoAccept {
		form := huh.NewForm(huh.NewGroup(
			huh.NewConfirm().
				Title(fmt.Sprintf("Remove %d image(s)?", len(images))).
				Value(&removeImages),
		))
		if err := form.Run(); err != nil {
			return err
		}
	}
	if !removeImages {
		return nil
	}

	removeImagesOneByOne(ctx, info, out, images)
	return nil
}

// removeImagesOneByOne removes images individually with per-image feedback.
// Uses --force (unlike cleanup) since uninstall is a destructive operation.
func removeImagesOneByOne(ctx context.Context, info docker.Info, out *ui.UI, images []oldImage) {
	var removed int
	for _, img := range images {
		if ctx.Err() != nil {
			out.Warn("operation cancelled")
			break
		}
		_, rmiErr := docker.RunCmd(ctx, info.DockerPath, "rmi", "--force", img.id)
		if rmiErr != nil {
			out.Warn(fmt.Sprintf("%-12s skipped: %v", img.id, rmiErr))
		} else {
			out.Success(fmt.Sprintf("%-12s removed", img.id))
			removed++
		}
	}
	if removed > 0 {
		out.Success(fmt.Sprintf("Removed %d image(s)", removed))
	}
}

func confirmAndRemoveData(cmd *cobra.Command, dataDir string, autoAccept bool) error {
	removeData := autoAccept
	if !autoAccept {
		form := huh.NewForm(
			huh.NewGroup(
				huh.NewConfirm().
					Title(fmt.Sprintf("Remove config directory? (%s)", dataDir)).
					Value(&removeData),
			),
		)
		if err := form.Run(); err != nil {
			return err
		}
	}
	if !removeData {
		return nil
	}
	dir := filepath.Clean(dataDir)
	if err := rejectUnsafeDir(dir); err != nil {
		return err
	}
	return removeDataDir(cmd, dir)
}

// rejectUnsafeDir refuses to remove root, home, relative, UNC share
// roots, or drive roots. Splitting per-shape keeps each predicate easy
// to reason about and prevents the function from accumulating
// architecture-specific path knowledge in one body.
func rejectUnsafeDir(dir string) error {
	if dir == "" || dir == "." || !filepath.IsAbs(dir) {
		return fmt.Errorf("refusing to remove %q -- must be an absolute path", dir)
	}
	if dir == "/" || isHomeDirectory(dir) || isDriveRoot(dir) || isUNCShareRoot(dir) {
		return fmt.Errorf("refusing to remove %q -- does not look like an app data directory", dir)
	}
	return nil
}

// isHomeDirectory reports whether dir resolves to the user's home dir.
// On Windows the comparison is case-insensitive; elsewhere it is byte
// equal. If we cannot determine the home dir, returns false (we cannot
// confidently reject what we cannot identify).
func isHomeDirectory(dir string) bool {
	home, err := os.UserHomeDir()
	if err != nil {
		return false
	}
	home = filepath.Clean(home)
	if runtime.GOOS == "windows" {
		return strings.EqualFold(dir, home)
	}
	return dir == home
}

// isDriveRoot reports whether dir is a Windows drive root such as `C:\`
// or `C:/`. Three-character form is the only valid shape after
// filepath.Clean.
func isDriveRoot(dir string) bool {
	return len(dir) == 3 && dir[1] == ':' && (dir[2] == '\\' || dir[2] == '/')
}

// isUNCShareRoot reports whether dir is the root of a UNC share (e.g.
// \\server\share) rather than a path inside one (e.g.
// \\server\share\app\data). Only the bare root is rejected: paths
// inside UNC shares are legitimate install targets.
func isUNCShareRoot(dir string) bool {
	vol := filepath.VolumeName(dir)
	if vol == "" {
		return false
	}
	if !strings.HasPrefix(vol, `\\`) && !strings.HasPrefix(vol, "//") {
		return false
	}
	return dir == vol || dir == vol+`\` || dir == vol+"/"
}

// removeDataDir removes the data directory. The installed binary lives
// under a sibling `bin` tree, never inside the data dir, so a plain
// RemoveAll is sufficient and never deletes the running CLI.
func removeDataDir(cmd *cobra.Command, dir string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	if err := os.RemoveAll(dir); err != nil {
		return fmt.Errorf("removing config directory: %w", err)
	}
	out.Success(fmt.Sprintf("Removed %s", dir))
	return nil
}

// confirmAndRemoveBinary asks to remove the CLI binary. On Windows, spawns
// a detached process that waits for the current process to exit, then
// deletes the binary and cleans up empty parent directories.
func confirmAndRemoveBinary(cmd *cobra.Command, autoAccept bool) error {
	removeBinary := autoAccept
	if !autoAccept {
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
	}

	if !removeBinary {
		return nil
	}

	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("finding executable: %w", err)
	}
	// Resolve symlinks so we remove the actual binary.
	if resolved, err := filepath.EvalSymlinks(execPath); err == nil {
		execPath = resolved
	}

	if runtime.GOOS != "windows" {
		return removeUnixBinary(cmd, execPath)
	}
	return scheduleWindowsCleanup(cmd, execPath)
}

func removeUnixBinary(cmd *cobra.Command, execPath string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	if err := os.Remove(execPath); err != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not remove binary: %v\n", err)
		out.HintNextStep(fmt.Sprintf("Manually remove: %s", execPath))
	} else {
		out.Success("CLI binary removed")
	}
	return nil
}

// scheduleWindowsCleanup writes a temporary .bat file that waits for the
// current process to exit, then deletes the binary, empty parent dirs,
// and the .bat file itself. Uses a temp .bat instead of inline cmd /c
// because goto/labels don't work in single-line cmd /c commands.
//
// It tidies the install tree only: the binary, its `bin` dir, and the
// app root (the parent of `bin`). The data dir is a sibling removed
// separately by uninstallData, so this never touches it.
func scheduleWindowsCleanup(cmd *cobra.Command, execPath string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	binDir := filepath.Dir(execPath)
	appRoot := filepath.Dir(binDir)

	// Reject paths with characters that would break .bat quoting or let an
	// install path inject cmd.exe syntax: double-quote, percent (variable
	// expansion), and the metacharacters caret/redirection/pipe/ampersand.
	for _, p := range []string{execPath, binDir, appRoot} {
		if strings.ContainsAny(p, "\"%^<>|&") {
			return fallbackManualCleanup(cmd, execPath, fmt.Errorf("path contains unsafe characters for batch script (one of \"%%^<>|&)"))
		}
	}

	batContent := windowsCleanupBat(execPath, binDir, appRoot)
	batFile, err := os.CreateTemp(binDir, "synthorg-cleanup-*.bat")
	if err != nil {
		return fallbackManualCleanup(cmd, execPath, err)
	}
	batPath := batFile.Name()
	if _, err := batFile.WriteString(batContent); err != nil {
		_ = batFile.Close()
		_ = os.Remove(batPath)
		return fallbackManualCleanup(cmd, execPath, err)
	}
	if err := batFile.Close(); err != nil {
		_ = os.Remove(batPath)
		return fallbackManualCleanup(cmd, execPath, err)
	}

	// Spawn detached -- use context.Background so parent context
	// cancellation doesn't kill the cleanup process.
	c := exec.CommandContext(context.Background(), "cmd.exe", "/c", batPath) //nolint:gosec,noctx // G204: cmd.exe is constant, batPath is the CLI-generated temp uninstaller script; noctx: intentionally detached
	c.SysProcAttr = windowsDetachedProcAttr()
	if err := c.Start(); err != nil {
		_ = os.Remove(batPath)
		return fallbackManualCleanup(cmd, execPath, err)
	}

	// Detach -- don't wait for the cleanup process.
	_ = c.Process.Release()

	out.Success("CLI binary will be removed automatically after exit")
	return nil
}

// windowsCleanupBat builds the cleanup .bat body: it polls for the current
// process to exit, then deletes the binary and removes the now-empty `bin`
// dir and app root (both best-effort via 2>nul, no-op if anything else still
// lives there), and finally self-deletes. Callers MUST validate execPath /
// binDir / appRoot for cmd.exe metacharacters before passing them in.
func windowsCleanupBat(execPath, binDir, appRoot string) string {
	pid := os.Getpid()
	return fmt.Sprintf(
		"@echo off\r\n"+
			"for /L %%%%i in (1,1,30) do (\r\n"+
			"  tasklist /fi \"PID eq %d\" 2>nul | find \"%d\" >nul || goto :cleanup\r\n"+
			"  timeout /t 1 /nobreak >nul\r\n"+
			")\r\n"+
			"goto :done\r\n"+
			":cleanup\r\n"+
			"del /f /q \"%s\"\r\n"+
			"rmdir \"%s\" 2>nul\r\n"+
			"rmdir \"%s\" 2>nul\r\n"+
			":done\r\n"+
			"del /f /q \"%%~f0\"\r\n",
		pid, pid,
		execPath,
		binDir,
		appRoot,
	)
}

func fallbackManualCleanup(cmd *cobra.Command, execPath string, cause error) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	out.Warn(fmt.Sprintf("Could not schedule automatic cleanup: %v", cause))
	escaped := strings.ReplaceAll(execPath, "'", "''")
	out.HintNextStep(fmt.Sprintf("To finish cleanup after exit, run: powershell -Command \"Remove-Item -LiteralPath '%s'\"", escaped))
	return nil
}
