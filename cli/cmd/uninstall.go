package cmd

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
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
	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
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
	if err := confirmAndRemoveBinary(cmd, safeDir, autoAccept); err != nil {
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
	info, dockerErr := docker.Detect(ctx)
	if dockerErr != nil {
		errUI.Warn(fmt.Sprintf("Docker not available, cannot stop containers: %v", dockerErr))
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

// removeDataDir removes the data directory. On Windows, if the running
// binary lives inside the directory, it removes everything except the binary.
func removeDataDir(cmd *cobra.Command, dir string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	execPath, execErr := os.Executable()
	if execErr != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: cannot resolve executable path: %v\n", execErr)
	}
	if execErr == nil {
		if resolved, err := filepath.EvalSymlinks(execPath); err == nil {
			execPath = resolved
		}
	}
	if execErr == nil && runtime.GOOS == "windows" && isInsideDir(execPath, dir) {
		if err := removeAllExcept(dir, execPath); err != nil {
			return fmt.Errorf("removing config directory: %w", err)
		}
		out.Success(fmt.Sprintf("Removed contents of %s (binary skipped -- still running)", dir))
	} else {
		if err := os.RemoveAll(dir); err != nil {
			return fmt.Errorf("removing config directory: %w", err)
		}
		out.Success(fmt.Sprintf("Removed %s", dir))
	}
	return nil
}

// confirmAndRemoveBinary asks to remove the CLI binary. On Windows, spawns
// a detached process that waits for the current process to exit, then
// deletes the binary and cleans up empty parent directories.
func confirmAndRemoveBinary(cmd *cobra.Command, dataDir string, autoAccept bool) error {
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
	return scheduleWindowsCleanup(cmd, execPath, dataDir)
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
func scheduleWindowsCleanup(cmd *cobra.Command, execPath, dataDir string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	pid := os.Getpid()
	binDir := filepath.Dir(execPath)

	// Reject paths containing characters that would break .bat quoting:
	// double-quote (command injection) and percent (cmd.exe variable expansion).
	if strings.ContainsAny(execPath, `"%`) || strings.ContainsAny(binDir, `"%`) || strings.ContainsAny(dataDir, `"%`) {
		return fallbackManualCleanup(cmd, execPath, fmt.Errorf("path contains unsafe characters for batch script (double-quote or percent)"))
	}

	// Write cleanup script to a temp .bat file next to the binary
	// (same filesystem, survives after this process exits).
	batContent := fmt.Sprintf(
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
		dataDir,
	)

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
	c := exec.CommandContext(context.Background(), "cmd.exe", "/c", batPath) //nolint:noctx // intentionally detached
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

func fallbackManualCleanup(cmd *cobra.Command, execPath string, cause error) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	out.Warn(fmt.Sprintf("Could not schedule automatic cleanup: %v", cause))
	escaped := strings.ReplaceAll(execPath, "'", "''")
	out.HintNextStep(fmt.Sprintf("To finish cleanup after exit, run: powershell -Command \"Remove-Item -LiteralPath '%s'\"", escaped))
	return nil
}

// isInsideDir reports whether child is inside (or equal to) parent.
// On Windows, the comparison is case-insensitive (NTFS is case-insensitive).
// Note: strings.ToLower is correct for ASCII paths; non-ASCII Unicode paths
// on NTFS could require full Unicode case-folding (golang.org/x/text/cases),
// but Windows user profile and app-data paths are overwhelmingly ASCII.
func isInsideDir(child, parent string) bool {
	child = filepath.Clean(child)
	parent = filepath.Clean(parent)
	// Case-fold on Windows so that C:\Foo and C:\foo are treated as equal.
	if runtime.GOOS == "windows" {
		child = strings.ToLower(child)
		parent = strings.ToLower(parent)
	}
	rel, err := filepath.Rel(parent, child)
	if err != nil {
		return false
	}
	return !strings.HasPrefix(rel, "..")
}

type walkEntry struct {
	path  string
	isDir bool
}

// caseFoldOnWindows lowercases s for case-insensitive comparison on
// Windows (NTFS is case-insensitive). On other platforms it is the
// identity function.
func caseFoldOnWindows(s string) string {
	if runtime.GOOS == "windows" {
		return strings.ToLower(s)
	}
	return s
}

// collectRemoveEntries walks root and returns every descendant entry
// except root itself and the path that equals exceptCmp (case-folded
// on Windows). The order matches filepath.WalkDir (parents before
// children), so callers iterate in reverse to remove deepest-first.
func collectRemoveEntries(root, exceptCmp string) ([]walkEntry, error) {
	var entries []walkEntry
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		cleanPath := filepath.Clean(path)
		if cleanPath == root {
			return nil
		}
		if caseFoldOnWindows(cleanPath) == exceptCmp {
			return nil
		}
		entries = append(entries, walkEntry{path: path, isDir: d.IsDir()})
		return nil
	})
	return entries, err
}

// removeAllExcept removes all files and directories under root except the
// file at except (and its ancestor directories up to root). The root
// directory itself is preserved. Entries are removed deepest-first so
// that empty directories are cleaned up.
func removeAllExcept(root, except string) error {
	root = filepath.Clean(root)
	except = filepath.Clean(except)
	exceptCmp := caseFoldOnWindows(except)
	entries, err := collectRemoveEntries(root, exceptCmp)
	if err != nil {
		return err
	}

	// Remove in reverse order (deepest first). Directory removal failures
	// are expected for ancestors of the excluded file (non-empty); other
	// errors (files, permission-denied dirs) are collected and reported.
	var errs []error
	for i := len(entries) - 1; i >= 0; i-- {
		if err := os.Remove(entries[i].path); err != nil {
			if entries[i].isDir && isInsideDir(except, entries[i].path) {
				continue // expected: ancestor of excluded file is non-empty
			}
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}
