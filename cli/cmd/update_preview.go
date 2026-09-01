package cmd

import (
	"fmt"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

// The read-only halves of `synthorg update`: --check and --dry-run, plus the
// verdict rendering they share. Neither writes anything, which is why they
// sit apart from the mutating flow in update.go.

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
	result, err := checkForChannel(ctx, channel)
	if err != nil {
		return fmt.Errorf("checking for updates: %w", err)
	}
	if result.UpdateAvail {
		// LatestVersion is a remote tag name, and this is the line `update
		// --check` exists to print: on the terse path it is the ONLY thing an
		// operator sees before deciding to run the install.
		out.Step(fmt.Sprintf("Update available: %s (current: %s)",
			versionLabel(result.LatestVersion), versionLabel(result.CurrentVersion)))
		out.HintNextStep("Run 'synthorg update' to apply")
		return NewExitError(ExitUpdateAvail, nil)
	}
	out.Success(fmt.Sprintf("Up to date (%s)", versionLabel(result.CurrentVersion)))
	out.HintGuidance("Exit code 0 means up to date; exit code 10 means an update is available.")
	return nil
}

// runUpdateDryRun shows what an update would do without executing.
//
// It runs the same check --check runs, because "would this update anything"
// is one question and the two preview surfaces must answer it the same way.
// Each half is therefore judged on its SCOPED verdict, in scope AND behind,
// never on availability alone: the scope flags decide which HALVES an
// invocation may touch, so an out-of-scope half being behind must not
// register as "yes" under a "CLI update" / "Image update" label.
func runUpdateDryRun(cmd *cobra.Command, state config.State) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	channel := state.Channel
	if channel == "" {
		channel = "stable"
	}
	result, err := checkForChannel(ctx, channel)
	if err != nil {
		return fmt.Errorf("checking for updates: %w", err)
	}
	// The tag is tracked in the CLI's own config and moves separately from the
	// binary, so it is compared to the release rather than assumed to share
	// the binary's answer: an operator who ran --cli-only has one current and
	// the other behind.
	imagesBehind := imagesAreBehind(state.ImageTag, result.LatestVersion)

	out.Section("Dry run: update preview")
	out.KeyValue("Current CLI", version.Version)
	out.KeyValue("Current images", state.ImageTag)
	out.KeyValue("Channel", channel)
	// A remote tag name, so it takes the same scrub every other remote label
	// on this path takes.
	out.KeyValue("Latest release", versionLabel(result.LatestVersion))
	cliDue := !updateImagesOnly && result.UpdateAvail
	imagesDue := !updateCLIOnly && imagesBehind
	out.KeyValue("CLI update", updateVerdict(!updateImagesOnly, result.UpdateAvail))
	out.KeyValue("Image update", updateVerdict(!updateCLIOnly, imagesBehind))
	// A restart is what pulling images costs, so it follows the pull rather
	// than the flag alone: promising one on an installation that will pull
	// nothing tells the operator to expect downtime they will not get.
	out.KeyValue("Restart after pull", restartVerdict(imagesDue, updateNoRestart))
	// Judged on the SCOPED verdicts, never on raw availability, so an
	// out-of-scope half being behind cannot produce a "run the update" hint
	// when the in-scope half has nothing to do.
	if !cliDue && !imagesDue {
		out.Success("Nothing to update; this installation is current.")
		return nil
	}
	out.HintNextStep("Remove --dry-run to execute the update")
	return nil
}

// imagesAreBehind reports whether the installed image tag trails the release.
//
// An unorderable tag is "not known to be behind" rather than an error. The tag
// is an operator-settable config value documented for private registries, and
// `latest` is what ImageTagForVersion falls back to, so neither is exotic;
// the update path itself only ever compares it for equality. Refusing the
// PREVIEW over a value the real command handles would make the read-only
// surface the stricter of the two.
func imagesAreBehind(installed, latest string) bool {
	behind, err := selfupdate.IsNewer(installed, latest)
	if err != nil {
		return installed != targetImageTag(latest)
	}
	return behind
}

// updateVerdict renders one half of the preview.
//
// Two conditions have to hold for a half to change, and they fail for
// different reasons an operator acts on differently: out of scope is their own
// flag, and already current is the installation. Each is therefore named, so a
// bare "no" never leaves the reader guessing which one they are seeing.
func updateVerdict(inScope, available bool) string {
	if !inScope {
		return "no (excluded by flags)"
	}
	if !available {
		return "no (already current)"
	}
	return "yes"
}

// restartVerdict renders the restart line, naming which of its two reasons
// applies. --no-restart is the operator's own choice; having nothing to pull
// is the installation's state, and only one of them changes if they run the
// update again tomorrow.
//
// Both inputs are parameters, like updateVerdict's: read off the package flag
// instead, this could only be exercised through the flag-mutating harness its
// sibling does not need.
func restartVerdict(pullsImages, noRestart bool) string {
	if !pullsImages {
		return "no (nothing to pull)"
	}
	if noRestart {
		return "no (excluded by flags)"
	}
	return "yes"
}
