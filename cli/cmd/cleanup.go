package cmd

import (
	"context"
	"fmt"
	"strings"

	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/images"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

var (
	cleanupDryRun bool
	cleanupAll    bool
	cleanupKeep   int
)

var cleanupCmd = &cobra.Command{
	Use:   "cleanup",
	Short: "Remove old container images to free disk space",
	Long: `Remove old SynthOrg container images that are no longer needed.

After updates, previous image versions remain on disk. This command
identifies images that don't match the current version and offers to
remove them individually.`,
	Example: `  synthorg cleanup              # interactive cleanup of old images
  synthorg cleanup --dry-run    # list images without removing
  synthorg cleanup --all --yes  # remove ALL SynthOrg images non-interactively
  synthorg cleanup --keep 2     # keep 2 most recent previous versions`,
	RunE: runCleanup,
}

func init() {
	cleanupCmd.Flags().BoolVar(&cleanupDryRun, "dry-run", false, "list images without removing")
	cleanupCmd.Flags().BoolVar(&cleanupAll, "all", false, "include ALL SynthOrg images, not just old ones")
	cleanupCmd.Flags().IntVar(&cleanupKeep, "keep", 0, "keep N most recent previous versions (0=remove all)")
	cleanupCmd.GroupID = "lifecycle"
	rootCmd.AddCommand(cleanupCmd)
}

func validateCleanupFlags() error {
	if cleanupKeep < 0 {
		return fmt.Errorf("invalid --keep %d: must be >= 0", cleanupKeep)
	}
	return nil
}

func runCleanup(cmd *cobra.Command, _ []string) error {
	if err := validateCleanupFlags(); err != nil {
		return fmt.Errorf("validating cleanup flags: %w", err)
	}
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	info, err := docker.Detect(ctx)
	if err != nil {
		return fmt.Errorf("detecting docker: %w", err)
	}
	old, err := collectCleanupCandidates(ctx, cmd, info, state, out, errOut)
	if err != nil {
		return err
	}
	if old == nil {
		// nothing to clean (collectCleanupCandidates emitted its own hint)
		hintAutoCleanupIfDisabled(out, state, false)
		return nil
	}
	displayOldImages(out, old)
	if cleanupAll {
		out.HintNextStep("--all includes current images. Running containers will prevent removal.")
	}
	if cleanupDryRun {
		out.HintNextStep(fmt.Sprintf("Dry run: %d image(s) would be removed", len(old)))
		return nil
	}
	removedAny, err := confirmAndCleanup(ctx, cmd, info, out, old)
	if err != nil {
		return fmt.Errorf("confirming cleanup: %w", err)
	}
	hintAutoCleanupIfDisabled(out, state, removedAny)
	return nil
}

// collectCleanupCandidates returns the candidate list with --keep
// applied. Returns nil (no error) when the call would be a no-op so
// the caller can short-circuit; nil also covers the "fewer than --keep
// images exist" early-return shape.
func collectCleanupCandidates(ctx context.Context, cmd *cobra.Command, info docker.Info, state config.State, out, errOut *ui.UI) ([]oldImage, error) {
	var old []oldImage
	var err error
	if cleanupAll {
		// --all: include ALL SynthOrg images (same as uninstall).
		old, err = listNonCurrentImages(ctx, errOut.Writer(), info, nil)
	} else {
		old, err = findOldImages(ctx, cmd.ErrOrStderr(), info, state)
	}
	if err != nil {
		return nil, fmt.Errorf("finding images: %w", err)
	}
	if len(old) == 0 {
		out.Success("No images found -- nothing to clean up")
		return nil, nil
	}
	// --keep: preserve N most recent (remove from the end of the list,
	// Docker returns images in most-recent-first order).
	if cleanupKeep > 0 && len(old) > cleanupKeep {
		return old[cleanupKeep:], nil
	}
	if cleanupKeep > 0 {
		out.Success(fmt.Sprintf("Only %d image(s) found, keeping all (--keep %d)", len(old), cleanupKeep))
		return nil, nil
	}
	return old, nil
}

// hintAutoCleanupIfDisabled emits the auto_cleanup hint when at least
// one image was removed and the user has not enabled auto-cleanup. When
// removedAny is false but state.AutoCleanup is also false this still
// emits the hint (from the empty-candidates branch).
func hintAutoCleanupIfDisabled(out *ui.UI, state config.State, removedAny bool) {
	if state.AutoCleanup {
		return
	}
	if !removedAny {
		out.HintTip("Run 'synthorg config set auto_cleanup true' to clean up automatically after updates.")
		return
	}
	out.Blank()
	out.HintTip("Tip: run 'synthorg config set auto_cleanup true' to clean up old images automatically after updates.")
}

// displayOldImages renders the image list with total size.
func displayOldImages(out *ui.UI, old []oldImage) {
	var totalB float64
	lines := make([]string, 0, len(old))
	for _, img := range old {
		lines = append(lines, img.display)
		totalB += img.sizeB
	}
	out.Box("Old Images", lines)
	out.Blank()

	if totalB > 0 {
		out.KeyValue("Total", formatBytes(totalB))
		out.Blank()
	}
}

// confirmAndCleanup prompts the user and removes approved images.
// Returns (true, nil) when at least one image was removed.
func confirmAndCleanup(ctx context.Context, cmd *cobra.Command, info docker.Info, out *ui.UI, old []oldImage) (bool, error) {
	opts := GetGlobalOpts(ctx)
	if !opts.ShouldPrompt() && !opts.Yes {
		out.HintNextStep("Non-interactive mode: run interactively or use --yes to remove, or use 'docker rmi <id>'.")
		return false, nil
	}
	confirmed, err := confirmCleanupPrompt(cmd, opts, old)
	if err != nil {
		return false, err
	}
	if !confirmed {
		return false, nil
	}
	removed, freedB, hardFailures, ctxErr := removeOldImages(ctx, info, out, old)
	emitCleanupSummary(out, old, removed, freedB)
	if ctxErr != nil {
		return removed > 0, ctxErr
	}
	if hardFailures > 0 {
		return removed > 0, fmt.Errorf("%d image removal(s) failed", hardFailures)
	}
	return removed > 0, nil
}

// confirmCleanupPrompt asks the operator whether to proceed. --yes
// auto-confirms; otherwise the huh form prompts interactively.
func confirmCleanupPrompt(cmd *cobra.Command, opts *GlobalOpts, old []oldImage) (bool, error) {
	if opts.Yes {
		return true, nil
	}
	var remove bool
	form := huh.NewForm(huh.NewGroup(
		huh.NewConfirm().
			Title(fmt.Sprintf("Remove %d old image(s)?", len(old))).
			Value(&remove),
	))
	if err := form.WithInput(cmd.InOrStdin()).WithOutput(cmd.OutOrStdout()).Run(); err != nil {
		return false, err
	}
	return remove, nil
}

// removeOldImages iterates `docker rmi` one image at a time without
// --force (gentle cleanup: only untagged/unused images come off; tagged
// images need 'synthorg uninstall'). Returns the count removed, the
// total bytes freed, the number of hard `docker rmi` failures (non
// "in use" errors, which should surface as a runtime-failure exit code),
// and ctx.Err() if the loop was interrupted by cancellation. The caller
// surfaces the summary first, then propagates whichever signal is set.
func removeOldImages(ctx context.Context, info docker.Info, out *ui.UI, old []oldImage) (int, float64, int, error) {
	var freedB float64
	var removed, hardFailures int
	for _, img := range old {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return removed, freedB, hardFailures, ctxErr
		}
		_, rmiErr := docker.RunCmd(ctx, info.DockerPath, "rmi", img.id)
		if rmiErr != nil {
			reclaimed, hardFailure := reclaimBlockedImage(ctx, info, out, img, rmiErr)
			if hardFailure {
				hardFailures++
			}
			if !reclaimed {
				continue
			}
		} else {
			out.Success(fmt.Sprintf("%-12s removed", img.id))
		}
		removed++
		freedB += img.sizeB
	}
	return removed, freedB, hardFailures, nil
}

// emitCleanupSummary prints the post-cleanup totals + hints.
func emitCleanupSummary(out *ui.UI, old []oldImage, removed int, freedB float64) {
	out.Blank()
	if removed > 0 && freedB > 0 {
		out.Success(fmt.Sprintf("Freed %s (%d image(s) removed)", formatBytes(freedB), removed))
	} else if removed > 0 {
		out.Success(fmt.Sprintf("Removed %d image(s)", removed))
	}
	if skipped := len(old) - removed; skipped > 0 {
		// Deliberately not "stop containers first": that is the remedy for
		// one of the blocks, and telling an operator to hunt for a container
		// holding an image that merely carries a second tag is what sent
		// this hint's predecessor looking for something that did not exist.
		out.HintError(fmt.Sprintf("%d image(s) skipped; each line above names why", skipped))
	}
	if removed > 0 {
		out.HintNextStep("Use --keep N to preserve N recent previous versions.")
	}
}

// reclaimBlockedImage handles a `docker rmi <id>` the daemon declined.
// Reports whether the image came off anyway, and whether the failure was a
// hard one that must surface as a runtime-failure exit code.
//
// A multiply-referenced image is not held by anything: it is merely
// ambiguous by id. The operator asked for these to go, so each reference is
// removed rather than reporting a blocker they would have to clear by hand.
func reclaimBlockedImage(
	ctx context.Context,
	info docker.Info,
	out *ui.UI,
	img oldImage,
	rmiErr error,
) (reclaimed bool, hardFailure bool) {
	block := classifyImageRemoval(rmiErr)
	switch block {
	case rmiMultipleReferences:
		ours, foreign := imageReferences(ctx, info, img.id)
		// A reference outside our repository prefix belongs to whoever
		// created it. Removing the rest would not free the image anyway,
		// and removing theirs is not this command's to do, so report and
		// leave it: the operator can drop their own tag if they want the
		// space back.
		if len(foreign) > 0 {
			out.Warn(fmt.Sprintf(
				"%-12s skipped (also tagged outside this project: %s)",
				img.id, strings.Join(foreign, ", "),
			))
			return false, false
		}
		removed := removeByReferences(ctx, info, ours)
		if len(removed) == len(ours) {
			out.Success(fmt.Sprintf("%-12s removed (%d references)", img.id, len(ours)))
			return true, false
		}
		if len(removed) > 0 {
			// Said plainly rather than reported as a skip: those
			// references are gone whatever happens next, and an operator
			// told "skipped" would go looking for tags that no longer
			// exist. The image itself survives, so nothing was freed.
			out.Warn(fmt.Sprintf(
				"%-12s partially removed (%s came off; %s remains)",
				img.id, strings.Join(removed, ", "), blockReason(block, ours[len(removed):]),
			))
			return false, false
		}
		out.Warn(fmt.Sprintf("%-12s skipped (%s)", img.id, blockReason(block, ours)))
		return false, false
	case rmiNotBlocked:
		out.Error(fmt.Sprintf("%-12s failed: %v", img.id, rmiErr))
		return false, true
	default:
		out.Warn(fmt.Sprintf("%-12s skipped (%s)", img.id, blockReason(block, nil)))
		return false, false
	}
}

// removeByReferences removes an image by each of its references in turn,
// reporting which ones actually came off.
//
// The removed list is what the caller reports, not a bare success flag: a
// reference that came off stays off, so calling a partial removal "skipped"
// would tell an operator nothing happened while a tag they had is already
// gone. Only a complete removal frees the layers, so only that counts
// towards the space reclaimed.
func removeByReferences(ctx context.Context, info docker.Info, refs []string) []string {
	var removed []string
	for _, ref := range refs {
		if _, err := docker.RunCmd(ctx, info.DockerPath, "rmi", ref); err != nil {
			return removed
		}
		removed = append(removed, ref)
	}
	return removed
}

// imageRemovalBlock is why `docker rmi` declined to remove an image.
//
// Docker opens all of these with "conflict", so matching that word alone
// cannot tell them apart, and they do not share a remedy: one needs a
// container stopped, another needs a tag removed, and a third needs the
// images built on top of it removed first. Reporting them as one thing
// sends an operator hunting for a container that does not exist.
type imageRemovalBlock int

const (
	rmiNotBlocked imageRemovalBlock = iota
	// rmiHeldByContainer: a container, running or stopped, still
	// references the image.
	rmiHeldByContainer
	// rmiMultipleReferences: the image carries more than one tag or
	// digest reference, so removing it by id is ambiguous.
	rmiMultipleReferences
	// rmiDependentChildren: another image was built on top of this one.
	rmiDependentChildren
	// rmiBlockedOther: a conflict Docker words in some way not matched
	// above. Still benign (not a runtime failure), but unclassified.
	rmiBlockedOther
)

// classifyImageRemoval maps a `docker rmi` error to the reason it declined.
// A nil error, or one that is not a conflict at all (permissions, daemon
// unreachable), returns rmiNotBlocked so the caller treats it as a real
// failure rather than a skip.
func classifyImageRemoval(err error) imageRemovalBlock {
	if err == nil {
		return rmiNotBlocked
	}
	msg := err.Error()
	switch {
	case strings.Contains(msg, "image is being used"):
		return rmiHeldByContainer
	case strings.Contains(msg, "image is referenced"):
		return rmiMultipleReferences
	case strings.Contains(msg, "dependent child images"):
		return rmiDependentChildren
	case strings.Contains(msg, "conflict"):
		return rmiBlockedOther
	default:
		return rmiNotBlocked
	}
}

// blockReason renders the operator-facing reason, naming the remedy that
// actually applies to this block.
func blockReason(block imageRemovalBlock, refs []string) string {
	switch block {
	case rmiHeldByContainer:
		return "in use by a container"
	case rmiMultipleReferences:
		if len(refs) > 0 {
			return fmt.Sprintf("tagged %s", strings.Join(refs, ", "))
		}
		return "carries more than one reference"
	case rmiDependentChildren:
		return "another image is built on it"
	default:
		return "blocked by the daemon"
	}
}

// imageReferences lists the tag and digest references pointing at an image,
// split into ours and everybody else's.
//
// Removing an image that carries several means removing each reference, so
// dropping only the tag would leave the digest reference and the layers
// behind: exactly the "freed nothing" outcome this reports on. The split
// matters because an operator can tag any image themselves, including one of
// ours (`docker tag ghcr.io/.../synthorg-backend:0.4.5 my-backup:keep`), and
// that tag is theirs: the candidate list was scoped to our repository prefix,
// but expanding an id back to its references escapes that scope.
func imageReferences(ctx context.Context, info docker.Info, id string) (ours, foreign []string) {
	const format = "{{range .RepoTags}}{{println .}}{{end}}{{range .RepoDigests}}{{println .}}{{end}}"
	out, err := docker.RunCmd(ctx, info.DockerPath, "image", "inspect", id, "--format", format)
	if err != nil {
		return nil, nil
	}
	prefix := images.RepoPrefix()
	for line := range strings.SplitSeq(out, "\n") {
		ref := strings.TrimSpace(line)
		switch {
		case ref == "":
		case strings.HasPrefix(ref, prefix):
			ours = append(ours, ref)
		default:
			foreign = append(foreign, ref)
		}
	}
	return ours, foreign
}
