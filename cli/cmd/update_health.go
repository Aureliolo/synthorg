package cmd

import (
	"context"
	"fmt"
	"path/filepath"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/images"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

// checkInstallationHealth detects inconsistent state between config and the
// actual Docker/filesystem state (e.g. after a partial uninstall). Returns
// (abort, recovered, error): abort=true if the user declined recovery,
// recovered=true if recovery was chosen (caller should force refresh).
//
// Missing container images are treated as a "needs pull" signal rather
// than a corruption warning: update's normal flow will pull them, so we
// set recovered=true to force the pull (bypassing the early-exit when
// state.ImageTag already matches target) without surfacing a scary
// "installation incomplete" prompt. Only genuine corruption (missing
// config.json, missing compose.yml, missing JWT/settings key) raises
// the warning and prompts the user to recover.
func checkInstallationHealth(cmd *cobra.Command, state config.State) (bool, bool, error) {
	corruption, needsPull := detectInstallationIssues(cmd.Context(), state)

	if len(corruption) == 0 && !needsPull {
		return false, false, nil
	}

	if len(corruption) == 0 {
		// Only missing images -- silently force-refresh; update's
		// pull step handles this as the normal first-run path.
		return false, true, nil
	}

	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	out.Warn("Installation appears incomplete:")
	for _, issue := range corruption {
		_, _ = fmt.Fprintf(cmd.OutOrStdout(), "  - %s\n", issue)
	}

	abort, err := promptHealthRecover(cmd)
	if err != nil {
		return false, false, err
	}
	if abort {
		return true, false, nil
	}
	return false, true, nil
}

// detectInstallationIssues checks config, secrets, compose, and Docker
// images. Returns (corruption, needsPull) where corruption is
// human-readable messages for genuinely broken state that requires user
// acknowledgment, and needsPull is true when one or more service images
// are missing locally (expected state before the first `synthorg start`
// or after enabling a new service like sandbox).
func detectInstallationIssues(ctx context.Context, state config.State) (corruption []string, needsPull bool) {
	if !fileExists(config.StatePath(state.DataDir)) {
		corruption = append(corruption, "config.json is missing (no previous init)")
	}
	if state.JWTSecret == "" {
		corruption = append(corruption, "JWT secret is not configured")
	}
	if state.SettingsKey == "" {
		corruption = append(corruption, "settings encryption key is not configured")
	}

	safeDir, err := safeStateDir(state)
	if err != nil {
		corruption = append(corruption, fmt.Sprintf("data directory path issue: %v", err))
	} else if !fileExists(filepath.Join(safeDir, "compose.yml")) {
		corruption = append(corruption, "compose.yml is missing")
	}

	// Only check for missing images when we're re-verifying the same tag
	// the user already has. During a version upgrade, missing old-version
	// images are expected (they're about to be replaced) and the warning
	// is noise -- the pull will fix the install regardless.
	if state.ImageTag != "" && state.ImageTag == targetImageTag(version.Version) {
		// Use a shorter timeout for health check Docker calls to avoid
		// blocking the update flow if Docker is unresponsive (resolved
		// update_health_timeout tunable, default 15s).
		healthCtx, cancel := context.WithTimeout(ctx, GetGlobalOpts(ctx).Tunables.UpdateHealthTimeout)
		defer cancel()

		// Docker unavailability is handled gracefully by updateContainerImages
		// (warns and skips). Only check images when Docker is reachable.
		info, dockerErr := docker.Detect(healthCtx)
		if dockerErr == nil {
			if missing := detectMissingImages(healthCtx, info, state); len(missing) > 0 {
				needsPull = true
			}
		}
	}

	return corruption, needsPull
}

// promptHealthRecover asks the user whether to recover or run init.
// Returns (true, nil) if the user chose to abort.
func promptHealthRecover(cmd *cobra.Command) (bool, error) {
	opts := GetGlobalOpts(cmd.Context())
	if !opts.ShouldPrompt() {
		if opts.Yes {
			return false, nil // --yes: auto-recover (default is yes)
		}
		_, _ = fmt.Fprintln(cmd.OutOrStdout(),
			"\nNon-interactive mode: run 'synthorg init' to restore a clean installation.")
		return true, nil
	}

	var doRecover bool
	form := huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title("Recover by pulling images and regenerating compose?").
			Description("Choose 'No' to run 'synthorg init' for a fresh setup instead.").
			Value(&doRecover),
	))
	if err := runUpdateConfirm(cmd.Context(), form); err != nil {
		return false, err
	}
	if !doRecover {
		_, _ = fmt.Fprintln(cmd.OutOrStdout(), "Run 'synthorg init' to restore a clean installation.")
		return true, nil
	}
	return false, nil
}

// detectMissingImages checks which SynthOrg service images are missing locally
// for the given state. Uses docker image inspect which works with both
// digest-pinned (@sha256:...) and tag-based (:tag) references.
//
// Precondition: caller must have verified Docker is reachable (e.g. via
// docker.Detect). Context cancellation or daemon errors are ignored to
// avoid false positives; only an empty InspectID result (the documented
// "image not present locally" signal) counts as missing.
func detectMissingImages(ctx context.Context, info docker.Info, state config.State) []string {
	var missing []string
	for _, svc := range images.ServiceNames(state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault()) {
		ref := images.RefForService(svc, state.ImageTag, state.VerifiedDigests)
		id, err := images.InspectID(ctx, info.DockerPath, ref)
		if err != nil {
			if ctx.Err() != nil {
				// Context expired or cancelled -- can't reliably determine
				// image state, so don't report as missing.
				return missing
			}
			// Daemon or inspect error -- treat as indeterminate rather
			// than missing. We only want to flag images docker explicitly
			// reports as absent.
			continue
		}
		if id == "" {
			missing = append(missing, svc)
		}
	}
	return missing
}
