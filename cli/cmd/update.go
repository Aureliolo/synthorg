package cmd

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var (
	updateDryRun        bool
	updateNoRestart     bool
	updateTimeout       string
	updateVerifyTimeout string
	updateCLIOnly       bool
	updateImagesOnly    bool
	updateCheck         bool
)

// checkForChannel is the update-check entrypoint, indirected through a
// package var so tests can drive the failure/abort path: the real
// selfupdate.CheckForChannel hits the GitHub API with no injection seam
// of its own.
var checkForChannel = selfupdate.CheckForChannel

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
	updateCmd.Flags().Bool("health-recovered", false, "carry the parent's installation-health verdict across re-exec (internal)")
	if err := updateCmd.Flags().MarkHidden("health-recovered"); err != nil {
		panic(err)
	}
	updateCmd.Flags().BoolVar(&updateDryRun, "dry-run", false, "show what would happen without executing")
	updateCmd.Flags().BoolVar(&updateNoRestart, "no-restart", false, "pull images but do not restart running containers")
	updateCmd.Flags().StringVar(&updateTimeout, "timeout", "90s", "post-restart health check timeout (e.g. 90s, 2m)")
	updateCmd.Flags().StringVar(&updateVerifyTimeout, "verify-timeout", "", "image signature/SLSA verification deadline (default: image_verify_timeout, 120s)")
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
	if err := validateUpdateTimeoutFlags(); err != nil {
		return err
	}
	return nil
}

// validateUpdateTimeoutFlags parses and range-checks the --timeout and
// --verify-timeout duration flags. Kept separate from validateUpdateFlags so
// the latter stays under the gocyclo ceiling.
func validateUpdateTimeoutFlags() error {
	d, err := time.ParseDuration(updateTimeout)
	if err != nil {
		return fmt.Errorf("invalid --timeout %q: %w", updateTimeout, err)
	}
	if d <= 0 {
		return fmt.Errorf("invalid --timeout %q: must be positive", updateTimeout)
	}
	if updateVerifyTimeout == "" {
		return nil
	}
	vd, err := time.ParseDuration(updateVerifyTimeout)
	if err != nil {
		return fmt.Errorf("invalid --verify-timeout %q: %w", updateVerifyTimeout, err)
	}
	if vd <= 0 {
		return fmt.Errorf("invalid --verify-timeout %q: must be positive", updateVerifyTimeout)
	}
	if vd < config.MinImageVerifyTimeout {
		return fmt.Errorf(
			"invalid --verify-timeout %q: %v is below the %v minimum floor; a shorter timeout would bypass cosign/SLSA verification by silently timing out",
			updateVerifyTimeout, vd, config.MinImageVerifyTimeout,
		)
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

	// --check / --dry-run are read-only modes that report and exit.
	if handled, err := runUpdateReadOnlyModes(cmd, state); handled {
		return err
	}

	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// Resolve installation health BEFORE updating the CLI binary so a
	// genuinely broken install aborts before the irreversible binary
	// swap, instead of swapping the binary and only discovering the
	// corruption in the re-exec'd child. The verdict is carried across
	// the re-exec on --health-recovered so the child neither re-prompts
	// nor loses the force-pull (recovered) signal.
	abort, recovered, healthErr := resolveInstallationHealth(cmd, state)
	if healthErr != nil {
		return healthErr
	}
	if abort {
		return nil
	}

	// CLI update (unless --images-only). A re-exec or hard error ends
	// the run here; otherwise the CLI was already current and we fall
	// through to the compose/image steps.
	if done, err := runCLIUpdateStep(cmd, state, recovered); done || err != nil {
		return err
	}

	// --cli-only: stop after CLI update.
	if updateCLIOnly {
		out.HintNextStep("Run 'synthorg update --images-only' to update container images separately.")
		return nil
	}

	if err := updateComposeAndImages(cmd, recovered); err != nil {
		return fmt.Errorf("updating compose and images: %w", err)
	}
	if updateImagesOnly {
		out.HintNextStep("Run 'synthorg update --cli-only' to update the CLI binary separately.")
	}
	return nil
}

// runUpdateReadOnlyModes dispatches the read-only --check / --dry-run
// modes. It returns handled=true (with that mode's result) when one
// fired, so the caller exits before the mutating update flow.
func runUpdateReadOnlyModes(cmd *cobra.Command, state config.State) (bool, error) {
	if updateCheck {
		return true, runUpdateCheck(cmd, state)
	}
	if updateDryRun {
		runUpdateDryRun(cmd, state)
		return true, nil
	}
	return false, nil
}

// runCLIUpdateStep runs the CLI self-update unless --images-only. It
// returns done=true when the run is finished here -- either a re-exec
// occurred (the child continues the update) or a hard error must
// propagate. done=false means the CLI was already current and the
// caller should proceed to the compose/image steps.
func runCLIUpdateStep(cmd *cobra.Command, state config.State, recovered bool) (bool, error) {
	if updateImagesOnly {
		return false, nil
	}
	err := updateCLI(cmd, state.AutoUpdateCLI)
	if errors.Is(err, errReexec) {
		return true, reexecUpdate(cmd, recovered)
	}
	if err != nil {
		return true, fmt.Errorf("updating CLI binary: %w", err)
	}
	return false, nil
}

// resolveInstallationHealth determines whether the install is healthy
// enough to proceed, returning (abort, recovered, error). After a
// re-exec the parent already ran the interactive check, so the child
// (--skip-cli-update) trusts the verdict carried on --health-recovered
// rather than re-prompting for the same corruption.
func resolveInstallationHealth(cmd *cobra.Command, state config.State) (bool, bool, error) {
	skipCLI, err := cmd.Flags().GetBool("skip-cli-update")
	if err != nil {
		return false, false, fmt.Errorf("getting skip-cli-update flag: %w", err)
	}
	if skipCLI {
		recovered, recErr := cmd.Flags().GetBool("health-recovered")
		if recErr != nil {
			return false, false, fmt.Errorf("getting health-recovered flag: %w", recErr)
		}
		return false, recovered, nil
	}
	return checkInstallationHealth(cmd, state)
}

// updateComposeAndImages reloads config, refreshes the compose template,
// and pulls new container images. Separated from runUpdate for readability.
// recovered is the installation-health verdict resolved up front (before
// the CLI binary swap), forcing a pull when images are missing.
func updateComposeAndImages(cmd *cobra.Command, recovered bool) error {
	state, err := config.Load(GetGlobalOpts(cmd.Context()).DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
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
func runUpdateDryRun(cmd *cobra.Command, state config.State) {
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
}

// handleDeclinedCompose warns the user that new images may not work with
// their current compose configuration and offers to update images anyway.
func handleDeclinedCompose(cmd *cobra.Command, state config.State, recovered bool) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

	errOut.Warn("New images may not work correctly with your current compose configuration.")
	ok, err := confirmUpdateWithDefault(cmd.Context(),
		"Still update container images? (Only image references in compose.yml will be updated, template changes will not be applied.)",
		false, false,
	)
	if err != nil {
		return fmt.Errorf("confirming compose apply: %w", err)
	}
	if !ok {
		out.Step("Image update skipped.")
		out.HintNextStep("Run 'synthorg init' then 'synthorg update' when ready.")
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
// re-exec the updated binary. runChangelogWalk has already shown what the
// jump contains (the changelog, or its terse offline notice when that could
// not be fetched), so this function goes straight to the install confirm.
func downloadAndApplyCLI(ctx context.Context, out *ui.UI, result selfupdate.CheckResult, autoAccept bool) error {
	// The confirm prompt renders through huh, which applies none of the
	// UI's own scrubbing, and the target version is a remote tag name: this
	// is the one string the operator reads before consenting.
	ok, err := confirmUpdate(ctx, fmt.Sprintf("Update CLI from %s to %s?",
		ui.SanitizeUntrustedLine(result.CurrentVersion),
		ui.SanitizeUntrustedLine(result.LatestVersion)), autoAccept)
	if err != nil {
		return fmt.Errorf("confirming CLI update: %w", err)
	}
	if !ok {
		return nil
	}
	if autoAccept {
		// Name the setting that answered, or an install nobody confirmed
		// looks like the confirm prompt went missing. StepAlways because
		// --quiet is precisely how an unattended install is invoked, and
		// this line is the only record that it was unattended.
		out.StepAlways(fmt.Sprintf("auto_update_cli is set: installing %s without confirmation.",
			ui.SanitizeUntrustedLine(result.LatestVersion)))
	}

	// Surface a permission error in the install directory before the
	// (slow) download but only after the user has consented; otherwise the
	// probe would create and remove a temp file in the binary directory on
	// every declined update check. The download is the expensive step, so
	// failing here still avoids the "wait through a multi-MB download then
	// fail at Replace" scenario.
	if err := selfupdate.ProbeInstallDirWritable(); err != nil {
		return fmt.Errorf(
			"cannot update CLI in place; re-run as an administrator "+
				"or move the binary to a writable directory: %w", err,
		)
	}

	sp := out.StartSpinner("Downloading CLI update...")
	binary, err := selfupdate.Download(ctx, result.AssetURL, result.ChecksumURL, result.SigstoreBundURL)
	if err != nil {
		sp.Error("Download failed")
		// A signature failure is not something the running binary can
		// resolve: the identity it trusts is compiled in, so once that pin
		// stops matching what releases carry, every future update fails
		// identically. Name the way out, or the operator is left with an
		// unverified manual download as the obvious workaround.
		if errors.Is(err, selfupdate.ErrSigstoreVerification) {
			out.HintError(fmt.Sprintf(
				"This binary cannot verify the current release. Reinstall from "+
					"%s/releases/latest to pick up the identity it was signed under.",
				version.RepoURL))
		}
		return fmt.Errorf("downloading update: %w", err)
	}
	sp.Success("Download complete")

	if err := selfupdate.Replace(binary); err != nil {
		return fmt.Errorf("replacing binary: %w", err)
	}
	installed := ui.SanitizeUntrustedLine(result.LatestVersion)
	out.Success(fmt.Sprintf("CLI updated to %s", installed))
	out.HintNextStep(fmt.Sprintf("Release notes: %s/releases/tag/v%s",
		version.RepoURL, strings.TrimPrefix(installed, "v")))
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
	if version.Version == config.SourceBuildVersion {
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

	result, err := checkForChannel(ctx, channel)
	if err != nil {
		// A failed check means we cannot tell whether the CLI is current,
		// so abort rather than continue blindly into the compose/image
		// pull -- those images live on ghcr.io (GitHub), so a genuine
		// GitHub outage would fail the pull too. --images-only bypasses
		// this check entirely for a deliberate image-only refresh. Wrap in
		// an ExitError so Execute() surfaces this styled message + hint as
		// the sole output (a plain error would be re-printed on top).
		errUI.Error(fmt.Sprintf("Could not check for updates: %v", err))
		errUI.HintError("GitHub may be having a transient issue -- re-run 'synthorg update' shortly, " +
			"or 'synthorg update --images-only' to refresh container images without the CLI check.")
		return NewExitError(ExitRuntime, fmt.Errorf("checking for updates: %w", err))
	}

	if !result.UpdateAvail {
		out.Success(fmt.Sprintf("CLI is up to date (%s)", result.CurrentVersion))
		return nil
	}

	// Failure is non-fatal here: an unreadable config only degrades the
	// changelog walk to its offline fallback, and the update proceeds.
	state, _ := config.Load(opts.DataDir)
	runChangelogWalk(ctx, cmd, result, state, autoAcceptCLI)

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

// targetImageTag converts a CLI version string to a Docker image tag.
//
// This is the same question `init` answers, so it shares the answer: a
// source build resolving to `latest` here would pull the last stable
// release over the `dev` images `init` pinned, and persist that tag back,
// undoing the pin on the first update.
//
// ver may come from the GitHub Releases API, so the shared resolver
// validates it at that trust boundary; compose.Generate validates again
// downstream.
func targetImageTag(ver string) string {
	return config.ImageTagForVersion(ver)
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
// verifyAndPinForUpdate runs cache-aware verification of both SynthOrg and
// DHI images using the new tag, writes the compose file with the verified
// SynthOrg pins, and returns the merged pin map (SynthOrg bare-name keys
// plus "dhi:*" keys) ready for pullAndPersist to merge into state.
//
// The compose file is always (re)written -- update's job is to point the
// running stack at the new tag, even when verification was a cache hit.
//
// Precedence on the verification deadline: the --verify-timeout flag wins
// when set (operator intent for this invocation), otherwise the resolved
// Tunables.ImageVerifyTimeout applies. This is kept SEPARATE from --timeout,
// which governs only the post-restart health check: the two budgets have
// different defaults (verification 120s vs health 90s), so conflating them
// would silently shorten the verification deadline to the health value. The
// tunable is validated at PersistentPreRunE so an unparseable override fails
// fast upstream; --verify-timeout is validated in validateUpdateFlags.
func verifyAndPinForUpdate(ctx context.Context, info docker.Info, state config.State, tag, safeDir string, preserveCompose bool, out *ui.UI, errOut *ui.UI) (map[string]string, error) {
	updatedState := state
	updatedState.ImageTag = tag

	if GetGlobalOpts(ctx).SkipVerify {
		errOut.WarnAlways("Image verification skipped (--skip-verify). Containers are NOT verified.")
		if err := writeOrPatchCompose(updatedState, nil, safeDir, preserveCompose); err != nil {
			return nil, err
		}
		return nil, nil
	}

	verifyTimeout := GetGlobalOpts(ctx).Tunables.ImageVerifyTimeout
	if updateVerifyTimeout != "" {
		if d, err := time.ParseDuration(updateVerifyTimeout); err == nil && d > 0 {
			verifyTimeout = d
		}
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
		// Images were pulled but nothing is running to restart; point the
		// operator at the command that launches the freshly-pulled stack.
		uiOut.HintNextStep("Run 'synthorg start' to launch the updated stack.")
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
