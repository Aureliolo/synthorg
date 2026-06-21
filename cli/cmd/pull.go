package cmd

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
	"github.com/spf13/cobra"
)

// pullAllImages pulls all enabled images in a single unified LiveBox:
// compose services (backend, web, postgres, nats) plus standalone images
// (sandbox, sidecar, fine-tune) depending on configuration. Only enabled
// services are pulled.
//
// Callers MUST pass a state whose ImageTag and VerifiedDigests reflect the
// images to be pulled. During an update, disk config still holds the old
// tag/digests until after the pull completes; reloading here would cause
// standalone image pulls to use stale refs while compose-driven pulls use
// the new refs written into compose.yml, leaving the install inconsistent.
func pullAllImages(ctx context.Context, cmd *cobra.Command, info docker.Info, safeDir string, state config.State, out *ui.UI) (config.State, error) {
	if stateHasRegistryOverrides(state) {
		warnRegistryOverridesDisableVerification(cmd)
	}
	items := buildPullItems(state)
	emitFineTuneSizeHint(state, out)
	return state, runPullBatch(ctx, info, safeDir, items, out)
}

// pullItem describes one image to pull. compose=true uses
// `docker compose pull <name>`; compose=false uses `docker pull <ref>`
// with retry/backoff.
type pullItem struct {
	name    string
	compose bool
	ref     string
}

// buildPullItems enumerates every image the start path must pull: the
// enabled compose services plus the standalone (sandbox / sidecar /
// fine-tune) images that compose does not own.
//
// When the operator has overridden any of the registry / image-tag
// tunables (registry_host, image_repo_prefix, dhi_registry,
// postgres_image_tag, nats_image_tag), state.VerifiedDigests is bound
// to the DEFAULT-registry images and would pin the standalone pulls
// to stale digests that do not exist on the override registry. Drop
// the digest in that case so the pull resolves the tag on the
// override registry instead of failing on a stale @sha256 reference.
func buildPullItems(state config.State) []pullItem {
	var items []pullItem
	for _, svc := range composeServiceNames(state) {
		items = append(items, pullItem{name: svc, compose: true})
	}
	useDigests := !stateHasRegistryOverrides(state)
	pickDigest := func(name string) string {
		if !useDigests {
			return ""
		}
		return state.VerifiedDigests[name]
	}
	if state.Sandbox {
		items = append(items, pullItem{
			name: "sandbox",
			ref:  verify.FormatImageRef("sandbox", state.ImageTag, pickDigest("sandbox")),
		})
		items = append(items, pullItem{
			name: "sidecar",
			ref:  verify.FormatImageRef("sidecar", state.ImageTag, pickDigest("sidecar")),
		})
	}
	if state.FineTuning {
		variant := state.FineTuneVariantOrDefault()
		svc := verify.FineTuneServiceName(variant)
		items = append(items, pullItem{
			name: svc,
			ref:  verify.FormatImageRef(svc, state.ImageTag, pickDigest(svc)),
		})
	}
	return items
}

// emitFineTuneSizeHint warns the user about the fine-tune image size
// BEFORE the pull box renders so they understand why their terminal is
// about to pause; a hint shown after the pull completes is too late to
// set expectations.
//
// Uses HintNextStep (not HintGuidance): in the default "auto" hints mode
// HintGuidance is suppressed entirely, so the warning that explains a
// 10-to-30-minute stall is invisible to exactly the operators who need
// it. The GPU figures distinguish download size from the larger on-disk
// footprint and give an honest duration: the GPU image bundles ~2.5 GB of
// CUDA wheels inside the torch wheel, so a first pull realistically takes
// far longer than "a few minutes".
func emitFineTuneSizeHint(state config.State, out *ui.UI) {
	if !state.FineTuning {
		return
	}
	sizeHint := "~4 GB download (~7 GB on disk); first pull commonly takes 10-30 minutes"
	if state.FineTuneVariantOrDefault() == config.FineTuneVariantCPU {
		sizeHint = "~1.7 GB; first pull usually takes a few minutes"
	}
	out.HintNextStep(fmt.Sprintf(
		"Fine-tune image is %s on typical connections.",
		sizeHint,
	))
}

// runPullBatch fans out a pull goroutine per item and renders progress
// in a single LiveBox. Each line shows live pull progress (downloaded bytes
// and layer counts where Docker emits them, plus elapsed time) instead of a
// static spinner, so a multi-minute image pull shows activity. Returns the
// joined error covering every failed pull (nil when every pull succeeds).
func runPullBatch(ctx context.Context, info docker.Info, safeDir string, items []pullItem, out *ui.UI) error {
	labels := make([]string, len(items))
	for i, item := range items {
		labels[i] = item.name
	}
	lb := out.NewLiveBoxWithProgress("Pull Images", labels)
	defer lb.Finish()

	var (
		mu      sync.Mutex
		pullErr error
		wg      sync.WaitGroup
	)
	for i, item := range items {
		wg.Add(1)
		go func(idx int, it pullItem) {
			defer wg.Done()
			if err := pullItemTracked(ctx, info, safeDir, it, idx, lb); err != nil {
				mu.Lock()
				pullErr = errors.Join(pullErr, err)
				mu.Unlock()
			}
		}(i, item)
	}
	wg.Wait()
	return pullErr
}

// pullItemTracked pulls a single item, rendering its live progress on the
// LiveBox line and marking that line success or error. Returns the wrapped
// pull error (nil on success).
func pullItemTracked(ctx context.Context, info docker.Info, safeDir string, it pullItem, idx int, lb *ui.LiveBox) error {
	// newSink mints a fresh parser per pull attempt: a retried pull re-emits
	// its layers from scratch, so reusing one parser would leave stale
	// bytes/layer counts from the failed attempt.
	newSink := func() func(string) {
		pp := &docker.PullProgress{}
		return func(line string) {
			if pp.Observe(line) {
				lb.UpdateProgress(idx, pp.Render())
			}
		}
	}
	if err := pullOneItem(ctx, info, safeDir, it, newSink); err != nil {
		lb.UpdateLine(idx, ui.IconError)
		return fmt.Errorf("pulling %s: %w", it.name, err)
	}
	lb.UpdateLine(idx, ui.IconSuccess)
	return nil
}

// pullOneItem dispatches to the right puller for the item kind: compose
// services go through docker-compose's own pull (so it picks up the
// image override from compose.yml); standalone images use the retrying
// dockerPullWithRetry. newSink mints a fresh per-attempt progress callback
// for each underlying pull invocation.
func pullOneItem(ctx context.Context, info docker.Info, safeDir string, it pullItem, newSink func() func(string)) error {
	if it.compose {
		return composeRunStreaming(ctx, info, safeDir, newSink(), "pull", it.name)
	}
	tun := GetGlobalOpts(ctx).Tunables
	return dockerPullWithRetry(ctx, info, it.ref, tun.ImagePullAttempts, tun.ImagePullRetryDelay, newSink)
}

// maxPullBackoff caps the exponential-backoff delay between image-pull
// retries.  Guards against int64 overflow when operators set
// ImagePullAttempts to a high value: “baseDelay << (attempt - 1)“
// with a 2-second base and >=62 attempts would overflow time.Duration
// (int64 nanoseconds) and yield a negative delay that time.After
// resolves immediately, effectively disabling the backoff.  Saturating
// at 5 minutes keeps the retry schedule bounded and predictable.
const maxPullBackoff = 5 * time.Minute

// dockerPullWithRetry pulls an image with retries for transient failures.
// The caller supplies attempts (> 0) and baseDelay (exponential backoff
// seed) so the values flow from the resolved config.Tunables rather than
// being pinned by package-level constants. newSink mints a fresh progress
// callback per attempt so a retry starts from a clean parser.
func dockerPullWithRetry(
	ctx context.Context,
	info docker.Info,
	imageRef string,
	attempts int,
	baseDelay time.Duration,
	newSink func() func(string),
) error {
	var lastErr error
	for attempt := 1; attempt <= attempts; attempt++ {
		// A nil factory (no progress wanted) yields a nil sink, which
		// dockerRunStreaming forwards to a no-op lineSplitter.
		var sink func(string)
		if newSink != nil {
			sink = newSink()
		}
		err := dockerRunStreaming(ctx, info, sink, "pull", imageRef)
		if err == nil {
			return nil
		}
		lastErr = err
		if attempt == attempts || ctx.Err() != nil {
			break
		}
		backoff := computePullBackoff(baseDelay, attempt)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
	}
	return lastErr
}

// computePullBackoff returns baseDelay doubled (attempt-1) times, saturated
// at maxPullBackoff and guarded against int64 overflow.  Attempt is
// 1-indexed.
func computePullBackoff(baseDelay time.Duration, attempt int) time.Duration {
	// Any baseDelay <= 0 from a misconfigured caller collapses to the
	// ceiling immediately -- better than a zero-wait tight retry loop.
	if baseDelay <= 0 {
		return maxPullBackoff
	}
	// Double baseDelay (attempt-1) times, bailing early whenever the
	// next doubling would exceed the ceiling.  The halving guard keeps
	// every intermediate value representable as time.Duration (int64 ns).
	shift := max(attempt-1, 0)
	for range shift {
		if baseDelay > maxPullBackoff/2 {
			return maxPullBackoff
		}
		baseDelay *= 2
	}
	if baseDelay > maxPullBackoff {
		return maxPullBackoff
	}
	return baseDelay
}

// maxPullTailLines bounds the rolling buffer of recent output lines kept
// for the error message of a failed streaming pull, so a verbose failure
// does not balloon the wrapped error.
const maxPullTailLines = 20

// maxPullLineBytes caps the length of any single retained output line.
// Docker's own pull output is short, but a registry override pointed at an
// untrusted registry could emit one arbitrarily long newline-free line; the
// cap keeps both the tail buffer and the wrapped error bounded.
const maxPullLineBytes = 4096

// lineSplitter is an io.Writer that reassembles newline-delimited output
// from a child process, forwarding each complete line to onLine and
// retaining the last maxPullTailLines for error context. It is safe to use
// as both Stdout and Stderr of a single command: writes are serialised
// under mu so interleaved stdout/stderr chunks never corrupt a line mid-way.
// onLine is invoked OUTSIDE the lock so a slow callback cannot stall the
// child's pipe (which would block until Write returns).
type lineSplitter struct {
	mu     sync.Mutex
	buf    []byte
	onLine func(string)
	tail   []string
}

func (l *lineSplitter) Write(p []byte) (int, error) {
	l.forward(l.appendAndSplit(p))
	return len(p), nil
}

// appendAndSplit appends p to the buffer and extracts every complete line,
// capping and tailing each under the lock. It returns the extracted lines
// for the caller to forward outside the lock.
func (l *lineSplitter) appendAndSplit(p []byte) []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.buf = append(l.buf, p...)
	var lines []string
	for {
		i := bytes.IndexByte(l.buf, '\n')
		if i < 0 {
			break
		}
		lines = append(lines, l.takeLineLocked(string(l.buf[:i]))) //nolint:makezero // grown incrementally; len is unknown up front
		l.buf = l.buf[i+1:]
	}
	return lines
}

// takeLineLocked trims, length-caps, and folds one line into the tail ring,
// returning the capped line. Must be called with l.mu held.
func (l *lineSplitter) takeLineLocked(line string) string {
	line = strings.TrimRight(line, "\r")
	if len(line) > maxPullLineBytes {
		line = line[:maxPullLineBytes]
	}
	if len(l.tail) == maxPullTailLines {
		copy(l.tail, l.tail[1:])
		l.tail[maxPullTailLines-1] = line
	} else {
		l.tail = append(l.tail, line)
	}
	return line
}

// forward invokes onLine for each non-empty line. Called without the lock
// held so a slow callback never stalls the child process's pipe.
func (l *lineSplitter) forward(lines []string) {
	if l.onLine == nil {
		return
	}
	for _, line := range lines {
		if line != "" {
			l.onLine(line)
		}
	}
}

// flush emits any trailing partial line the process wrote without a final
// newline (Docker's last status line sometimes lacks one).
func (l *lineSplitter) flush() {
	l.mu.Lock()
	var line string
	if len(l.buf) > 0 {
		line = l.takeLineLocked(string(l.buf))
		l.buf = nil
	}
	l.mu.Unlock()
	if line != "" {
		l.forward([]string{line})
	}
}

// tailString returns the sanitized recent output for an error message.
func (l *lineSplitter) tailString() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return sanitizeCLIOutput(strings.Join(l.tail, "\n"))
}

// newLineSplitter builds a lineSplitter with the tail ring pre-sized so it
// never reallocates as lines arrive.
func newLineSplitter(onLine func(string)) *lineSplitter {
	return &lineSplitter{onLine: onLine, tail: make([]string, 0, maxPullTailLines)}
}

// composeRunStreaming runs a docker compose command, forwarding each line of
// combined output to onLine for live progress rendering. It is the streaming
// counterpart of composeRunQuiet: on error the recent output is folded into
// the error message, but the verbose progress never reaches the terminal
// directly (the LiveBox owns the display).
func composeRunStreaming(ctx context.Context, info docker.Info, dir string, onLine func(string), args ...string) error {
	fullArgs := make([]string, 0, len(info.ComposeCmd)-1+len(args))
	fullArgs = append(fullArgs, info.ComposeCmd[1:]...)
	fullArgs = append(fullArgs, args...)

	split := newLineSplitter(onLine)
	c := exec.CommandContext(ctx, info.ComposeCmd[0], fullArgs...) //nolint:gosec // G204: compose binary is CLI-detected (info.ComposeCmd), args internally assembled, never attacker-controlled
	c.Dir = dir
	c.Stdout = split
	c.Stderr = split
	err := c.Run()
	split.flush()
	return streamingError(err, split)
}

// dockerRunStreaming runs a docker command, forwarding each line of combined
// output to onLine for live progress rendering. Streaming counterpart of
// dockerRunQuiet.
func dockerRunStreaming(ctx context.Context, info docker.Info, onLine func(string), args ...string) error {
	dockerBin := info.DockerPath
	if dockerBin == "" {
		dockerBin = "docker"
	}
	split := newLineSplitter(onLine)
	c := exec.CommandContext(ctx, dockerBin, args...) //nolint:gosec // G204: dockerBin is the resolved docker binary (info.DockerPath), args internally assembled, never attacker-controlled
	c.Stdout = split
	c.Stderr = split
	err := c.Run()
	split.flush()
	return streamingError(err, split)
}

// streamingError wraps a streaming command's exit error with its recent
// sanitized output, mirroring the error shape of the *Quiet helpers.
func streamingError(err error, split *lineSplitter) error {
	if err == nil {
		return nil
	}
	if output := split.tailString(); output != "" {
		return fmt.Errorf("%w: %s", err, output)
	}
	return err
}
