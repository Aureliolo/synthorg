package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var (
	updateDryRun     bool
	updateNoRestart  bool
	updateTimeout    string
	updateCLIOnly    bool
	updateImagesOnly bool
	updateCheck      bool
)

var updateCmd = &cobra.Command{
	Use:   "update",
	Short: "Update CLI, refresh compose template, and pull new container images",
	Long: `Bring the local installation up to the channel's latest version.

Self-updates the CLI binary, regenerates compose.yml from the
embedded template, then pulls the matching container images
(verifying signatures and SLSA attestations) and restarts the
running stack. Pass --cli-only or --images-only to scope the
update, --check to exit 10 if an update is available without
applying it, --dry-run to preview the planned changes, or
--no-restart to pull images but leave the running containers
untouched.`,
	Example: `  synthorg update                # update CLI + images
  synthorg update --cli-only     # update CLI binary only
  synthorg update --images-only  # update container images only
  synthorg update --check        # check for updates (exit code 0 or 10)
  synthorg update --dry-run      # preview what would change
  synthorg update --no-restart   # pull images but skip restart`,
	RunE: runUpdate,
}

func init() {
	updateCmd.Flags().Bool("skip-cli-update", false, "skip CLI self-update check (used internally after re-exec)")
	if err := updateCmd.Flags().MarkHidden("skip-cli-update"); err != nil {
		panic(err)
	}
	updateCmd.Flags().BoolVar(&updateDryRun, "dry-run", false, "show what would happen without executing")
	updateCmd.Flags().BoolVar(&updateNoRestart, "no-restart", false, "pull images but do not restart running containers")
	updateCmd.Flags().StringVar(&updateTimeout, "timeout", "90s", "health check and verification timeout")
	updateCmd.Flags().BoolVar(&updateCLIOnly, "cli-only", false, "only update the CLI binary")
	updateCmd.Flags().BoolVar(&updateImagesOnly, "images-only", false, "only update container images (skip CLI)")
	updateCmd.Flags().BoolVar(&updateCheck, "check", false, "check for updates and exit (0=current, 10=available)")
	updateCmd.GroupID = "lifecycle"
	rootCmd.AddCommand(updateCmd)
}

func validateUpdateFlags() error {
	if updateCLIOnly && updateImagesOnly {
		return fmt.Errorf("--cli-only and --images-only are mutually exclusive")
	}
	if updateCheck && updateDryRun {
		return fmt.Errorf("--check and --dry-run are mutually exclusive")
	}
	if _, err := time.ParseDuration(updateTimeout); err != nil {
		return fmt.Errorf("invalid --timeout %q: %w", updateTimeout, err)
	}
	return nil
}

func runUpdate(cmd *cobra.Command, _ []string) error {
	if err := validateUpdateFlags(); err != nil {
		return fmt.Errorf("validating update flags: %w", err)
	}

	// Load config early for auto-behavior flags and --check mode.
	// Failure is non-fatal (pre-init, first run) -- auto-behavior defaults to false.
	state, _ := config.Load(GetGlobalOpts(cmd.Context()).DataDir)

	// --check: just check for updates and exit with appropriate code.
	if updateCheck {
		return runUpdateCheck(cmd, state)
	}

	// --dry-run: show what would happen without executing.
	if updateDryRun {
		return runUpdateDryRun(cmd, state)
	}

	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// CLI update (unless --images-only).
	if !updateImagesOnly {
		if err := updateCLI(cmd, state.AutoUpdateCLI); errors.Is(err, errReexec) {
			return reexecUpdate(cmd)
		} else if err != nil {
			return fmt.Errorf("updating CLI binary: %w", err)
		}
	}

	// --cli-only: stop after CLI update.
	if updateCLIOnly {
		out.HintGuidance("Run 'synthorg update --images-only' to update container images separately.")
		return nil
	}

	if err := updateComposeAndImages(cmd); err != nil {
		return fmt.Errorf("updating compose and images: %w", err)
	}
	if updateImagesOnly {
		out.HintGuidance("Run 'synthorg update --cli-only' to update the CLI binary separately.")
	}
	return nil
}

// updateComposeAndImages reloads config, refreshes the compose template,
// and pulls new container images. Separated from runUpdate for readability.
func updateComposeAndImages(cmd *cobra.Command) error {
	state, err := config.Load(GetGlobalOpts(cmd.Context()).DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	abort, recovered, healthErr := checkInstallationHealth(cmd, state)
	if healthErr != nil {
		return healthErr
	}
	if abort {
		return nil
	}

	applied, err := refreshCompose(cmd, state, recovered)
	if err != nil {
		return fmt.Errorf("refreshing compose template: %w", err)
	}
	if !applied {
		return handleDeclinedCompose(cmd, state, recovered)
	}
	return updateContainerImages(cmd, state, false, recovered)
}

// runUpdateCheck checks for available updates and exits with code 0 (current)
// or 10 (update available).
func runUpdateCheck(cmd *cobra.Command, state config.State) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	channel := state.Channel
	if channel == "" {
		channel = "stable"
	}
	result, err := selfupdate.CheckForChannel(ctx, channel)
	if err != nil {
		return fmt.Errorf("checking for updates: %w", err)
	}
	if result.UpdateAvail {
		out.Step(fmt.Sprintf("Update available: %s (current: %s)", result.LatestVersion, result.CurrentVersion))
		out.HintNextStep("Run 'synthorg update' to apply")
		return NewExitError(ExitUpdateAvail, nil)
	}
	out.Success(fmt.Sprintf("Up to date (%s)", result.CurrentVersion))
	out.HintGuidance("Exit code 0 means up to date; exit code 10 means an update is available.")
	return nil
}

// runUpdateDryRun shows what an update would do without executing.
func runUpdateDryRun(cmd *cobra.Command, state config.State) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	out.Section("Dry run: update preview")
	out.KeyValue("Current CLI", version.Version)
	out.KeyValue("Current images", state.ImageTag)
	out.KeyValue("Channel", state.Channel)
	out.KeyValue("CLI update", boolToYesNo(!updateImagesOnly))
	out.KeyValue("Image update", boolToYesNo(!updateCLIOnly))
	out.KeyValue("Restart after pull", boolToYesNo(!updateNoRestart))
	out.HintNextStep("Remove --dry-run to execute the update")
	return nil
}

// handleDeclinedCompose warns the user that new images may not work with
// their current compose configuration and offers to update images anyway.
func handleDeclinedCompose(cmd *cobra.Command, state config.State, recovered bool) error {
	_, _ = fmt.Fprintln(cmd.OutOrStdout(),
		"Warning: new images may not work correctly with your current compose configuration.")
	ok, err := confirmUpdateWithDefault(cmd.Context(),
		"Still update container images? (Only image references in compose.yml will be updated, template changes will not be applied.)",
		false, false,
	)
	if err != nil {
		return fmt.Errorf("confirming compose apply: %w", err)
	}
	if !ok {
		_, _ = fmt.Fprintln(cmd.OutOrStdout(), "Image update skipped. Run 'synthorg init' then 'synthorg update' when ready.")
		return nil
	}
	return updateContainerImages(cmd, state, true, recovered)
}

// isDevChannelMismatch returns true when the running binary is a dev build
// but the update channel is not "dev". This helps users who installed a dev
// build but forgot to set the channel.
func isDevChannelMismatch(channel, ver string) bool {
	return channel != "dev" && strings.Contains(ver, "-dev.")
}

// downloadAndApplyCLI downloads, verifies, and replaces the current binary
// with the new version. Returns errReexec on success so the caller can
// re-exec the updated binary. The "New version available" notice is now
// printed by runChangelogWalk (or its offline fallback) before this is
// called, so we go straight to the install confirm prompt.
func downloadAndApplyCLI(ctx context.Context, out *ui.UI, result selfupdate.CheckResult, autoAccept bool) error {
	ok, err := confirmUpdate(ctx, fmt.Sprintf("Update CLI from %s to %s?", result.CurrentVersion, result.LatestVersion), autoAccept)
	if err != nil {
		return fmt.Errorf("confirming CLI update: %w", err)
	}
	if !ok {
		return nil
	}

	// Surface a permission error in the install directory before the
	// download starts; otherwise the user waits through a multi-MB
	// transfer only to fail at the final ``Replace`` step.
	if err := selfupdate.ProbeInstallDirWritable(); err != nil {
		return fmt.Errorf(
			"cannot update CLI in place; re-run as an administrator "+
				"or move the binary to a writable directory: %w", err,
		)
	}

	out.Step("Downloading...")
	binary, err := selfupdate.Download(ctx, result.AssetURL, result.ChecksumURL, result.SigstoreBundURL)
	if err != nil {
		return fmt.Errorf("downloading update: %w", err)
	}

	if err := selfupdate.Replace(binary); err != nil {
		return fmt.Errorf("replacing binary: %w", err)
	}
	out.Success(fmt.Sprintf("CLI updated to %s", result.LatestVersion))
	out.HintNextStep(fmt.Sprintf("Release notes: %s/releases/tag/v%s",
		version.RepoURL, strings.TrimPrefix(result.LatestVersion, "v")))
	if !autoAccept {
		out.HintTip("Run 'synthorg config set auto_update_cli true' to auto-accept CLI updates.")
	}

	return errReexec
}

// errReexec is a sentinel error returned by updateCLI when the binary was
// replaced and the new binary should be re-executed to continue the update.
// The caller (runUpdate) handles this by spawning the new binary.
var errReexec = errors.New("cli updated, re-exec required")

// updateCLI checks for a new CLI release and optionally applies it.
// Returns errReexec if the binary was replaced (caller must re-exec).
// autoAcceptCLI is true when auto_update_cli config key is set.
func updateCLI(cmd *cobra.Command, autoAcceptCLI bool) error {
	// After re-exec the CLI was just replaced -- skip the redundant check.
	skip, err := cmd.Flags().GetBool("skip-cli-update")
	if err != nil {
		return fmt.Errorf("getting skip-cli-update flag: %w", err)
	}
	if skip {
		return nil
	}

	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errUI := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

	// Warn on dev builds.
	if version.Version == "dev" {
		out.Warn("Running a dev build -- update check will always report an update available.")
	}

	channel := resolveUpdateChannel(ctx)
	if channel == "dev" {
		out.Step("Checking for updates (dev channel)...")
	} else {
		out.Step("Checking for updates...")
	}

	if isDevChannelMismatch(channel, version.Version) {
		out.Warn("Running a dev build but update channel is \"stable\". Dev releases will not appear. Run 'synthorg config set channel dev' to receive dev updates.")
	}

	result, err := selfupdate.CheckForChannel(ctx, channel)
	if err != nil {
		errUI.Warn(fmt.Sprintf("Could not check for updates: %v", err))
		return nil
	}

	if !result.UpdateAvail {
		out.Success(fmt.Sprintf("CLI is up to date (%s)", result.CurrentVersion))
		return nil
	}

	state, _ := config.Load(opts.DataDir)
	runChangelogWalk(ctx, cmd, result, state)

	return downloadAndApplyCLI(ctx, out, result, autoAcceptCLI)
}

// resolveUpdateChannel reads the update channel from config, defaulting to
// "stable" if the config cannot be loaded or the channel is empty.
func resolveUpdateChannel(ctx context.Context) string {
	if state, err := config.Load(GetGlobalOpts(ctx).DataDir); err == nil && state.Channel != "" {
		return state.Channel
	}
	return "stable"
}

// ChildExitError and ChildExitCode are defined in exitcodes.go.

// reexecUpdate spawns the new binary with the same arguments so the rest
// of the update (compose refresh, image pull) uses the new embedded template.
// The CLI update step already ran, so the new binary will see "up to date"
// and proceed directly to compose + images.
//
// Arguments are reconstructed from known flag values rather than forwarding
// raw os.Args to avoid silently propagating unexpected flags.
//
// Returns a *ChildExitError if the child exits non-zero, so the caller
// can propagate the exit code rather than printing a generic error.
func reexecUpdate(cmd *cobra.Command) error {
	_, _ = fmt.Fprintln(cmd.OutOrStdout(), "Re-launching updated CLI to continue...")

	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("finding executable path: %w", err)
	}
	// Resolve symlinks to match the pattern in uninstall.go --
	// selfupdate.Replace writes to the resolved path.
	if resolved, resolveErr := filepath.EvalSymlinks(execPath); resolveErr == nil {
		execPath = resolved
	} else {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: could not resolve executable symlink: %v\n", resolveErr)
	}

	// Reconstruct args from known flags instead of forwarding os.Args
	// to avoid silently propagating unexpected flags.
	reArgs := []string{"update", "--skip-cli-update"}
	if flagDataDir != "" {
		reArgs = append(reArgs, "--data-dir", flagDataDir)
	}
	if flagSkipVerify {
		reArgs = append(reArgs, "--skip-verify")
		_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "Warning: --skip-verify is being carried forward to the re-launched CLI.")
	}
	if flagQuiet {
		reArgs = append(reArgs, "--quiet")
	}
	for range flagVerbose {
		reArgs = append(reArgs, "-v")
	}
	if flagNoColor {
		reArgs = append(reArgs, "--no-color")
	}
	if flagPlain {
		reArgs = append(reArgs, "--plain")
	}
	if flagJSON {
		reArgs = append(reArgs, "--json")
	}
	if flagYes {
		reArgs = append(reArgs, "--yes")
	}
	// Forward per-command flags added in PR 3.
	if updateNoRestart {
		reArgs = append(reArgs, "--no-restart")
	}
	if cmd.Flags().Changed("timeout") {
		reArgs = append(reArgs, "--timeout", updateTimeout)
	}
	if updateImagesOnly {
		reArgs = append(reArgs, "--images-only")
	}
	if updateCLIOnly {
		reArgs = append(reArgs, "--cli-only")
	}

	c := exec.CommandContext(cmd.Context(), execPath, reArgs...)
	c.Stdin = os.Stdin
	c.Stdout = cmd.OutOrStdout()
	c.Stderr = cmd.ErrOrStderr()

	if runErr := c.Run(); runErr != nil {
		// Preserve the child's exit code so the parent can propagate it.
		if exitErr, ok := errors.AsType[*exec.ExitError](runErr); ok {
			return &ChildExitError{Code: exitErr.ExitCode()}
		}
		return fmt.Errorf("re-launching updated CLI: %w", runErr)
	}
	return nil
}

// targetImageTag converts a CLI version string to a Docker image tag.
// Strips the "v" prefix and maps dev/empty/invalid to "latest".
// Validates the tag at the trust boundary (version may come from the
// GitHub Releases API); compose.Generate also validates downstream.
func targetImageTag(ver string) string {
	tag := strings.TrimPrefix(ver, "v")
	if tag == "" || tag == "dev" {
		return "latest"
	}
	if !config.IsValidImageTag(tag) {
		return "latest"
	}
	return tag
}

// updateContainerImages offers to update container images to match the
// current CLI version. Skips if images already match unless forceRefresh
// is true (recovery mode -- images may be missing despite matching tag).
// When preserveCompose is true, only image references are patched in the
// existing compose instead of regenerating from the template.
func updateContainerImages(cmd *cobra.Command, state config.State, preserveCompose bool, forceRefresh bool) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := cmd.OutOrStdout()
	uiOut := ui.NewUIWithOptions(out, opts.UIOptions())

	tag := targetImageTag(version.Version)

	safeDir, err := safeStateDir(state)
	if err != nil {
		return fmt.Errorf("resolving data directory: %w", err)
	}

	// Check if container images already match the target version.
	if state.ImageTag == tag && !forceRefresh {
		_, _ = fmt.Fprintf(out, "Container images already at %s\n", tag)
		return nil
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: Docker not available, skipping image update: %v\n", err)
		return nil
	}

	manualPull := !state.AutoPull
	ok, err := confirmUpdate(ctx, fmt.Sprintf("Update container images from %s to %s?", state.ImageTag, tag), state.AutoPull)
	if err != nil {
		return fmt.Errorf("confirming image update: %w", err)
	}
	if !ok {
		return nil
	}

	previousIDs := captureImageIDsForCleanup(ctx, cmd, info, state)

	updatedState, err := pullAndPersist(ctx, cmd, info, state, tag, safeDir, preserveCompose)
	if err != nil {
		return fmt.Errorf("pulling updated images: %w", err)
	}

	if err := postPullActions(cmd, info, safeDir, state, updatedState, previousIDs); err != nil {
		return fmt.Errorf("running post-pull actions: %w", err)
	}
	if manualPull {
		uiOut.HintTip("Run 'synthorg config set auto_pull true' to auto-accept image pulls.")
	}
	return nil
}

// captureImageIDsForCleanup records current image IDs before a pull so
// auto-cleanup can remove them afterwards. Best-effort: returns nil on
// genuine errors, but keeps the partial snapshot when some services are
// simply not pulled yet (e.g. fine-tune) so the services that ARE
// present on disk still get rollback-image protection after the update.
func captureImageIDsForCleanup(ctx context.Context, cmd *cobra.Command, info docker.Info, state config.State) map[string]bool {
	if !state.AutoCleanup {
		return nil
	}
	ids, err := collectCurrentImageIDs(ctx, info, state)
	if err != nil {
		if errors.Is(err, errImageNotLocal) {
			// Some services were never pulled (partial install, fresh
			// machine, fine-tune skipped). Use whatever partial snapshot
			// collectCurrentImageIDs managed to build -- present services
			// still get rollback protection; missing services have
			// nothing to protect.
			return ids
		}
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
			"Warning: could not capture previous image IDs for auto-cleanup: %v\n", err)
		return nil
	}
	return ids
}

// postPullActions handles restart, auto-cleanup, and old image hints after
// a successful image pull.
func postPullActions(cmd *cobra.Command, info docker.Info, safeDir string, oldState, updatedState config.State, previousIDs map[string]bool) error {
	restarted, restartErr := restartIfRunning(cmd, info, safeDir, updatedState)
	if restartErr != nil {
		return restartErr
	}

	// Auto-cleanup old images if enabled, otherwise show a passive hint.
	// Auto-cleanup runs regardless of restart (docker rmi skips in-use images).
	// The passive hint only shows after restart (old containers are stopped).
	if oldState.AutoCleanup {
		autoCleanupOldImages(cmd, info, updatedState, previousIDs)
	} else if restarted {
		hintOldImages(cmd, info, updatedState)
	}
	return nil
}

// confirmUpdate prompts the user to confirm an update action.
// Returns (true, nil) if --yes/non-interactive (auto-accept), config auto-accept,
// or user confirms. Default is yes.
func confirmUpdate(ctx context.Context, title string, autoAccept bool) (bool, error) {
	return confirmUpdateWithDefault(ctx, title, true, autoAccept)
}

// confirmUpdateWithDefault prompts the user with a configurable default.
// Respects --yes flag, config auto-accept keys, and SYNTHORG_YES env var.
// Precedence: --yes > config auto key > interactive prompt > non-interactive default.
func confirmUpdateWithDefault(ctx context.Context, title string, defaultVal bool, autoAccept bool) (bool, error) {
	if !GetGlobalOpts(ctx).ShouldPrompt() {
		return defaultVal, nil // --yes or non-interactive
	}
	if autoAccept {
		return true, nil // config auto-accept key
	}
	proceed := defaultVal
	form := huh.NewForm(huh.NewGroup(
		huh.NewConfirm().Title(title).Value(&proceed),
	))
	if err := form.Run(); err != nil {
		return false, err
	}
	return proceed, nil
}

// pullAndPersist verifies images, updates compose, pulls, and persists config.
// If any step fails, the previous compose.yml is restored. When
// preserveCompose is true, only image references are patched in the
// existing compose instead of regenerating from the template.
// Returns the persisted state with updated ImageTag and VerifiedDigests.
func pullAndPersist(ctx context.Context, cmd *cobra.Command, info docker.Info, state config.State, tag, safeDir string, preserveCompose bool) (config.State, error) {
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// Back up existing compose.yml for rollback on failure.
	composePath := filepath.Join(safeDir, "compose.yml")
	backup, backupErr := os.ReadFile(composePath)
	backupExists := backupErr == nil

	rollback := func() {
		if backupExists {
			if wErr := os.WriteFile(composePath, backup, 0o600); wErr != nil {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
					"Warning: failed to restore compose.yml backup: %v\n", wErr)
			}
		} else {
			if rErr := os.Remove(composePath); rErr != nil && !errors.Is(rErr, os.ErrNotExist) {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
					"Warning: failed to clean up compose.yml: %v\n", rErr)
			}
		}
	}

	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

	// Verify + write compose atomically: compose.yml is only updated after
	// verification succeeds (or when --skip-verify explicitly skips it).
	digestPins, err := verifyAndPinForUpdate(ctx, info, state, tag, safeDir, preserveCompose, out, errOut)
	if err != nil {
		rollback()
		return state, err
	}

	// Use newly verified digest pins for the pull so standalone images
	// (sandbox, sidecar, fine-tune) resolve to pinned references. Merge
	// fresh pins on top of any existing ones (e.g. cached DHI keys when
	// the verify step hit the DHI cache) so the pull sees the union, not
	// just the freshly-verified subset.
	mergedPins := mergeVerifiedDigests(state.VerifiedDigests, digestPins)
	pullState := state
	pullState.ImageTag = tag
	pullState.VerifiedDigests = mergedPins
	if _, err := pullAllImages(ctx, info, safeDir, pullState, out); err != nil {
		rollback()
		return state, err
	}

	// Persist config only after successful pull so a failed pull
	// doesn't leave state claiming images are at the new version.
	// VerifiedImageTag tracks which tag the SynthOrg pins were verified
	// against; hasSynthOrgDigests rejects the cache when this drifts.
	updatedState := state
	updatedState.ImageTag = tag
	updatedState.VerifiedDigests = mergedPins
	updatedState.VerifiedImageTag = tag
	if err := config.Save(updatedState); err != nil {
		rollback()
		return state, fmt.Errorf("saving config: %w", err)
	}
	return updatedState, nil
}

// mergeVerifiedDigests overlays fresh pins on top of existing ones, returning
// a new map so the caller can assign without aliasing the original. Returns
// nil only when both inputs are nil/empty (lets compose.ParamsFromState's
// nil-pin fallback path fire when there is nothing to write).
func mergeVerifiedDigests(existing, fresh map[string]string) map[string]string {
	if len(existing) == 0 && len(fresh) == 0 {
		return nil
	}
	out := make(map[string]string, len(existing)+len(fresh))
	maps.Copy(out, existing)
	maps.Copy(out, fresh)
	return out
}

// verifyAndPinForUpdate runs cache-aware verification of both SynthOrg and
// DHI images using the new tag, writes the compose file with the verified
// SynthOrg pins, and returns the merged pin map (SynthOrg bare-name keys
// plus "dhi:*" keys) ready for pullAndPersist to merge into state.
//
// The compose file is always (re)written -- update's job is to point the
// running stack at the new tag, even when verification was a cache hit.
//
// Precedence on the verification deadline: --timeout flag wins when set
// (operator-level intent for this invocation), otherwise the resolved
// Tunables.ImageVerifyTimeout applies. The tunable is validated at
// PersistentPreRunE so an unparseable override fails fast upstream.
func verifyAndPinForUpdate(ctx context.Context, info docker.Info, state config.State, tag, safeDir string, preserveCompose bool, out *ui.UI, errOut *ui.UI) (map[string]string, error) {
	updatedState := state
	updatedState.ImageTag = tag

	if GetGlobalOpts(ctx).SkipVerify {
		errOut.Warn("Image verification skipped (--skip-verify). Containers are NOT verified.")
		if err := writeOrPatchCompose(updatedState, nil, safeDir, preserveCompose); err != nil {
			return nil, err
		}
		return nil, nil
	}

	verifyTimeout, _ := time.ParseDuration(updateTimeout)
	if verifyTimeout <= 0 {
		verifyTimeout = GetGlobalOpts(ctx).Tunables.ImageVerifyTimeout
	}
	verifyCtx, cancel := context.WithTimeout(ctx, verifyTimeout)
	defer cancel()

	result, err := verifyImagesWithCache(verifyCtx, info, updatedState, out, errOut)
	if err != nil {
		return nil, err
	}

	if err := writeOrPatchCompose(updatedState, synthOrgPins(result.Pins), safeDir, preserveCompose); err != nil {
		return nil, err
	}
	return result.Pins, nil
}

// restartIfRunning checks if containers are running and offers a restart.
// Returns (true, nil) when containers were restarted and passed health checks.
// Returns (false, nil) when restart was skipped or health check failed.
// Respects --no-restart flag, auto_restart config key, and --yes flag.
func restartIfRunning(cmd *cobra.Command, info docker.Info, safeDir string, state config.State) (bool, error) {
	opts := GetGlobalOpts(cmd.Context())
	uiOut := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// --no-restart: skip entirely.
	if updateNoRestart {
		uiOut.Success("Restart skipped (--no-restart)")
		uiOut.HintNextStep("Run 'synthorg stop && synthorg start' to apply new images.")
		return false, nil
	}

	ctx := cmd.Context()
	out := cmd.OutOrStdout()

	psOut, err := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "-q")
	if err != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
			"Warning: could not check container status: %v\nIf containers are running, restart manually: synthorg stop && synthorg start\n", err)
		return false, nil
	}
	if psOut == "" {
		return false, nil
	}

	// Precedence: --no-restart (above) > --yes > config auto key > prompt > non-interactive default.
	if state.AutoRestart {
		return performRestart(ctx, out, info, safeDir, state, opts.UIOptions())
	}

	if !opts.ShouldPrompt() {
		if opts.Yes {
			return performRestart(ctx, out, info, safeDir, state, opts.UIOptions())
		}
		_, _ = fmt.Fprintln(out, "Non-interactive mode: skipping restart. Run 'synthorg stop && synthorg start' to apply new images.")
		return false, nil
	}

	restart, err := confirmRestart()
	if err != nil {
		return false, err
	}
	if !restart {
		return false, nil
	}
	restarted, restartErr := performRestart(ctx, out, info, safeDir, state, opts.UIOptions())
	if restarted {
		uiOut.HintTip("Run 'synthorg config set auto_restart true' to auto-restart after updates.")
	}
	return restarted, restartErr
}

// performRestart stops, restarts, and health-checks containers.
func performRestart(ctx context.Context, out io.Writer, info docker.Info, safeDir string, state config.State, uiOpts ui.Options) (bool, error) {
	uiOut := ui.NewUIWithOptions(out, uiOpts)

	sp := uiOut.StartSpinner("Stopping containers...")
	if err := composeRunQuiet(ctx, info, safeDir, "down"); err != nil {
		sp.Error("Failed to stop containers")
		return false, fmt.Errorf("stopping containers: %w", err)
	}
	sp.Success("Containers stopped")

	sp = uiOut.StartSpinner("Starting containers...")
	if err := composeRunQuiet(ctx, info, safeDir, "up", "-d"); err != nil {
		sp.Error("Failed to start containers")
		return false, fmt.Errorf("restarting containers: %w", err)
	}
	sp.Success("Containers started")

	sp = uiOut.StartSpinner("Waiting for backend to become healthy...")
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", state.BackendPort)
	healthTimeout, _ := time.ParseDuration(updateTimeout)
	if healthTimeout <= 0 {
		healthTimeout = 90 * time.Second
	}
	if err := health.WaitForHealthy(ctx, healthURL, healthTimeout, 2*time.Second, 5*time.Second); err != nil {
		sp.Warn(fmt.Sprintf("Health check did not pass after restart: %v", err))
		return false, nil
	}
	sp.Success("Backend healthy")
	uiOut.KeyValue("Dashboard", fmt.Sprintf("http://localhost:%d", state.WebPort))
	return true, nil
}

func confirmRestart() (bool, error) {
	restart := true // default yes
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
