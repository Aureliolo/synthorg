package cmd

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var (
	startNoWait   bool
	startTimeout  string
	startNoPull   bool
	startDryRun   bool
	startNoDetach bool
	startNoVerify bool
)

var startCmd = &cobra.Command{
	Use:   "start",
	Short: "Pull images and start the SynthOrg stack",
	Long: `Start every container in the SynthOrg compose stack.

By default this pulls each image (verifying signatures and SLSA
attestations against the pinned digests) before bringing the stack
up detached, then waits for the backend's /api/v1/readyz to return
healthy. Pass --no-pull to skip the pull when iterating locally,
--no-detach to stream logs in the foreground, or --dry-run to print
the docker commands the run would issue without executing them.`,
	Example: `  synthorg start              # pull, verify, and start
  synthorg start --no-pull    # start without pulling images
  synthorg start --dry-run    # preview what would happen
  synthorg start --no-detach  # run in foreground (stream logs)`,
	RunE: runStart,
}

func init() {
	startCmd.Flags().BoolVar(&startNoWait, "no-wait", false, "skip health check after start")
	startCmd.Flags().StringVar(&startTimeout, "timeout", "90s", "health check timeout (e.g. 90s, 2m)")
	startCmd.Flags().BoolVar(&startNoPull, "no-pull", false, "skip image verification and pull")
	startCmd.Flags().BoolVar(&startDryRun, "dry-run", false, "show what would happen without executing")
	startCmd.Flags().BoolVar(&startNoDetach, "no-detach", false, "run in foreground (stream logs, Ctrl+C to stop)")
	startCmd.Flags().BoolVar(&startNoVerify, "no-verify", false, "skip image signature verification (alias for --skip-verify)")
	startCmd.GroupID = "core"
	rootCmd.AddCommand(startCmd)
}

func runStart(cmd *cobra.Command, _ []string) error {
	if err := validateStartFlags(cmd); err != nil {
		return err
	}
	healthTimeout, parseErr := time.ParseDuration(startTimeout)
	if parseErr != nil {
		return fmt.Errorf("invalid --timeout %q: %w", startTimeout, parseErr)
	}
	if !startNoWait && healthTimeout <= 0 {
		return fmt.Errorf("invalid --timeout %q: must be > 0", startTimeout)
	}

	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	if startNoVerify {
		opts.SkipVerify = true
		cmd.SetContext(SetGlobalOpts(ctx, opts))
		ctx = cmd.Context()
	}

	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}
	composePath := filepath.Join(safeDir, "compose.yml")
	if _, err := os.Stat(composePath); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("compose.yml not found in %s -- run 'synthorg init' first", safeDir)
		}
		return fmt.Errorf("checking compose.yml: %w", err)
	}

	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

	if startDryRun {
		return printStartDryRun(out, state, opts)
	}
	return startContainers(cmd, ctx, state, safeDir, out, errOut, healthTimeout)
}

func validateStartFlags(cmd *cobra.Command) error {
	if startNoDetach && startNoWait {
		return fmt.Errorf("--no-detach and --no-wait are incompatible (foreground mode has no health check to skip)")
	}
	if startNoDetach && cmd.Flags().Changed("timeout") {
		return fmt.Errorf("--no-detach and --timeout are incompatible")
	}
	return nil
}

func printStartDryRun(out *ui.UI, state config.State, opts *GlobalOpts) error {
	out.KeyValue("Image tag", state.ImageTag)
	out.KeyValue("Backend port", strconv.Itoa(state.BackendPort))
	out.KeyValue("Web port", strconv.Itoa(state.WebPort))
	out.KeyValue("Sandbox", strconv.FormatBool(state.Sandbox))
	out.KeyValue("Skip verify", strconv.FormatBool(opts.SkipVerify || startNoPull))
	out.KeyValue("Skip pull", strconv.FormatBool(startNoPull))
	out.KeyValue("Detached", strconv.FormatBool(!startNoDetach))
	out.KeyValue("Health check", strconv.FormatBool(!startNoWait && !startNoDetach))
	out.Step("Dry run -- no changes made")
	out.HintNextStep("Remove --dry-run to start the stack")
	return nil
}

func startContainers(cmd *cobra.Command, ctx context.Context, state config.State, safeDir string, out, errOut *ui.UI, healthTimeout time.Duration) error {
	if os.Getenv("SYNTHORG_NO_LOGO") == "" {
		out.Logo(version.Version)
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		return err
	}
	out.InlineKV(
		"Docker", info.DockerVersion+" "+ui.IconSuccess,
		"Compose", info.ComposeVersion+" "+ui.IconSuccess,
	)
	out.Blank()

	for _, w := range docker.CheckMinVersions(info) {
		errOut.Warn(w)
	}

	if !startNoPull {
		refreshed, err := verifyAndPullStartImages(cmd, ctx, info, state, safeDir, out, errOut)
		if err != nil {
			return err
		}
		state = refreshed
	}

	if startNoDetach {
		out.Step("Starting in foreground mode (Ctrl+C to stop)...")
		out.HintGuidance("Press Ctrl+C to stop. Logs stream directly to this terminal.")
		return composeRun(ctx, cmd, info, safeDir, "up")
	}

	return startDetached(ctx, info, safeDir, state, out, errOut, healthTimeout)
}

func verifyAndPullStartImages(cmd *cobra.Command, ctx context.Context, info docker.Info, state config.State, safeDir string, out, errOut *ui.UI) (config.State, error) {
	skipVerify := GetGlobalOpts(ctx).SkipVerify

	if !skipVerify {
		// SynthOrg images: check cache independently.
		if hasSynthOrgDigests(state) {
			renderCachedSynthOrgBox(out, state)
		} else {
			if err := verifyAndPinImages(ctx, cmd, state, safeDir, out, errOut); err != nil {
				return state, err
			}
			// Reload state since verifyAndPinImages saved it.
			reloaded, reloadErr := config.Load(GetGlobalOpts(ctx).DataDir)
			if reloadErr != nil {
				return state, fmt.Errorf("reloading config after verification: %w", reloadErr)
			}
			state = reloaded
		}

		// DHI images: check cache independently.
		if hasDHIDigests(state) {
			renderCachedDHIBox(out, state)
		} else {
			results, err := verifyDHIImages(ctx, info, state, out, errOut)
			if err != nil {
				return state, fmt.Errorf("DHI image verification failed: %w", err)
			}
			if state.VerifiedDigests == nil {
				state.VerifiedDigests = make(map[string]string)
			}
			for _, r := range results {
				if indexDigest, ok := verify.DHIPinnedIndexDigest(r.Image); ok {
					state.VerifiedDigests["dhi:"+r.Image] = indexDigest
				}
				if r.Digest != "" {
					state.VerifiedDigests["dhi:"+r.Image+":platform"] = r.Digest
				}
				if r.AttDigest != "" {
					state.VerifiedDigests["dhi:"+r.Image+":attestation"] = r.AttDigest
				}
				if r.SigDigest != "" {
					state.VerifiedDigests["dhi:"+r.Image+":signature"] = r.SigDigest
				}
			}
			if err := config.Save(state); err != nil {
				errOut.Warn(fmt.Sprintf("Could not cache DHI verification results: %v", err))
			}
		}
	} else {
		errOut.Warn("Image verification skipped (--skip-verify). Containers are NOT verified.")
	}

	out.Blank()
	refreshed, err := pullAllImages(ctx, info, safeDir, state, out)
	if err != nil {
		return state, err
	}
	return refreshed, nil
}

func startDetached(ctx context.Context, info docker.Info, safeDir string, state config.State, out, errOut *ui.UI, healthTimeout time.Duration) error {
	if state.PersistenceBackend == "postgres" {
		out.Step("Starting postgres container (backend will wait for it and apply migrations)")
	}
	sp := out.StartSpinner("Starting containers...")
	if err := composeRunQuiet(ctx, info, safeDir, "up", "-d"); err != nil {
		sp.Error("Failed to start containers")
		return fmt.Errorf("starting containers: %w", err)
	}
	sp.Success("Containers started")

	if !startNoWait {
		sp = out.StartSpinner("Waiting for backend to become healthy...")
		healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", state.BackendPort)
		if err := health.WaitForHealthy(ctx, healthURL, healthTimeout, healthPollInterval, healthInitialDelay); err != nil {
			sp.Error("Health check failed")
			errOut.HintError("Run 'synthorg doctor' for diagnostics.")
			return fmt.Errorf("health check did not pass: %w", err)
		}
		sp.Success("Backend healthy")
		if state.PersistenceBackend == "postgres" {
			out.Step("Postgres migrations checked/applied during backend startup")
		}
	} else {
		out.Step("Health check skipped (--no-wait)")
		out.HintGuidance("Run 'synthorg status --check' to verify health later.")
	}

	out.Blank()
	readyLines := []string{
		fmt.Sprintf("%-16s%s", "Dashboard", fmt.Sprintf("http://localhost:%d", state.WebPort)),
		fmt.Sprintf("%-16s%s", "API", fmt.Sprintf("http://localhost:%d", state.BackendPort)),
	}
	out.Box("Ready", readyLines)
	out.Blank()
	out.Section(fmt.Sprintf("Open http://localhost:%d", state.WebPort))
	out.HintTip("Run 'synthorg status --watch' to monitor container health.")
	if startNoPull {
		out.HintGuidance("Images not verified -- run 'synthorg update' to pull and verify latest images.")
	}
	return nil
}

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
func pullAllImages(ctx context.Context, info docker.Info, safeDir string, state config.State, out *ui.UI) (config.State, error) {
	refreshed := state

	// Build the full list of images to pull.
	type pullItem struct {
		name    string
		compose bool   // true = docker compose pull, false = docker pull
		ref     string // image ref for docker pull (only when compose=false)
	}

	var items []pullItem
	// Compose services
	for _, svc := range composeServiceNames(refreshed) {
		items = append(items, pullItem{name: svc, compose: true})
	}
	// Standalone images (only if enabled)
	if refreshed.Sandbox {
		items = append(items, pullItem{
			name: "sandbox",
			ref:  verify.FormatImageRef("sandbox", refreshed.ImageTag, refreshed.VerifiedDigests["sandbox"]),
		})
		items = append(items, pullItem{
			name: "sidecar",
			ref:  verify.FormatImageRef("sidecar", refreshed.ImageTag, refreshed.VerifiedDigests["sidecar"]),
		})
	}
	fineTuneVariant := ""
	if refreshed.FineTuning {
		fineTuneVariant = refreshed.FineTuneVariantOrDefault()
		fineTuneSvc := verify.FineTuneServiceName(fineTuneVariant)
		items = append(items, pullItem{
			name: fineTuneSvc,
			ref:  verify.FormatImageRef(fineTuneSvc, refreshed.ImageTag, refreshed.VerifiedDigests[fineTuneSvc]),
		})
	}

	// Emit the fine-tune size hint BEFORE the pull box renders, so the
	// user understands why their terminal is about to pause. Emitting it
	// after the pull (the old behaviour) was a logic error: by the time
	// the warning appeared, the wait had already completed. The per-
	// variant size matches the post-split image layout (see PR #1442).
	if fineTuneVariant != "" {
		sizeHint := "up to ~4 GB"
		if fineTuneVariant == config.FineTuneVariantCPU {
			sizeHint = "~1.7 GB"
		}
		out.HintTip(fmt.Sprintf(
			"Fine-tune image is %s -- first pull can take a few minutes on typical connections.",
			sizeHint,
		))
	}

	// Show all pulls in one LiveBox.
	labels := make([]string, len(items))
	for i, item := range items {
		labels[i] = item.name
	}
	lb := out.NewLiveBox("Pull Images", labels)
	defer lb.Finish()

	var (
		mu      sync.Mutex
		pullErr error
	)
	var wg sync.WaitGroup
	for i, item := range items {
		wg.Add(1)
		go func(idx int, it pullItem) {
			defer wg.Done()
			var err error
			if it.compose {
				err = composeRunQuiet(ctx, info, safeDir, "pull", it.name)
			} else {
				tun := GetGlobalOpts(ctx).Tunables
				err = dockerPullWithRetry(
					ctx, info, it.ref,
					tun.ImagePullAttempts, tun.ImagePullRetryDelay,
				)
			}
			if err != nil {
				lb.UpdateLine(idx, ui.IconError)
				mu.Lock()
				pullErr = errors.Join(pullErr, fmt.Errorf("pulling %s: %w", it.name, err))
				mu.Unlock()
			} else {
				lb.UpdateLine(idx, ui.IconSuccess)
			}
		}(i, item)
	}
	wg.Wait()

	return refreshed, pullErr
}

// maxPullBackoff caps the exponential-backoff delay between image-pull
// retries.  Guards against int64 overflow when operators set
// ImagePullAttempts to a high value: “baseDelay << (attempt - 1)“
// with a 2-second base and >=62 attempts would overflow time.Duration
// (int64 nanoseconds) and yield a negative delay that time.After
// resolves immediately, effectively disabling the backoff.  Saturating
// at 5 minutes keeps the retry schedule bounded and predictable.
const maxPullBackoff = 5 * time.Minute

// Health-check polling cadence shared by both start paths
// (startDetached and pullStartAndWait).  pullStartHealthTimeout is the
// fallback total budget for the pullStartAndWait path which has no
// --timeout flag.  dhiVerifyTimeout caps DHI cosign + SLSA verification.
const (
	healthPollInterval     = 2 * time.Second
	healthInitialDelay     = 5 * time.Second
	pullStartHealthTimeout = 90 * time.Second
	dhiVerifyTimeout       = 120 * time.Second
)

// dockerPullWithRetry pulls an image with retries for transient failures.
// The caller supplies attempts (> 0) and baseDelay (exponential backoff
// seed) so the values flow from the resolved
// config.Tunables rather than being pinned by package-level constants.
func dockerPullWithRetry(
	ctx context.Context,
	info docker.Info,
	imageRef string,
	attempts int,
	baseDelay time.Duration,
) error {
	var lastErr error
	for attempt := 1; attempt <= attempts; attempt++ {
		if err := dockerRunQuiet(ctx, info, "pull", imageRef); err == nil {
			return nil
		} else {
			lastErr = err
		}
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

// computePullBackoff returns “baseDelay << (attempt - 1)“ saturated
// at maxPullBackoff and guarded against int64 overflow.  Attempt is
// 1-indexed.
func computePullBackoff(baseDelay time.Duration, attempt int) time.Duration {
	// Any baseDelay <= 0 from a misconfigured caller collapses to the
	// ceiling immediately -- better than a zero-wait tight retry loop.
	if baseDelay <= 0 {
		return maxPullBackoff
	}
	// Compute the shift amount that would exceed the ceiling so we
	// can bail out before int64 overflow.  The ceiling is checked
	// against every candidate backoff.
	shift := max(attempt-1, 0)
	// math.MaxInt64 / baseDelay gives the largest multiplier that
	// stays in range; log2 of that caps the safe shift.
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

// pullStartAndWait pulls images, starts containers, and waits for health.
func pullStartAndWait(ctx context.Context, info docker.Info, safeDir string, state config.State, out, errOut *ui.UI) error {
	if _, err := pullAllImages(ctx, info, safeDir, state, out); err != nil {
		return err
	}

	sp := out.StartSpinner("Starting containers...")
	if err := composeRunQuiet(ctx, info, safeDir, "up", "-d"); err != nil {
		sp.Error("Failed to start containers")
		return fmt.Errorf("starting containers: %w", err)
	}
	sp.Success("Containers started")

	sp = out.StartSpinner("Waiting for backend to become healthy...")
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/readyz", state.BackendPort)
	if err := health.WaitForHealthy(ctx, healthURL, pullStartHealthTimeout, healthPollInterval, healthInitialDelay); err != nil {
		sp.Error("Health check failed")
		errOut.HintError("Run 'synthorg doctor' for diagnostics.")
		return fmt.Errorf("health check did not pass: %w", err)
	}
	sp.Success("Backend healthy")
	if state.PersistenceBackend == "postgres" {
		out.Step("Postgres migrations checked/applied during backend startup")
	}
	return nil
}

// composeServiceNames returns the compose service names that need pulling
// based on the current config. The sandbox and sidecar images are not
// compose services -- they are pulled separately.
func composeServiceNames(state config.State) []string {
	services := []string{"backend", "web"}
	if state.PersistenceBackend == "postgres" {
		services = append(services, "postgres")
	}
	if state.BusBackend == "nats" {
		services = append(services, "nats")
	}
	return services
}

// dockerRunQuiet runs a docker command with output captured in a buffer.
// Mirrors composeRunQuiet but shells out to `docker` directly via the
// resolved binary path from docker.Info -- used for operations that
// aren't tied to a compose service (e.g. pulling the sandbox image).
func dockerRunQuiet(ctx context.Context, info docker.Info, args ...string) error {
	dockerBin := info.DockerPath
	if dockerBin == "" {
		dockerBin = "docker"
	}
	var buf bytes.Buffer
	c := exec.CommandContext(ctx, dockerBin, args...)
	c.Stdout = &buf
	c.Stderr = &buf
	if err := c.Run(); err != nil {
		output := sanitizeCLIOutput(buf.String())
		if output != "" {
			return fmt.Errorf("%w: %s", err, output)
		}
		return err
	}
	return nil
}

// verifyAndPinImages verifies image signatures (unless --skip-verify) and
// pins the verified digests in the compose file and config.
func verifyAndPinImages(ctx context.Context, _ *cobra.Command, state config.State, safeDir string, out, errOut *ui.UI) error {
	if GetGlobalOpts(ctx).SkipVerify {
		errOut.Warn("Image verification skipped (--skip-verify). Containers are NOT verified.")
		return nil
	}

	imageRefs := verify.BuildImageRefs(state.ImageTag, state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault())
	labels := make([]string, len(imageRefs))
	for i, ref := range imageRefs {
		labels[i] = ref.Name()
	}
	lb := out.NewLiveBox("Verify SynthOrg Images", labels)

	verifyCtx, cancel := context.WithTimeout(ctx, GetGlobalOpts(ctx).Tunables.ImageVerifyTimeout)
	defer cancel()
	results, err := verify.VerifyImages(verifyCtx, verify.VerifyOptions{
		Images: imageRefs,
		Output: io.Discard,
		OnResult: func(i int, r verify.VerifyResult) {
			slsaIcon := ui.IconSuccess
			if !r.ProvenanceVerified {
				slsaIcon = ui.IconWarning
			}
			lb.UpdateLine(i, fmt.Sprintf("sig %s  slsa %s", ui.IconSuccess, slsaIcon))
		},
	})
	lb.Finish()

	if err != nil {
		if isTransportError(err) {
			errOut.HintError("Use --skip-verify for air-gapped environments")
		}
		return fmt.Errorf("image verification failed: %w", err)
	}

	pins, err := digestPinMap(results)
	if err != nil {
		return fmt.Errorf("digest pin map: %w", err)
	}

	if err := writeDigestPinnedCompose(state, pins, safeDir); err != nil {
		return fmt.Errorf("pinning verified digests: %w", err)
	}

	state.VerifiedDigests = pins
	if err := config.Save(state); err != nil {
		errOut.Warn(fmt.Sprintf("Could not cache verified digests: %v", err))
	}
	return nil
}

// hasSynthOrgDigests returns true if all SynthOrg image digests are cached.
func hasSynthOrgDigests(state config.State) bool {
	if len(state.VerifiedDigests) == 0 {
		return false
	}
	for _, ref := range verify.BuildImageRefs(state.ImageTag, state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault()) {
		if _, ok := state.VerifiedDigests[ref.Name()]; !ok {
			return false
		}
	}
	return true
}

// hasDHIDigests returns true if all DHI image digests are cached AND
// match the current index pins baked into the binary. When Renovate
// bumps a pin, the cache misses and re-verification triggers.
func hasDHIDigests(state config.State) bool {
	for _, tp := range thirdPartyImages(state) {
		if !strings.HasPrefix(tp.Image, "dhi.io/") {
			continue
		}
		cached, ok := state.VerifiedDigests["dhi:"+tp.Image]
		if !ok {
			return false
		}
		current, pinOK := verify.DHIPinnedIndexDigest(tp.Image)
		if !pinOK || cached != current {
			return false
		}
	}
	return true
}

func renderCachedSynthOrgBox(out *ui.UI, state config.State) {
	refs := verify.BuildImageRefs(state.ImageTag, state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault())
	lines := make([]string, len(refs))
	for i, ref := range refs {
		lines[i] = fmt.Sprintf("  %-12s sig %s  slsa %s", ref.Name(), ui.IconSuccess, ui.IconSuccess)
	}
	out.Box("Verify SynthOrg Images (cached)", lines)
}

func renderCachedDHIBox(out *ui.UI, state config.State) {
	var lines []string
	for _, tp := range thirdPartyImages(state) {
		if !strings.HasPrefix(tp.Image, "dhi.io/") {
			continue
		}
		shortName := tp.Name
		lines = append(lines, fmt.Sprintf("  %-12s sig %s  slsa %s", shortName, ui.IconSuccess, ui.IconSuccess))
	}
	if len(lines) > 0 {
		out.Box("Verify DHI Images (cached)", lines)
	}
}

// verifyDHIImages verifies cosign signatures and SLSA provenance on
// third-party DHI images using Docker's embedded public key. Called
// BEFORE pulling to prevent MITM.
func verifyDHIImages(ctx context.Context, _ docker.Info, state config.State, out, _ *ui.UI) ([]verify.DHIVerifyResult, error) {
	var dhiRefs []string
	var labels []string
	for _, tp := range thirdPartyImages(state) {
		if strings.HasPrefix(tp.Image, "dhi.io/") {
			dhiRefs = append(dhiRefs, tp.Image)
			labels = append(labels, tp.Name)
		}
	}
	if len(dhiRefs) == 0 {
		return nil, nil
	}

	lb := out.NewLiveBox("Verify DHI Images", labels)
	defer lb.Finish()

	// Verify each image with a timeout to prevent hanging on network issues.
	dhiCtx, dhiCancel := context.WithTimeout(ctx, dhiVerifyTimeout)
	defer dhiCancel()
	results, err := verify.VerifyDHIImages(dhiCtx, dhiRefs)

	// Update LiveBox lines from results.
	for i, r := range results {
		if r.SigOK {
			slsaIcon := ui.IconSuccess
			if !r.SLSAOK {
				slsaIcon = ui.IconWarning
			}
			lb.UpdateLine(i, fmt.Sprintf("sig %s  slsa %s", ui.IconSuccess, slsaIcon))
		} else {
			lb.UpdateLine(i, ui.IconError)
		}
	}

	return results, err
}

// thirdPartyImage pairs a service name with its image reference.
type thirdPartyImage struct {
	Name  string
	Image string
}

// thirdPartyImages returns the image references of third-party (non-SynthOrg)
// containers that need digest pinning, based on config. Returns a slice for
// deterministic iteration order in UI rendering and verification.
func thirdPartyImages(state config.State) []thirdPartyImage {
	var images []thirdPartyImage
	if state.PersistenceBackend == "postgres" {
		images = append(images, thirdPartyImage{"postgres", "dhi.io/postgres:" + config.DefaultPostgresImageTag})
	}
	if state.BusBackend == "nats" {
		images = append(images, thirdPartyImage{"nats", "dhi.io/nats:" + config.DefaultNATSImageTag})
	}
	return images
}

// writeDigestPinnedCompose generates and writes a compose file with digest-pinned
// image references. Shared by start.go and update.go verification flows.
//
// Uses atomic write (temp file + rename) to prevent a partial write from
// corrupting the compose file if the process is interrupted.
func writeDigestPinnedCompose(state config.State, digestPins map[string]string, safeDir string) error {
	params, err := compose.ParamsFromState(state)
	if err != nil {
		return fmt.Errorf("building compose params: %w", err)
	}
	params.DigestPins = digestPins

	composeYAML, err := compose.Generate(params)
	if err != nil {
		return fmt.Errorf("generating compose file: %w", err)
	}

	return compose.WriteComposeAndNATS("compose.yml", composeYAML, state.BusBackend, safeDir)
}

// digestPinMap converts verification results to a map of image name -> digest
// for use in compose generation. Returns an error if any result has an empty
// digest -- after successful verification all digests must be resolved.
func digestPinMap(results []verify.VerifyResult) (map[string]string, error) {
	pins := make(map[string]string, len(results))
	for _, r := range results {
		if r.Ref.Digest == "" {
			return nil, fmt.Errorf("image %s has no resolved digest after verification", r.Ref.Name())
		}
		pins[r.Ref.Name()] = r.Ref.Digest
	}
	return pins, nil
}

// renderVerifyBox displays image verification results in a bordered box.
// Shared by start.go and update.go verification flows.
// Uses plain glyph constants (not ANSI-styled icons) because Box()
// sanitizes content with stripControlStrict which removes ESC bytes.
func renderVerifyBox(out *ui.UI, results []verify.VerifyResult) {
	boxLines := make([]string, 0, len(results))
	for _, r := range results {
		sigIcon := ui.IconSuccess
		slsaIcon := ui.IconSuccess
		if !r.ProvenanceVerified {
			slsaIcon = ui.IconWarning
		}
		boxLines = append(boxLines, fmt.Sprintf("  %-12s sig %s  slsa %s",
			r.Ref.Name(), sigIcon, slsaIcon))
	}
	out.Box("Verify SynthOrg Images", boxLines)
}

// composeRun runs a docker compose command with output forwarded to the
// Cobra command's stdout/stderr.
func composeRun(ctx context.Context, cobraCmd *cobra.Command, info docker.Info, dir string, args ...string) error {
	fullArgs := make([]string, 0, len(info.ComposeCmd)-1+len(args))
	fullArgs = append(fullArgs, info.ComposeCmd[1:]...)
	fullArgs = append(fullArgs, args...)

	c := exec.CommandContext(ctx, info.ComposeCmd[0], fullArgs...)
	c.Dir = dir
	c.Stdout = cobraCmd.OutOrStdout()
	c.Stderr = cobraCmd.ErrOrStderr()
	return c.Run()
}

// composeRunQuiet runs a docker compose command with output captured in
// a buffer. On error, the sanitized output is included in the error message.
// Used when a spinner is shown and Docker's verbose output should be hidden.
func composeRunQuiet(ctx context.Context, info docker.Info, dir string, args ...string) error {
	fullArgs := make([]string, 0, len(info.ComposeCmd)-1+len(args))
	fullArgs = append(fullArgs, info.ComposeCmd[1:]...)
	fullArgs = append(fullArgs, args...)

	var buf bytes.Buffer
	c := exec.CommandContext(ctx, info.ComposeCmd[0], fullArgs...)
	c.Dir = dir
	c.Stdout = &buf
	c.Stderr = &buf
	if err := c.Run(); err != nil {
		output := sanitizeCLIOutput(buf.String())
		if output != "" {
			return fmt.Errorf("%w: %s", err, output)
		}
		return err
	}
	return nil
}

// sanitizeCLIOutput strips control characters from external CLI output
// before including it in error messages, preserving only printable text
// and newlines for readability.
func sanitizeCLIOutput(s string) string {
	s = strings.Map(func(r rune) rune {
		if (r < 0x20 && r != '\n') || r == 0x7F || (r >= 0x80 && r <= 0x9F) {
			return -1
		}
		return r
	}, s)
	return strings.TrimSpace(s)
}
