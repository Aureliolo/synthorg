package cmd

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

// errWipeCancelled is a sentinel error used to signal that the user cancelled
// the wipe operation. Callers convert this to a clean (nil) exit.
var errWipeCancelled = errors.New("wipe cancelled by user")

// wipeContext bundles the shared dependencies for the wipe workflow,
// reducing parameter passing across the multi-step operation.
type wipeContext struct {
	ctx     context.Context
	cmd     *cobra.Command
	state   config.State
	info    docker.Info
	safeDir string
	out     *ui.UI
	errOut  *ui.UI
	// dockerAvailable is false when Docker could not be detected. Teardown
	// stays best-effort: a missing Docker skips backup + container teardown
	// but still clears the data dir.
	dockerAvailable bool
}

var (
	wipeDryRun     bool
	wipeNoBackup   bool
	wipeKeepImages bool
)

var wipeCmd = &cobra.Command{
	Use:   "wipe",
	Short: "Factory-reset: wipe all data with optional backup and restart",
	Long: `Destroy all SynthOrg data (database, memory, settings) and return
to a clean state. You are prompted at each step:

  1. Whether to create a backup (default: yes)
  2. Whether to start containers for the backup (if needed)
  3. Where to save the backup archive (if backing up)
  4. Whether to overwrite if the backup file already exists
  5. Final confirmation before wiping

After wiping, run 'synthorg init' to set up again.

Requires an interactive terminal.`,
	Example: `  synthorg wipe                # interactive factory reset with backup
  synthorg wipe --yes          # non-interactive wipe with backup
  synthorg wipe --no-backup    # skip the backup step
  synthorg wipe --dry-run      # preview what would happen`,
	RunE: runWipe,
}

func init() {
	wipeCmd.Flags().BoolVar(&wipeDryRun, "dry-run", false, "show what would happen without wiping")
	wipeCmd.Flags().BoolVar(&wipeNoBackup, "no-backup", false, "skip the backup prompt entirely")
	wipeCmd.Flags().BoolVar(&wipeKeepImages, "keep-images", false, "do not remove container images during wipe")
	wipeCmd.GroupID = "lifecycle"
	rootCmd.AddCommand(wipeCmd)
}

func runWipe(cmd *cobra.Command, _ []string) error {
	opts := GetGlobalOpts(cmd.Context())
	if !wipeDryRun && !isInteractive() && !opts.Yes {
		return fmt.Errorf("wipe requires an interactive terminal or --yes flag (destructive operation)")
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

	// Teardown loads best-effort: a missing compose.yml, an unreadable or
	// invalid config (out-of-range port, missing master_key, corrupt JSON)
	// must never stop a factory-reset. Parse what we can, clear the rest.
	state, loadErr := config.LoadForTeardown(opts.DataDir)
	if loadErr != nil {
		errOut.Warn(fmt.Sprintf("Could not fully load config (%v); continuing wipe with what could be read.", loadErr))
	}
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}

	if wipeDryRun {
		// Preview is best-effort: a stat error on compose.yml renders as
		// "(unavailable)" rather than aborting the dry run.
		composePath, _ := composeFilePath(safeDir)
		return wipeDryRunPreview(out, safeDir, composePath)
	}

	// Docker detection is non-fatal: an unreachable daemon skips the backup
	// and container teardown but still clears the data dir.
	info, dockerAvailable := detectDockerForTeardown(cmd.Context(), errOut)
	wc := newWipeContext(cmd.Context(), cmd, state, info, safeDir, dockerAvailable, out, errOut)
	proceed, err := wc.runOptionalBackup()
	if err != nil {
		return err
	}
	if !proceed {
		return nil
	}
	return wc.confirmAndWipe()
}

func newWipeContext(ctx context.Context, cmd *cobra.Command, state config.State, info docker.Info, safeDir string, dockerAvailable bool, out, errOut *ui.UI) *wipeContext {
	return &wipeContext{
		ctx:             ctx,
		cmd:             cmd,
		state:           state,
		info:            info,
		safeDir:         safeDir,
		dockerAvailable: dockerAvailable,
		out:             out,
		errOut:          errOut,
	}
}

// runOptionalBackup runs the offerBackup workflow unless --no-backup is
// set. Returns proceed=false when the user cancelled (errWipeCancelled
// is treated as a clean exit, not an error).
func (wc *wipeContext) runOptionalBackup() (proceed bool, err error) {
	if wipeNoBackup {
		return true, nil
	}
	if !wc.dockerAvailable {
		// A backup needs a running stack; without Docker there is nothing
		// to back up. Proceed straight to the data wipe.
		wc.errOut.Warn("Skipping backup: Docker is not available.")
		return true, nil
	}
	if err := wc.offerBackup(); err != nil {
		if errors.Is(err, errWipeCancelled) {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// wipeDryRunPreview shows what a wipe would do without executing.
// Empty safeDir / composePath strings render as "(unavailable)" so a
// half-installed state (missing compose.yml, unreadable config) still
// produces useful preview output instead of a confusing blank field.
func wipeDryRunPreview(out *ui.UI, safeDir, composePath string) error {
	out.Section("Dry run: wipe preview")
	out.KeyValue("Data directory", presentOrUnavailable(safeDir))
	out.KeyValue("Compose file", presentOrUnavailable(composePath))
	out.KeyValue("Backup", boolToYesNo(!wipeNoBackup))
	out.KeyValue("Remove images", boolToYesNo(!wipeKeepImages))
	out.HintNextStep("Remove --dry-run to execute the wipe")
	return nil
}

// presentOrUnavailable returns v unchanged when non-empty, or
// "(unavailable)" when empty so KeyValue renders something meaningful
// for half-installed wipe-preview rows.
func presentOrUnavailable(v string) string {
	if v == "" {
		return "(unavailable)"
	}
	return v
}

// confirmAndWipe asks for final confirmation, stops containers, removes
// volumes, and optionally restarts the stack. Restart failures are
// non-fatal -- they produce a warning and a manual-start hint.
func (wc *wipeContext) confirmAndWipe() error {
	confirmed, err := wc.confirmWipe()
	if err != nil {
		return err
	}
	if !confirmed {
		wc.out.HintNextStep("Wipe cancelled.")
		return nil
	}
	if err := wc.stopAndPrune(); err != nil {
		return err
	}
	if wipeNoBackup {
		wc.out.HintNextStep("Backup skipped. Data cannot be recovered after wipe.")
	}
	if err := wc.removeDataDirectory(); err != nil {
		return err
	}
	wc.out.Blank()
	wc.out.Success("Wipe complete -- back to clean state")
	wc.out.HintNextStep("Run 'synthorg init' to set up again.")
	return nil
}

// stopAndPrune runs `compose down -v` (plus --rmi all when images are
// not preserved) and renders the per-mode success message. On an
// uninitialised state (no Docker, or no compose.yml) it is a no-op: there
// is nothing to stop, so it reports that and lets the data wipe proceed.
func (wc *wipeContext) stopAndPrune() error {
	if !wc.dockerAvailable {
		wc.out.Step(msgNothingToStop)
		return nil
	}
	composePath, statErr := composeFilePath(wc.safeDir)
	if statErr != nil {
		// A permission (or other non-not-found) error: warn but stay
		// best-effort and proceed to the data wipe rather than aborting.
		wc.errOut.Warn(fmt.Sprintf("Could not check for compose.yml: %v; skipping container teardown.", statErr))
		return nil
	}
	if composePath == "" {
		wc.out.Step(msgNothingToStop)
		return nil
	}
	downArgs := []string{"down", "-v"}
	if !wipeKeepImages {
		downArgs = append(downArgs, "--rmi", "all")
	}
	sp := wc.out.StartSpinner("Stopping containers and removing volumes...")
	if err := composeRunQuiet(wc.ctx, wc.info, wc.safeDir, downArgs...); err != nil {
		// A compose file that vanished between the stat and the down call,
		// or any docker "no configuration file provided" error, means there
		// was nothing to stop. Translate the jargon and keep tearing down.
		if isNotInitialisedErr(err) {
			sp.Success(msgNothingToStop)
			return nil
		}
		sp.Error("Failed to stop containers")
		return fmt.Errorf("stopping containers: %w", err)
	}
	if wipeKeepImages {
		sp.Success("Containers stopped and volumes removed (images preserved)")
		wc.out.HintNextStep("Container images preserved. Run 'synthorg cleanup --all' to remove them later.")
		return nil
	}
	sp.Success("Containers stopped, volumes and images removed")
	return nil
}

// pathContainsTraversal reports whether path -- after lexical cleaning
// -- contains a ".." element. Compares whole path components rather
// than a substring so names like "foo..bar" or ".." (only as a literal
// component) are evaluated correctly. Both / and \ are tolerated as
// separators so the same check works on Windows.
func pathContainsTraversal(path string) bool {
	cleaned := filepath.Clean(path)
	parts := strings.FieldsFunc(cleaned, func(r rune) bool {
		return r == '/' || r == '\\'
	})
	return slices.Contains(parts, "..")
}

// removeDataDirectory deletes the safeDir contents after checking the
// path looks safe. The installed binary lives under a sibling `bin`
// tree, never inside the data dir, so a plain RemoveAll is sufficient
// and never deletes the running CLI.
func (wc *wipeContext) removeDataDirectory() error {
	// Guard: safeDir was validated by safeStateDir -> config.SecurePath
	// (absolute + clean). Reject anything that looks like a traversal
	// or root path to prevent accidental destruction. The traversal
	// check splits on filepath.Separator and matches whole ".."
	// elements rather than substring ".." -- legitimate names such as
	// "/var/lib/synthorg..bak" would otherwise be rejected. The bare
	// volume-name check (no trailing separator) catches UNC roots such
	// as "\\\\server\\share" which filepath.Clean normalises by
	// stripping the trailing separator; without it a RemoveAll could
	// recurse over an entire share.
	volume := filepath.VolumeName(wc.safeDir)
	if pathContainsTraversal(wc.safeDir) ||
		wc.safeDir == "/" ||
		wc.safeDir == volume+string(filepath.Separator) ||
		wc.safeDir == volume {
		return fmt.Errorf("refusing to remove suspicious path: %s", wc.safeDir)
	}
	sp := wc.out.StartSpinner("Removing data directory...")
	if rmErr := os.RemoveAll(wc.safeDir); rmErr != nil {
		// A partial wipe is not success. Surface the error so the CLI
		// exits with a non-zero status and the user sees a loud failure
		// instead of "Wipe complete" printed over a half-cleaned dir.
		sp.Warn(fmt.Sprintf("Could not remove data directory: %v", rmErr))
		wc.errOut.HintError(fmt.Sprintf("Manually delete %s to complete the wipe.", wc.safeDir))
		return fmt.Errorf("removing data directory: %w", rmErr)
	}
	sp.Success("Data directory removed")
	return nil
}

// runForm configures a huh form with the wipe context's I/O streams and runs it.
func (wc *wipeContext) runForm(form *huh.Form) error {
	return form.
		WithInput(wc.cmd.InOrStdin()).
		WithOutput(wc.cmd.OutOrStdout()).
		Run()
}

// confirmWipe prompts for final destructive-action confirmation.
func (wc *wipeContext) confirmWipe() (bool, error) {
	if !wc.shouldPrompt() {
		return true, nil // --yes: auto-confirm wipe
	}
	var confirmed bool
	err := wc.runForm(huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title("This will destroy ALL data (database, memory, settings). Continue?").
			Description("This cannot be undone.").
			Affirmative("Yes, wipe everything").
			Negative("Cancel").
			Value(&confirmed),
	)))
	if err != nil {
		if isUserAbort(err) {
			// Wipe has NOT happened yet, so Ctrl-C is equivalent to
			// choosing "Cancel" -- return false without errWipeCancelled
			// since the caller already handles the !confirmed path.
			return false, nil
		}
		return false, fmt.Errorf("confirmation prompt: %w", err)
	}
	return confirmed, nil
}

// containersRunning reports whether the SynthOrg stack has at least one
// container. A non-nil error indicates that Docker itself could not be
// reached (as opposed to containers simply being stopped).
func (wc *wipeContext) containersRunning() (bool, error) {
	psOut, err := docker.ComposeExecOutput(wc.ctx, wc.info, wc.safeDir, "ps", "--format", "json")
	if err != nil {
		return false, fmt.Errorf("checking container status: %w", err)
	}
	empty, err := isEmptyPS(psOut)
	if err != nil {
		return false, err
	}
	return !empty, nil
}

// startContainers verifies, pulls, and starts the stack, then waits for
// the backend to become healthy.
func (wc *wipeContext) startContainers() error {
	wc.out.Blank()
	if err := wc.verifyAndPin(); err != nil {
		return err
	}
	wc.out.Blank()
	return pullStartAndWait(wc.ctx, wc.cmd, wc.info, wc.safeDir, wc.state, wc.out, wc.errOut)
}

// verifyAndPin runs cache-aware verification of both image groups, writes
// the digest-pinned compose if SynthOrg was reverified, persists fresh
// pins + the new VerifiedImageTag sentinel, and reloads wc.state from
// disk so the subsequent pull sees the freshly-pinned references.
//
// On --skip-verify it warns and returns immediately, leaving wc.state
// untouched (matching the start path's behaviour).
func (wc *wipeContext) verifyAndPin() error {
	if GetGlobalOpts(wc.ctx).SkipVerify {
		wc.errOut.Warn("Image verification skipped (--skip-verify). Containers are NOT verified.")
		return nil
	}

	verifyCtx, cancel := context.WithTimeout(wc.ctx, GetGlobalOpts(wc.ctx).Tunables.ImageVerifyTimeout)
	defer cancel()
	result, err := verifyImagesWithCache(verifyCtx, wc.info, wc.state, wc.out, wc.errOut)
	if err != nil {
		return err
	}

	if result.SynthOrgReverified {
		if err := writeDigestPinnedCompose(wc.state, synthOrgPins(result.Pins), wc.safeDir); err != nil {
			return fmt.Errorf("pinning verified digests: %w", err)
		}
	}

	if !result.SynthOrgReverified && !result.DHIReverified {
		return nil
	}

	wc.state.VerifiedDigests = result.Pins
	wc.state.VerifiedImageTag = wc.state.ImageTag
	if err := config.Save(wc.state); err != nil {
		wc.errOut.Warn(fmt.Sprintf("Could not cache verified digests: %v", err))
	}
	reloaded, err := config.Load(GetGlobalOpts(wc.ctx).DataDir)
	if err != nil {
		return fmt.Errorf("reloading config after verification: %w", err)
	}
	wc.state = reloaded
	return nil
}

// waitForBackendHealth waits for the backend to become healthy.
// Returns an error if the backend does not become healthy within the
// timeout or the context is cancelled.
func (wc *wipeContext) waitForBackendHealth() error {
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", wc.state.BackendPort)
	return health.WaitForHealthy(wc.ctx, healthURL, 30*time.Second, 2*time.Second, 5*time.Second)
}

// offerBackup prompts whether to create a backup, and if so, ensures
// containers are running (prompting if needed), prompts for a save path,
// checks for overwrite conflicts, then creates the backup via the backend
// API and copies the archive to a local path.
func (wc *wipeContext) offerBackup() error {
	wantBackup, err := wc.promptForBackup()
	if err != nil {
		return err
	}
	if !wantBackup {
		return nil
	}

	ready, err := wc.ensureRunningForBackup()
	if err != nil {
		return err
	}
	if !ready {
		return nil // user chose to skip backup via askContinueWithoutBackup
	}

	savePath, err := wc.promptSavePath()
	if err != nil {
		return err
	}

	if err := wc.checkOverwrite(savePath); err != nil {
		return err
	}

	return wc.createAndCopyBackup(savePath)
}

// ensureRunningForBackup checks whether containers are running. If not,
// it prompts the user before starting them. If the user declines, it
// falls through to askContinueWithoutBackup (backup cannot proceed
// without running containers). Returns true when the backend is ready
// for a backup, or false when the user chose to skip the backup.
func (wc *wipeContext) ensureRunningForBackup() (bool, error) {
	// askToSkip warns and prompts the user to continue without a backup.
	// Returns (false, nil) if the user agrees to skip, or (false, err)
	// if the user cancels the wipe.
	askToSkip := func(prompt string) (bool, error) {
		if err := wc.askContinueWithoutBackup(prompt); err != nil {
			return false, err
		}
		return false, nil
	}

	running, err := wc.containersRunning()
	if err != nil {
		wc.errOut.Warn(fmt.Sprintf("Could not check container status: %v", err))
		return askToSkip("Could not check container status. Continue with wipe anyway?")
	}
	if running {
		if err := wc.waitForBackendHealth(); err != nil {
			wc.errOut.Warn(fmt.Sprintf("Backend not healthy: %v", err))
			return askToSkip("Backend is not healthy. Continue with wipe anyway?")
		}
		return true, nil
	}

	startOK, err := wc.promptStartForBackup()
	if err != nil {
		return false, err
	}
	if !startOK {
		return askToSkip("Backup requires running containers. Continue with wipe anyway?")
	}

	if err := wc.startContainers(); err != nil {
		wc.errOut.Warn(fmt.Sprintf("Could not start containers for backup: %v", err))
		return askToSkip("Could not start containers for backup. Continue with wipe anyway?")
	}

	// Containers just started -- wait for the backend before attempting backup.
	if err := wc.waitForBackendHealth(); err != nil {
		wc.errOut.Warn(fmt.Sprintf("Backend not healthy after start: %v", err))
		return askToSkip("Backend is not healthy. Continue with wipe anyway?")
	}

	return true, nil
}

// shouldPrompt reports whether interactive prompts should be shown.
// Returns false when --yes is active, allowing non-interactive automation.
func (wc *wipeContext) shouldPrompt() bool {
	return GetGlobalOpts(wc.ctx).ShouldPrompt()
}

// promptStartForBackup asks whether to start containers so a backup
// can be created.
func (wc *wipeContext) promptStartForBackup() (bool, error) {
	if !wc.shouldPrompt() {
		return true, nil // default: yes, start for backup
	}
	startOK := true
	err := wc.runForm(huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title("Containers are not running. Start them for backup?").
			Affirmative("Yes").
			Negative("No, skip backup").
			Value(&startOK),
	)))
	if err != nil {
		if isUserAbort(err) {
			wc.out.HintNextStep("Wipe cancelled.")
			return false, errWipeCancelled
		}
		return false, fmt.Errorf("start prompt: %w", err)
	}
	return startOK, nil
}

// promptForBackup asks whether the user wants a backup before wiping.
func (wc *wipeContext) promptForBackup() (bool, error) {
	if !wc.shouldPrompt() {
		return true, nil // default: yes, create backup
	}
	wantBackup := true
	err := wc.runForm(huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title("Create a backup before wiping? (recommended)").
			Description("Saves your current data so you can restore later.").
			Affirmative("Yes").
			Negative("No, skip").
			Value(&wantBackup),
	)))
	if err != nil {
		if isUserAbort(err) {
			wc.out.HintNextStep("Wipe cancelled.")
			return false, errWipeCancelled
		}
		return false, fmt.Errorf("backup prompt: %w", err)
	}
	return wantBackup, nil
}

// promptSavePath asks the user for a local path to save the backup archive.
func (wc *wipeContext) promptSavePath() (string, error) {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		homeDir = os.TempDir()
		wc.errOut.Warn("Could not determine home directory; defaulting to temp directory")
	}
	defaultPath := filepath.Join(homeDir, fmt.Sprintf("synthorg-backup-%s.tar.gz", time.Now().Format("20060102-150405")))

	if !wc.shouldPrompt() {
		return filepath.Abs(defaultPath)
	}

	savePath := defaultPath
	if err := wc.runForm(huh.NewForm(huh.NewGroup(
		huh.NewInput().
			Title("Save backup to").
			Description("Path for the backup archive").
			Value(&savePath),
	))); err != nil {
		if isUserAbort(err) {
			wc.out.HintNextStep("Wipe cancelled.")
			return "", errWipeCancelled
		}
		return "", fmt.Errorf("save path prompt: %w", err)
	}
	savePath = strings.TrimSpace(savePath)
	if savePath == "" {
		savePath = defaultPath
	}

	// Expand leading ~ or ~/ to the user's home directory.
	if savePath == "~" {
		savePath = homeDir
	} else if strings.HasPrefix(savePath, "~/") || strings.HasPrefix(savePath, "~\\") {
		savePath = filepath.Join(homeDir, savePath[2:])
	}

	savePath = filepath.Clean(savePath)
	absPath, err := filepath.Abs(savePath)
	if err != nil {
		return "", fmt.Errorf("resolving save path: %w", err)
	}
	return absPath, nil
}

// checkOverwrite warns and prompts if the save path already exists.
// Note: there is an inherent TOCTOU race between this check and the
// eventual write (in tarDirectory or docker compose cp). For a local CLI
// tool this is acceptable -- the race requires a co-located malicious
// process, and resolving it would require restructuring both write paths.
func (wc *wipeContext) checkOverwrite(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil // file does not exist -- safe to write
		}
		return fmt.Errorf("cannot access save path: %w", err)
	}
	if info.IsDir() {
		return fmt.Errorf("save path must be a file, not a directory: %s", path)
	}
	if !wc.shouldPrompt() {
		return nil // --yes: auto-overwrite
	}
	var overwrite bool
	err = wc.runForm(huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title(fmt.Sprintf("File already exists: %s. Overwrite?", path)).
			Affirmative("Yes, overwrite").
			Negative("Cancel").
			Value(&overwrite),
	)))
	if err != nil {
		if isUserAbort(err) {
			wc.out.HintNextStep("Overwrite declined -- wipe cancelled.")
			return errWipeCancelled
		}
		return fmt.Errorf("overwrite prompt: %w", err)
	}
	if !overwrite {
		wc.out.HintNextStep("Overwrite declined -- wipe cancelled.")
		return errWipeCancelled
	}
	return nil
}

// createAndCopyBackup creates a backup via the API and copies it locally.
func (wc *wipeContext) createAndCopyBackup(savePath string) error {
	sp := wc.out.StartSpinner("Creating backup...")
	manifest, err := createBackupViaAPI(wc.ctx, wc.state)
	if err != nil {
		sp.Error("Backup failed")
		wc.errOut.Warn(fmt.Sprintf("Could not create backup: %v", err))
		return wc.askContinueWithoutBackup("Backup creation failed. Continue with wipe anyway?")
	}
	sp.Success("Backup created")

	sp = wc.out.StartSpinner("Copying backup to local path...")
	if err := copyBackupFromContainer(wc.ctx, wc.info, wc.safeDir, manifest.BackupID, savePath); err != nil {
		sp.Error("Failed to copy backup")
		wc.errOut.Warn(fmt.Sprintf("Could not copy backup locally: %v", err))
		wc.errOut.HintError("The backup exists in the container but will be destroyed by the wipe.")
		return wc.askContinueWithoutBackup(
			"Backup was created but could not be copied locally. Continue with wipe anyway?",
		)
	}
	sp.Success(fmt.Sprintf("Backup saved to %s", savePath))

	return nil
}

// createBackupViaAPI triggers a manual backup and returns the manifest.
func createBackupViaAPI(ctx context.Context, state config.State) (backupManifest, error) {
	body, statusCode, err := backupAPIRequest(
		ctx, state.BackendPort, http.MethodPost, "", nil,
		60*time.Second, state.JWTSecret,
	)
	if err != nil {
		return backupManifest{}, fmt.Errorf("backup API request: %w", err)
	}
	if statusCode < 200 || statusCode >= 300 {
		msg := apiErrorMessage(body, "backup failed")
		return backupManifest{}, fmt.Errorf("backup API error: %s", sanitizeAPIMessage(msg))
	}

	data, err := parseAPIResponse(body)
	if err != nil {
		return backupManifest{}, fmt.Errorf("parsing backup response: %w", err)
	}

	var manifest backupManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return backupManifest{}, fmt.Errorf("parsing backup manifest: %w", err)
	}
	return manifest, nil
}

// copyBackupFromContainer copies the backup archive from the backend
// container to a local path. It tries the compressed archive first,
// then falls back to the uncompressed directory.
func copyBackupFromContainer(ctx context.Context, info docker.Info, safeDir, backupID, localPath string) error {
	// Validate backup ID format (12 hex chars).
	if !isValidBackupID(backupID) {
		return fmt.Errorf("invalid backup ID: %s", backupID)
	}

	// Try compressed archive first (default). If the compressed file
	// does not exist, docker compose cp fails and we fall back to the
	// uncompressed directory below. Log the first error for diagnostics.
	archiveName := backupID + "_manual.tar.gz"
	containerSrc := "backend:/data/backups/" + archiveName
	firstErr := composeRunQuiet(ctx, info, safeDir, "cp", containerSrc, localPath)
	if firstErr == nil {
		return nil
	}

	// Fall back to uncompressed directory -- the compressed archive may
	// not exist depending on the backup handler. Log the first attempt
	// error for diagnostics in case the fallback also fails.
	_, _ = fmt.Fprintf(os.Stderr, "compressed archive not available (%v), trying uncompressed directory\n", firstErr)
	dirName := backupID + "_manual"
	containerSrc = "backend:/data/backups/" + dirName + "/."
	tmpDir, mkErr := os.MkdirTemp("", "synthorg-backup-*")
	if mkErr != nil {
		return fmt.Errorf("creating temp dir: %w", mkErr)
	}
	defer func() { _ = os.RemoveAll(tmpDir) }()

	if err := composeRunQuiet(ctx, info, safeDir, "cp", containerSrc, tmpDir+"/"); err != nil {
		return fmt.Errorf("copying backup from container: %w", err)
	}

	// The user expects a single file at localPath.
	return tarDirectory(tmpDir, localPath)
}

// tarDirectory creates a tar.gz archive of the contents of srcDir at dstPath.
func tarDirectory(srcDir, dstPath string) error {
	entries, err := os.ReadDir(srcDir)
	if err != nil {
		return fmt.Errorf("reading backup dir: %w", err)
	}
	if len(entries) == 0 {
		return fmt.Errorf("backup directory is empty")
	}

	dstPath = filepath.Clean(dstPath)
	f, err := os.OpenFile(dstPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return fmt.Errorf("creating archive: %w", err)
	}

	if err := createTarGz(f, srcDir); err != nil {
		_ = f.Close()
		_ = os.Remove(dstPath)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(dstPath)
		return fmt.Errorf("finalising archive: %w", err)
	}
	return nil
}

// askContinueWithoutBackup prompts whether to proceed with the wipe even
// though the backup could not be created. The title parameter customises
// the prompt to match the reason (e.g. user declined, Docker unreachable,
// container start failure). Returns nil to continue, or errWipeCancelled
// to abort the wipe cleanly.
func (wc *wipeContext) askContinueWithoutBackup(title string) error {
	if !wc.shouldPrompt() {
		return nil // --yes: continue without backup
	}
	var proceed bool
	err := wc.runForm(huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title(title).
			Description("All data will be lost without a backup.").
			Affirmative("Yes, continue").
			Negative("Cancel").
			Value(&proceed),
	)))
	if err != nil {
		if isUserAbort(err) {
			wc.out.HintNextStep("Wipe cancelled.")
			return errWipeCancelled
		}
		return fmt.Errorf("continue prompt: %w", err)
	}
	if !proceed {
		wc.out.HintNextStep("Wipe cancelled.")
		return errWipeCancelled
	}
	return nil
}

// isEmptyPS returns true if docker compose ps output indicates no containers.
// Handles both JSON array format (Compose v2.21+) and NDJSON (older versions).
// Returns an error if the JSON output cannot be parsed.
func isEmptyPS(output string) (bool, error) {
	trimmed := strings.TrimSpace(output)
	if trimmed == "" {
		return true, nil
	}
	// JSON array format (Compose v2.21+).
	if strings.HasPrefix(trimmed, "[") {
		var arr []json.RawMessage
		if err := json.Unmarshal([]byte(trimmed), &arr); err != nil {
			return false, fmt.Errorf("parsing docker compose ps output: %w", err)
		}
		return len(arr) == 0, nil
	}
	// NDJSON: any non-empty line means at least one container.
	return false, nil
}

// createTarGz writes a gzip-compressed tar archive of srcDir's contents to w.
// Symlinks are skipped to prevent following links outside the source directory.
func createTarGz(w io.Writer, srcDir string) error {
	gw := gzip.NewWriter(w)
	tw := tar.NewWriter(gw)

	walkErr := filepath.WalkDir(srcDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return nil // skip symlinks
		}
		rel, err := filepath.Rel(srcDir, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		return writeTarEntry(tw, path, rel, d)
	})

	// Close tar then gzip; errors.Join reports all errors.
	errTar := tw.Close()
	errGzip := gw.Close()
	return errors.Join(walkErr, errTar, errGzip)
}

// writeTarEntry writes a single directory or file entry into the tar writer.
// It normalizes the path, validates against traversal, and strips host identity.
func writeTarEntry(tw *tar.Writer, path, rel string, d fs.DirEntry) error {
	fi, err := d.Info()
	if err != nil {
		return fmt.Errorf("stat %s: %w", rel, err)
	}

	header, err := tar.FileInfoHeader(fi, "")
	if err != nil {
		return fmt.Errorf("creating tar header for %s: %w", rel, err)
	}

	// Normalize path and validate against traversal.
	cleanRel := filepath.ToSlash(filepath.Clean(rel))
	if strings.HasPrefix(cleanRel, "..") {
		return fmt.Errorf("refusing to archive path with traversal component: %s", rel)
	}
	header.Name = cleanRel

	// Strip host identity to avoid information disclosure and permission
	// mismatch when the archive is restored on a different machine.
	header.Uid = 0
	header.Gid = 0
	header.Uname = ""
	header.Gname = ""

	if err := tw.WriteHeader(header); err != nil {
		return fmt.Errorf("writing tar header for %s: %w", rel, err)
	}

	if d.IsDir() {
		return nil
	}

	return addFileToTar(tw, path, rel)
}

// addFileToTar copies a single file into the tar writer.
func addFileToTar(tw *tar.Writer, path, rel string) error {
	f, err := os.Open(path) //nolint:gosec // G304: path comes from the filepath.Walk over the data-dir backup root, not external input
	if err != nil {
		return fmt.Errorf("opening %s: %w", rel, err)
	}

	_, copyErr := io.Copy(tw, f)
	if err := f.Close(); err != nil && copyErr == nil {
		return fmt.Errorf("closing %s: %w", rel, err)
	}
	if copyErr != nil {
		return fmt.Errorf("writing %s to archive: %w", rel, copyErr)
	}
	return nil
}

// isUserAbort returns true if the error is a huh user-abort (Ctrl-C/Esc).
func isUserAbort(err error) bool {
	return errors.Is(err, huh.ErrUserAborted)
}
