package cmd

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

// `update --check` and `update --dry-run` are the only two preview surfaces
// the CLI offers, and they answered "is an update available" differently: the
// dry run printed the SCOPE flags under labels that read as availability, so
// on an installation at the channel head --check said "Up to date" and exit 0
// while --dry-run seconds later reported both halves as "yes". The more
// detailed surface was the wrong one, which is the way round that costs an
// operator a needless update.

// dryRunOutput runs the preview against a scripted release and returns what
// an operator would read.
func dryRunOutput(t *testing.T, state config.State, result selfupdate.CheckResult) string {
	t.Helper()
	prev := checkForChannel
	checkForChannel = func(_ context.Context, _ string) (selfupdate.CheckResult, error) {
		return result, nil
	}
	t.Cleanup(func() { checkForChannel = prev })

	cmd := &cobra.Command{}
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Hints: "always"}))
	var buf bytes.Buffer
	cmd.SetOut(&buf)

	if err := runUpdateDryRun(cmd, state); err != nil {
		t.Fatalf("dry run returned %v", err)
	}
	return buf.String()
}

// withScopeFlags sets the package-level scope flags for one case and restores
// them afterwards, since they are command state rather than parameters.
func withScopeFlags(t *testing.T, cliOnly, imagesOnly, noRestart bool) {
	t.Helper()
	prevCLI, prevImages, prevRestart := updateCLIOnly, updateImagesOnly, updateNoRestart
	updateCLIOnly, updateImagesOnly, updateNoRestart = cliOnly, imagesOnly, noRestart
	t.Cleanup(func() {
		updateCLIOnly, updateImagesOnly, updateNoRestart = prevCLI, prevImages, prevRestart
	})
}

// withVersion pins the embedded build version, which is what the CLI half is
// compared against.
func withVersion(t *testing.T, v string) {
	t.Helper()
	prev := version.Version
	version.Version = v
	t.Cleanup(func() { version.Version = prev })
}

func TestRunUpdateDryRun_currentInstallationReportsNothingToDo(t *testing.T) {
	// The reported defect, exactly: everything is at the channel head.
	withScopeFlags(t, false, false, false)
	withVersion(t, "0.9.4-dev.168")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.4-dev.168"},
		selfupdate.CheckResult{
			UpdateAvail:    false,
			CurrentVersion: "0.9.4-dev.168",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireContains(t, got,
		"CLI update", "no (already current)",
		"Image update",
		"this installation is current",
	)
	if strings.Contains(got, "Remove --dry-run to execute") {
		t.Errorf("a current installation must not be told to run the update\n--- got ---\n%s", got)
	}
}

func TestRunUpdateDryRun_behindInstallationReportsBothHalves(t *testing.T) {
	// The complement, so the case above cannot pass by reporting "no" always.
	withScopeFlags(t, false, false, false)
	withVersion(t, "0.9.3")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.3"},
		selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.9.3",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireContains(t, got, "yes", "Remove --dry-run to execute")
	if strings.Contains(got, "already current") {
		t.Errorf("an installation behind the channel must not read as current\n--- got ---\n%s", got)
	}
}

func TestRunUpdateDryRun_halvesAreJudgedSeparately(t *testing.T) {
	// An operator who ran --cli-only has one half current and the other
	// behind, and the preview is the surface that has to say which.
	withScopeFlags(t, false, false, false)
	withVersion(t, "0.9.4-dev.168")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.3"},
		selfupdate.CheckResult{
			UpdateAvail:    false,
			CurrentVersion: "0.9.4-dev.168",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireContains(t, got, "no (already current)", "Remove --dry-run to execute")
	if strings.Contains(got, "this installation is current") {
		t.Errorf("images are behind, so the install is not current\n--- got ---\n%s", got)
	}
}

func TestRunUpdateDryRun_scopeAndCurrencyAreDistinguished(t *testing.T) {
	// Both mean the half will not change, and they fail for different
	// reasons: one is the operator's own flag, the other the installation.
	withScopeFlags(t, false, true, false)
	withVersion(t, "0.9.3")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.3"},
		selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.9.3",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireContains(t, got, "no (excluded by flags)")
}

func TestRunUpdateDryRun_cliOnlySuppressesTheRestart(t *testing.T) {
	// The mirror case, and the one that decides the restart line: a restart is
	// what pulling images costs, so excluding images must not promise one.
	withScopeFlags(t, true, false, false)
	withVersion(t, "0.9.3")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.3"},
		selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.9.3",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireContains(t, got, "no (excluded by flags)")
	requireLineValue(t, got, "Restart after pull", "no (nothing to pull)")
}

func TestRunUpdateDryRun_noRestartFlagIsNotTheSameAsNothingToPull(t *testing.T) {
	// Both suppress the restart and only one of them changes if the operator
	// runs the update again tomorrow, so the line names which applies.
	withScopeFlags(t, false, false, true)
	withVersion(t, "0.9.3")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.3"},
		selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.9.3",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireLineValue(t, got, "Restart after pull", "no (excluded by flags)")
}

// requireLineValue asserts the rendered value of one key-value line. The
// separator is whatever padding the UI chose, so the line is matched by its
// key and then read for its value rather than reconstructed.
func requireLineValue(t *testing.T, out, key, want string) {
	t.Helper()
	for line := range strings.SplitSeq(out, "\n") {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, key) {
			continue
		}
		value := strings.TrimSpace(
			strings.TrimPrefix(strings.TrimSpace(strings.TrimPrefix(trimmed, key)), ":"),
		)
		if value != want {
			t.Errorf("%s = %q, want %q\n--- got ---\n%s", key, value, want, out)
		}
		return
	}
	t.Errorf("no %q line in output\n--- got ---\n%s", key, out)
}

func TestRunUpdateDryRun_noRestartWhenNothingIsPulled(t *testing.T) {
	// A restart is what a pull costs, so promising one on an installation
	// that will pull nothing tells the operator to expect downtime.
	withScopeFlags(t, false, false, false)
	withVersion(t, "0.9.4-dev.168")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.4-dev.168"},
		selfupdate.CheckResult{
			UpdateAvail:    false,
			CurrentVersion: "0.9.4-dev.168",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireLineValue(t, got, "Restart after pull", "no (nothing to pull)")
}

func TestRunUpdateDryRun_scrubsSpoofedLatestVersion(t *testing.T) {
	// LatestVersion is a remote tag name and now reaches this surface too, so
	// it takes the same scrub the changelog walk's labels take.
	withScopeFlags(t, false, false, false)
	withVersion(t, "0.7.4")

	got := dryRunOutput(t,
		config.State{Channel: "stable", ImageTag: "0.7.4"},
		selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.7.4",
			LatestVersion:  spoofedTargetTag,
		},
	)

	requireContains(t, got, spoofedTargetSaf)
	if strings.Contains(got, spoofedTargetTag) {
		t.Errorf("the raw remote tag reached the terminal\n--- got ---\n%s", got)
	}
}

func TestRunUpdateDryRun_surfacesAFailedCheck(t *testing.T) {
	// Silence would be the old behaviour under a new name: a preview that
	// could not reach the channel must not report an answer it does not have.
	withScopeFlags(t, false, false, false)
	prev := checkForChannel
	checkForChannel = func(_ context.Context, _ string) (selfupdate.CheckResult, error) {
		return selfupdate.CheckResult{}, errors.New("github unreachable")
	}
	t.Cleanup(func() { checkForChannel = prev })

	cmd := &cobra.Command{}
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Hints: "always"}))
	var buf bytes.Buffer
	cmd.SetOut(&buf)

	if err := runUpdateDryRun(cmd, config.State{Channel: "dev"}); err == nil {
		t.Fatal("expected the failed check to surface")
	}
}
