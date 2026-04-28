package cmd

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/mattn/go-isatty"
	"github.com/spf13/cobra"
	"golang.org/x/term"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

// walkBatchSize is the number of versions shown per bubbletea program in the
// stable-channel per-release walk. Picked so a typical 80x24 terminal can
// fit one block plus the key footer without forcing scroll.
const walkBatchSize = 3

// Function variables for the GitHub release/compare API calls. Tests swap
// these via t.Cleanup to drive runStableHighlightsWalk and runDevCommitWalk
// down their error and empty-range branches without spinning up a fake
// GitHub server.
var (
	releasesBetween = selfupdate.ReleasesBetween
	commitsBetween  = selfupdate.CommitsBetween
)

// currentBuildCommit returns the commit SHA stamped into the running
// binary by GoReleaser at build time. Wrapped in a function variable so
// tests can stub it without touching the version package globals.
var currentBuildCommit = func() string { return version.Commit }

// runChangelogWalk renders the per-release Highlights walk (stable channel)
// or the combined commit-list view (dev channel) before the install confirm
// prompt in updateCLI. The walk is informational and never blocks the
// update; any failure falls back to a terse "Update available" notice.
//
// Gating: walk is skipped entirely in non-interactive contexts (--yes,
// --quiet, --json, non-TTY stdin or stdout). The single-line fallback is
// printed in those cases so the user still sees the version jump.
func runChangelogWalk(ctx context.Context, cmd *cobra.Command, result selfupdate.CheckResult, state config.State) {
	if !shouldShowWalk(cmd) {
		printOfflineNotice(cmd, result)
		return
	}

	if state.Channel == "dev" {
		runDevCommitWalk(ctx, cmd, result)
		return
	}

	runStableHighlightsWalk(ctx, cmd, result, state)
}

// runStableHighlightsWalk fetches every release in (installed, target] and
// renders them oldest-to-newest in batches of walkBatchSize using the
// per-release Highlights walk Model.
func runStableHighlightsWalk(ctx context.Context, cmd *cobra.Command, result selfupdate.CheckResult, state config.State) {
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// Normalise to vX.Y.Z[-dev.N] so release/compare API failures and the
	// user-facing range labels stay consistent with the dev-channel path.
	// selfupdate.ReleasesBetween itself accepts both forms, but we prefer
	// passing the canonical shape so any future stricter validation does
	// not silently regress.
	base := normalizeVersionRef(result.CurrentVersion)
	head := normalizeVersionRef(result.LatestVersion)
	releases, err := releasesBetween(ctx, base, head, false)
	if err != nil {
		out.Warn(fmt.Sprintf("Could not load release list (%s..%s): %v", base, head, err))
		out.HintError("Showing terse update notice instead. Re-run later or check release notes manually.")
		printOfflineNotice(cmd, result)
		return
	}
	if len(releases) == 0 {
		out.Warn(fmt.Sprintf(
			"No releases found strictly between %s and %s -- the walk has nothing to show.",
			base, head))
		out.HintError("This is unusual on the stable channel; check the GitHub releases page if a release was pruned.")
		printOfflineNotice(cmd, result)
		return
	}

	printWalkSummary(out, releases, result)
	if !confirmStartWalk(opts) {
		return
	}

	width, height := terminalSize(cmd)
	view := state.ChangelogViewOrDefault()
	batches := batchReleases(releases, walkBatchSize)
	for batchIdx, batch := range batches {
		isFinal := batchIdx == len(batches)-1
		batchResult, err := ui.RunWalkBatch(ctx, ui.WalkBatchInput{
			Versions:     batch,
			InitialView:  view,
			IsFinalBatch: isFinal,
			Width:        width,
			Height:       height,
			Options:      opts.UIOptions(),
			Output:       cmd.OutOrStdout(),
		})
		if err != nil {
			out.Warn(fmt.Sprintf("walk batch %d: %v", batchIdx, err))
			return
		}
		if batchResult.Outcome == ui.WalkOutcomeQuit {
			return
		}
		view = batchResult.FinalView
		if !isFinal {
			out.Blank()
		}
	}
}

// runDevCommitWalk fetches all commits between the installed and target dev
// (or stable) tag via the GitHub compare API and renders them in a single
// scrollable bubbletea program. Dev pre-releases have no Highlights blocks,
// so a per-release walk is uninformative -- a flat commit list is what the
// user actually wants to see.
//
// The GitHub compare endpoint accepts tags or commit SHAs on either side,
// and we deliberately prefer the embedded build SHA over the installed tag
// for the base ref: dev pre-release tags are auto-pruned from the remote
// shortly after newer dev tags publish, so a tag-based base routinely 404s
// once a few rollovers have happened. The embedded SHA, by contrast, is
// permanent. We still normalise the version strings for the user-facing
// labels (which may lack the leading "v" -- the CLI's own version.Version
// is set without it) and for the head ref, which is the freshly-published
// latest and unlikely to be pruned in the seconds between check and walk.
//
// When compare fails we ALWAYS surface the failure with a warning
// explaining why the rich walk did not render -- silent fallbacks have
// repeatedly bitten users who could not tell whether the changelog was
// missing because of an empty range or a real error.
func runDevCommitWalk(ctx context.Context, cmd *cobra.Command, result selfupdate.CheckResult) {
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	base := normalizeVersionRef(result.CurrentVersion)
	head := normalizeVersionRef(result.LatestVersion)
	apiBase := effectiveBaseRef(base, currentBuildCommit())
	commitRange, err := commitsBetween(ctx, apiBase, head)
	if err != nil {
		// commitsBetween wraps its underlying error as
		// "comparing <apiBase>...<head>: <inner>" -- when apiBase is the
		// embedded build SHA that wrapper would leak the SHA into the
		// warn line. Substitute it back to the user-facing version
		// label before formatting. The inner cause (rate-limit, 404,
		// etc.) is preserved because it is genuinely useful for
		// self-diagnosis and contains no secret data.
		errMsg := scrubAPIBase(err.Error(), apiBase, base)
		out.Warn(fmt.Sprintf("Could not fetch commit list for %s..%s: %s", base, head, errMsg))
		out.HintError(devCommitWalkErrorHint(apiBase != base))
		printOfflineNotice(cmd, result)
		return
	}
	if len(commitRange.Commits) == 0 {
		out.Warn(fmt.Sprintf(
			"GitHub returned 0 commits between %s and %s -- range looks empty.", base, head))
		printOfflineNotice(cmd, result)
		return
	}
	width, height := terminalSize(cmd)
	if _, err := ui.RunCommitWalk(ctx, ui.CommitWalkInput{
		Installed: base,
		Target:    head,
		Commits:   commitRange,
		Width:     width,
		Height:    height,
		Options:   opts.UIOptions(),
		Output:    cmd.OutOrStdout(),
	}); err != nil {
		out.Warn(fmt.Sprintf("commit walk failed: %v", err))
		printOfflineNotice(cmd, result)
	}
}

// normalizeVersionRef ensures a version string carries the leading "v"
// expected by GitHub release tags. The CLI's own `version.Version` is set
// without the "v" by GoReleaser ldflags, so callers that pass it straight
// to the GitHub compare/refs API would otherwise hit 404. Empty input is
// returned unchanged.
func normalizeVersionRef(v string) string {
	if v == "" || strings.HasPrefix(v, "v") {
		return v
	}
	return "v" + v
}

// effectiveBaseRef chooses which ref to pass to the GitHub compare API for
// the "installed" side of the dev-channel commit walk. Prefers the embedded
// build commit SHA (permanent on the remote) over the installed tag (which
// is auto-pruned from the remote once a few newer dev releases roll over).
// Falls back to the tag for builds without a stamped commit, e.g. local
// `go build` or `go run` where Commit is "none" / "dev".
func effectiveBaseRef(tagRef, commitSHA string) string {
	if isLikelyCommitSHA(commitSHA) {
		return commitSHA
	}
	return tagRef
}

// commitSHALen is the canonical length of a git commit SHA. GoReleaser
// stamps the full 40-char SHA into version.Commit; sentinel values
// ("none", "dev", "unknown", "") are shorter and fail the length check,
// which is the trigger for falling back to the tag-based ref.
const commitSHALen = 40

// isLikelyCommitSHA reports whether s has the canonical shape of a git
// commit SHA: exactly commitSHALen hex chars. Restricting to the full
// length closes a hex-named-tag bypass (a tag like "abcdef1" would
// otherwise match a >= 7-char prefix test and the dev commit walk would
// search for the wrong commit). The name avoids "stable" because that
// term is already used in this file for the release channel (see
// runStableHighlightsWalk).
func isLikelyCommitSHA(s string) bool {
	if len(s) != commitSHALen {
		return false
	}
	for _, r := range s {
		switch {
		case r >= '0' && r <= '9':
		case r >= 'a' && r <= 'f':
		case r >= 'A' && r <= 'F':
		default:
			return false
		}
	}
	return true
}

// scrubAPIBase replaces every occurrence of apiBase in errMsg with the
// user-facing tagRef. Used to substitute the embedded build SHA back to
// its version label in error strings sourced from the GitHub compare
// path -- both the wrapper from commitsBetween and any inner error that
// happens to include the request URL embed apiBase verbatim. No-op when
// apiBase == tagRef (we never used the SHA on this call).
func scrubAPIBase(errMsg, apiBase, tagRef string) string {
	if apiBase == tagRef {
		return errMsg
	}
	return strings.ReplaceAll(errMsg, apiBase, tagRef)
}

// devCommitWalkErrorHint returns the HintError body shown when the dev
// commit-walk list-commits API call fails. The wording differs based on
// whether we already used the embedded commit SHA: with a SHA the
// tag-pruning explanation no longer fits, so we leave the inner error
// (which now carries the real cause -- rate limit, 4xx, or the explicit
// "response exceeded N-byte cap" guard from fetchJSON) to speak for
// itself instead of guessing at "transient network or rate limit".
func devCommitWalkErrorHint(usedCommitSHA bool) string {
	if usedCommitSHA {
		return "Showing terse update notice instead."
	}
	return "This usually means the installed dev pre-release tag was pruned from GitHub " +
		"(dev releases are auto-rolled), or this is a local build without an embedded " +
		"commit SHA. Showing terse update notice instead."
}

// shouldShowWalk reports whether the walk UI should run for this invocation.
// Bubbletea requires both stdin and stdout to be a TTY; --yes / --quiet /
// --json all suppress the walk.
func shouldShowWalk(cmd *cobra.Command) bool {
	opts := GetGlobalOpts(cmd.Context())
	if opts.Quiet || opts.JSON || opts.Yes {
		return false
	}
	if !opts.ShouldPrompt() {
		return false
	}
	return writerIsTTY(cmd.OutOrStdout())
}

// writerIsTTY reports whether w is a terminal file descriptor.
func writerIsTTY(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	return isatty.IsTerminal(f.Fd()) || isatty.IsCygwinTerminal(f.Fd())
}

// terminalSize returns the (width, height) of the terminal attached to
// cmd's output. Falls back to (80, 24) when the size cannot be determined.
func terminalSize(cmd *cobra.Command) (int, int) {
	if f, ok := cmd.OutOrStdout().(*os.File); ok {
		if w, h, err := term.GetSize(int(f.Fd())); err == nil && w > 0 && h > 0 {
			return w, h
		}
	}
	return 80, 24
}

// printWalkSummary prints a one-line summary above the walk so the user
// sees what they are about to walk through. The bubbletea Model handles
// the per-version layout; this is plain UI output.
func printWalkSummary(out *ui.UI, releases []selfupdate.Release, result selfupdate.CheckResult) {
	out.Section(fmt.Sprintf("Walking %d release%s: %s -> %s",
		len(releases), pluralS(len(releases)),
		normalizeVersionRef(result.CurrentVersion),
		normalizeVersionRef(result.LatestVersion),
	))
	for _, r := range releases {
		_, hasHighlights := selfupdate.ExtractHighlights(r.Body)
		marker := "commit-based"
		if hasHighlights {
			marker = "Highlights"
		}
		out.KeyValue(r.TagName, fmt.Sprintf("%s   %s", formatPublishedDate(r.PublishedAt), marker))
	}
	out.Blank()
}

// confirmStartWalk asks the user (interactively) to start the walk. Returns
// false to abort; non-interactive paths (Yes, no TTY) never reach this code.
func confirmStartWalk(opts *GlobalOpts) bool {
	// In non-prompting modes (Yes, no TTY) shouldShowWalk would have already
	// returned false before reaching here. Belt-and-braces: skip the prompt
	// if for some reason we got here without a TTY.
	if !opts.ShouldPrompt() {
		return false
	}
	// We could prompt here, but the issue's UX explicitly opens the walk
	// directly after the summary table. Returning true preserves that flow.
	// Users can still abort via `q` once inside the bubbletea program.
	return true
}

// pluralS returns "s" when n != 1, "" otherwise.
func pluralS(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// formatPublishedDate parses a GitHub `published_at` ISO 8601 string and
// returns "YYYY-MM-DD". Falls back to the raw input when unparseable.
func formatPublishedDate(raw string) string {
	if raw == "" {
		return ""
	}
	if t, err := time.Parse(time.RFC3339, raw); err == nil {
		return t.UTC().Format("2006-01-02")
	}
	return raw
}

// batchReleases splits a slice of releases into chunks of at most size
// elements. The final batch may be smaller. Returns nil for an empty input.
func batchReleases(releases []selfupdate.Release, size int) [][]selfupdate.Release {
	if size <= 0 {
		return nil
	}
	if len(releases) == 0 {
		return nil
	}
	batches := make([][]selfupdate.Release, 0, (len(releases)+size-1)/size)
	for i := 0; i < len(releases); i += size {
		end := min(i+size, len(releases))
		batches = append(batches, releases[i:end])
	}
	return batches
}

// printOfflineNotice prints the terse "Update available" line + a release
// notes URL hint for non-interactive contexts and offline / rate-limited
// fallbacks. Existing call sites (the original updateCLI Step output) are
// replaced by this so we never print the version-jump twice.
//
// Both versions are normalised to the canonical "vX.Y.Z[-dev.N]" form so
// the line never reads "v0.7.3-dev.19 (current: 0.7.3-dev.11)" -- the
// installed version is stamped without the "v" prefix at build time, but
// the user-facing notice should match the GitHub release tag style.
func printOfflineNotice(cmd *cobra.Command, result selfupdate.CheckResult) {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	current := normalizeVersionRef(result.CurrentVersion)
	latest := normalizeVersionRef(result.LatestVersion)
	out.Step(fmt.Sprintf("New version available: %s (current: %s)", latest, current))
	out.HintNextStep(fmt.Sprintf("Release notes: %s/releases/tag/%s",
		version.RepoURL, latest))
}
