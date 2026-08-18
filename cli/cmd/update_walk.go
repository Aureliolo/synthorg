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
// autoAccept is the auto_update_cli verdict updateCLI already resolved, and
// decides whether the walk waits for keys -- not whether it renders.
func runChangelogWalk(
	ctx context.Context,
	cmd *cobra.Command,
	result selfupdate.CheckResult,
	state config.State,
	autoAccept bool,
) {
	mode := resolveWalkMode(cmd, autoAccept)
	if mode == walkModeSuppressed {
		return
	}
	out := changelogUI(cmd, mode)

	if state.Channel == "dev" {
		runDevCommitWalk(ctx, cmd, out, result, mode)
		return
	}

	runStableHighlightsWalk(ctx, cmd, out, result, state, mode)
}

// changelogUI builds the UI the whole changelog presentation writes through.
//
// Static mode outranks --quiet, and it has to do so for every part of the
// presentation rather than just the body: the summary index carries the
// publish dates and the Highlights markers that appear nowhere else, and the
// fallback notice is the only output at all when the fetch fails. Printing
// the body raw while letting the flag eat those leaves a record that reads
// complete and is not. --json still suppresses everything, and never reaches
// static mode.
func changelogUI(cmd *cobra.Command, mode walkMode) *ui.UI {
	opts := GetGlobalOpts(cmd.Context()).UIOptions()
	if mode == walkModeStatic {
		opts.Quiet = false
	}
	return ui.NewUIWithOptions(cmd.OutOrStdout(), opts)
}

// runStableHighlightsWalk fetches every release in (installed, target] and
// renders them oldest-to-newest: in batches of walkBatchSize through the
// per-release Highlights walk Model when interactive, or as one printed
// block when static.
func runStableHighlightsWalk(
	ctx context.Context,
	cmd *cobra.Command,
	out *ui.UI,
	result selfupdate.CheckResult,
	state config.State,
	mode walkMode,
) {
	opts := GetGlobalOpts(ctx)

	// Normalise to vX.Y.Z[-dev.N] so release/compare API failures and the
	// user-facing range labels stay consistent with the dev-channel path.
	// selfupdate.ReleasesBetween itself accepts both forms, but we prefer
	// passing the canonical shape so any future stricter validation does
	// not silently regress.
	base := normalizeVersionRef(result.CurrentVersion)
	head := normalizeVersionRef(result.LatestVersion)
	// Display-only copies. The raw values keep going to the API, which needs
	// the ref as published; the labels below are read by an operator, and
	// UI.Warn strips control bytes but leaves the bidi and zero-width runes
	// a remote tag can carry to spoof the range being offered.
	baseLabel := versionLabel(result.CurrentVersion)
	headLabel := versionLabel(result.LatestVersion)
	releases, err := releasesBetween(ctx, base, head, false)
	if err != nil {
		out.Warn(fmt.Sprintf("Could not load release list (%s..%s): %v", baseLabel, headLabel, err))
		out.HintError("Showing terse update notice instead. Re-run later or check release notes manually.")
		printOfflineNotice(out, result)
		return
	}
	if len(releases) == 0 {
		out.Warn(fmt.Sprintf(
			"No releases found strictly between %s and %s -- the walk has nothing to show.",
			baseLabel, headLabel))
		out.HintError("This is unusual on the stable channel; check the GitHub releases page if a release was pruned.")
		printOfflineNotice(out, result)
		return
	}

	printWalkSummary(out, releases, result)

	width, height := terminalSize(cmd)
	view := state.ChangelogViewOrDefault()
	if mode == walkModeStatic {
		printStaticBlock(cmd, ui.RenderWalkStatic(ui.WalkBatchInput{
			Versions:    releases,
			InitialView: view,
			Width:       width,
			Options:     opts.UIOptions(),
		}))
		return
	}
	runStableWalkBatches(ctx, cmd, out, releases, view, width, height)
}

// runStableWalkBatches drives the interactive per-release walk, carrying the
// operator's `c` view toggle across batch boundaries.
func runStableWalkBatches(
	ctx context.Context,
	cmd *cobra.Command,
	out *ui.UI,
	releases []selfupdate.Release,
	view string,
	width, height int,
) {
	opts := GetGlobalOpts(ctx)
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

// printStaticBlock writes a pre-rendered changelog block verbatim.
//
// Deliberately not ui.UI.Plain: that runs stripControl, which drops ESC and
// would strip every colour out of the block. Writing raw is safe because
// both renderers ANSI-scrub the untrusted release/commit text they format.
func printStaticBlock(cmd *cobra.Command, block string) {
	_, _ = fmt.Fprintln(cmd.OutOrStdout(), block)
}

// runDevCommitWalk fetches all commits between the installed and target dev
// (or stable) tag via the GitHub compare API and renders them as a single
// combined list: a scrollable bubbletea program when interactive, one
// printed block when static. Dev pre-releases have no Highlights blocks,
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
func runDevCommitWalk(
	ctx context.Context,
	cmd *cobra.Command,
	out *ui.UI,
	result selfupdate.CheckResult,
	mode walkMode,
) {
	opts := GetGlobalOpts(ctx)

	base := normalizeVersionRef(result.CurrentVersion)
	head := normalizeVersionRef(result.LatestVersion)
	// Display-only copies; see runStableHighlightsWalk. The raw values still
	// go to the compare API below.
	baseLabel := versionLabel(result.CurrentVersion)
	headLabel := versionLabel(result.LatestVersion)
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
		//
		// The wrapper embeds the head ref verbatim, so the message is
		// itself remote-influenced and gets the same scrub as the labels.
		errMsg := ui.SanitizeUntrustedLine(scrubAPIBase(err.Error(), apiBase, base))
		out.Warn(fmt.Sprintf("Could not fetch commit list for %s..%s: %s", baseLabel, headLabel, errMsg))
		out.HintError(devCommitWalkErrorHint(apiBase != base))
		printOfflineNotice(out, result)
		return
	}
	if len(commitRange.Commits) == 0 {
		out.Warn(fmt.Sprintf(
			"GitHub returned 0 commits between %s and %s -- range looks empty.", baseLabel, headLabel))
		printOfflineNotice(out, result)
		return
	}
	width, height := terminalSize(cmd)
	in := ui.CommitWalkInput{
		Installed: base,
		Target:    head,
		Commits:   commitRange,
		Width:     width,
		Height:    height,
		Options:   opts.UIOptions(),
		Output:    cmd.OutOrStdout(),
	}
	if mode == walkModeStatic {
		printStaticBlock(cmd, ui.RenderCommitWalkStatic(in))
		return
	}
	if _, err := ui.RunCommitWalk(ctx, in); err != nil {
		out.Warn(fmt.Sprintf("commit walk failed: %v", err))
		printOfflineNotice(out, result)
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

// versionLabel is the display form of a remote version ref: normalised to the
// leading "v", then scrubbed of the control and spoofing runes git permits in
// a tag name. The two steps travel together in one helper because every
// rendered version is read by an operator deciding whether to install it, and
// a call site that takes only the first is the defect this closes.
func versionLabel(v string) string {
	return ui.SanitizeUntrustedLine(normalizeVersionRef(v))
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

// walkMode is how the changelog is presented for one invocation.
type walkMode int

const (
	// walkModeInteractive runs the bubbletea pager and waits for keys.
	walkModeInteractive walkMode = iota
	// walkModeStatic prints the whole changelog and returns immediately.
	walkModeStatic
	// walkModeSuppressed renders nothing.
	walkModeSuppressed
)

// resolveWalkMode decides how this invocation presents the changelog.
//
// Only --json suppresses it: what somebody is about to install is worth
// seeing even when nothing is going to ask them about it, so every other
// non-interactive context downgrades to the static render rather than
// losing the content. That deliberately includes --quiet, the one place
// this outranks a flag's usual meaning: an unattended install is exactly
// where the record of what landed is worth most.
//
// Interactive needs what bubbletea needs (a TTY on both stdin and stdout)
// plus an operator who has not already said yes, via --yes or
// auto_update_cli.
func resolveWalkMode(cmd *cobra.Command, autoAccept bool) walkMode {
	opts := GetGlobalOpts(cmd.Context())
	return walkModeFor(opts, autoAccept, opts.ShouldPrompt(), writerIsTTY(cmd.OutOrStdout()))
}

// walkModeFor is the decision above with both terminal probes lifted out to
// parameters. They have to leave together: ShouldPrompt stats the process's
// own stdin and writerIsTTY needs a real *os.File, so under `go test` each is
// independently false and the static verdict is unconditional. A table that
// cannot vary them proves only that some term fired, never which, and the
// interactive verdict is unreachable altogether.
func walkModeFor(opts *GlobalOpts, autoAccept, canPrompt, outIsTTY bool) walkMode {
	if opts.JSON {
		return walkModeSuppressed
	}
	if autoAccept || opts.Quiet || !canPrompt || !outIsTTY {
		return walkModeStatic
	}
	return walkModeInteractive
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

// printWalkSummary prints an index of what the changelog covers above the
// releases themselves, carrying the publish date and the Highlights marker
// that appear nowhere in the releases underneath it. Worded for both
// presentations, and written through the caller's UI, which clears the
// quiet flag for the static one so this index cannot go missing from the
// only record an unattended run leaves.
func printWalkSummary(out *ui.UI, releases []selfupdate.Release, result selfupdate.CheckResult) {
	out.Section(fmt.Sprintf("%d release%s: %s -> %s",
		len(releases), pluralS(len(releases)),
		versionLabel(result.CurrentVersion),
		versionLabel(result.LatestVersion),
	))
	for _, r := range releases {
		_, hasHighlights := selfupdate.ExtractHighlights(r.Body)
		marker := "commit-based"
		if hasHighlights {
			marker = "Highlights"
		}
		// Same remote tag the renderers scrub: this index prints it too,
		// so it needs the same treatment or the spoof just moves up a line.
		out.KeyValue(ui.SanitizeUntrustedLine(r.TagName), fmt.Sprintf("%s   %s",
			ui.SanitizeUntrustedLine(formatPublishedDate(r.PublishedAt)), marker))
	}
	out.Blank()
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
func printOfflineNotice(out *ui.UI, result selfupdate.CheckResult) {
	current := versionLabel(result.CurrentVersion)
	latest := versionLabel(result.LatestVersion)
	out.Step(fmt.Sprintf("New version available: %s (current: %s)", latest, current))
	out.HintNextStep(fmt.Sprintf("Release notes: %s/releases/tag/%s",
		version.RepoURL, latest))
}
