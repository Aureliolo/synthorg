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

	// Pinned per key. A whole-buffer substring search cannot tell the two
	// labels apart, so a build that swapped which line carries which verdict
	// would pass every assertion in a file written to catch exactly that.
	requireLineValue(t, got, "CLI update", "no (already current)")
	requireLineValue(t, got, "Image update", "no (already current)")
	requireContains(t, got, "this installation is current")
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

	requireLineValue(t, got, "CLI update", "yes")
	requireLineValue(t, got, "Image update", "yes")
	requireContains(t, got, "Remove --dry-run to execute")
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

	requireLineValue(t, got, "CLI update", "no (already current)")
	requireLineValue(t, got, "Image update", "yes")
	requireContains(t, got, "Remove --dry-run to execute")
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

	requireLineValue(t, got, "CLI update", "no (excluded by flags)")
	requireLineValue(t, got, "Image update", "yes")
}

func TestRunUpdateDryRun_anOutOfScopeHalfDoesNotPromptAnUpdate(t *testing.T) {
	// The staged case an ordinary operator reaches: they ran --cli-only once,
	// so the CLI is current and the images are not, and they run --cli-only
	// again. Both in-scope verdicts read "no", and judging the summary on the
	// RAW availability still told them to remove --dry-run and run an update
	// that would do nothing.
	withScopeFlags(t, true, false, false)
	withVersion(t, "0.9.4-dev.168")

	got := dryRunOutput(t,
		config.State{Channel: "dev", ImageTag: "0.9.3"},
		selfupdate.CheckResult{
			UpdateAvail:    false,
			CurrentVersion: "0.9.4-dev.168",
			LatestVersion:  "v0.9.4-dev.168",
		},
	)

	requireLineValue(t, got, "CLI update", "no (already current)")
	requireLineValue(t, got, "Image update", "no (excluded by flags)")
	requireContains(t, got, "this installation is current")
	if strings.Contains(got, "Remove --dry-run to execute") {
		t.Errorf("nothing in scope will change, so nothing is worth running\n--- got ---\n%s", got)
	}
}

func TestRunUpdateDryRun_anUnorderableImageTagDoesNotAbortThePreview(t *testing.T) {
	// `latest` is what ImageTagForVersion falls back to and what an operator
	// on a private registry may set, and it is not a version. Ordering it
	// against a release errors, and refusing the read-only preview over a
	// value the real update handles by equality makes the preview stricter
	// than the command it previews.
	withScopeFlags(t, false, false, false)
	withVersion(t, "0.9.3")

	got := dryRunOutput(t,
		config.State{Channel: "stable", ImageTag: "latest"},
		selfupdate.CheckResult{
			UpdateAvail:    true,
			CurrentVersion: "0.9.3",
			LatestVersion:  "v0.9.4",
		},
	)

	requireLineValue(t, got, "Image update", "yes")
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

	requireLineValue(t, got, "Image update", "no (excluded by flags)")
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

// requireLineValue asserts the rendered value of one key-value line.
//
// “ui.KeyValue“ always renders the key followed by exactly one colon, so the
// line splits on that rather than being unwrapped prefix by prefix. Comparing
// the whole key also stops one key matching another's prefix.
func requireLineValue(t *testing.T, out, key, want string) {
	t.Helper()
	for line := range strings.SplitSeq(out, "\n") {
		lineKey, value, found := strings.Cut(strings.TrimSpace(line), ":")
		if !found || strings.TrimSpace(lineKey) != key {
			continue
		}
		if got := strings.TrimSpace(value); got != want {
			t.Errorf("%s = %q, want %q\n--- got ---\n%s", key, got, want, out)
		}
		return
	}
	t.Errorf("no %q line in output\n--- got ---\n%s", key, out)
}

func TestUpdateVerdict(t *testing.T) {
	cases := []struct {
		name      string
		inScope   bool
		available bool
		want      string
	}{
		{"out of scope reads as the operator's own flag", false, true, "no (excluded by flags)"},
		{"in scope and current reads as the installation", true, false, "no (already current)"},
		{"in scope and behind is the only yes", true, true, "yes"},
		{"out of scope wins over currency", false, false, "no (excluded by flags)"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := updateVerdict(tc.inScope, tc.available); got != tc.want {
				t.Errorf("updateVerdict(%v, %v) = %q, want %q",
					tc.inScope, tc.available, got, tc.want)
			}
		})
	}
}

func TestRestartVerdict(t *testing.T) {
	cases := []struct {
		name        string
		pullsImages bool
		noRestart   bool
		want        string
	}{
		{"nothing to pull is the installation's state", false, false, "no (nothing to pull)"},
		{"nothing to pull outranks the flag", false, true, "no (nothing to pull)"},
		{"a pull the operator excluded names the flag", true, true, "no (excluded by flags)"},
		{"a pull with no exclusion restarts", true, false, "yes"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := restartVerdict(tc.pullsImages, tc.noRestart); got != tc.want {
				t.Errorf("restartVerdict(%v, %v) = %q, want %q",
					tc.pullsImages, tc.noRestart, got, tc.want)
			}
		})
	}
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
