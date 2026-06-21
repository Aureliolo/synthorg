package cmd

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
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
	healthTimeout, err := parseStartTimeout(cmd)
	if err != nil {
		return err
	}
	ctx := applyStartNoVerify(cmd)
	opts := GetGlobalOpts(ctx)
	state, err := loadStartState(opts.DataDir)
	if err != nil {
		return err
	}
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}
	if err := assertComposeExists(safeDir); err != nil {
		return err
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	if startDryRun {
		return printStartDryRun(out, state, opts)
	}
	return startContainers(ctx, cmd, state, safeDir, out, errOut, healthTimeout)
}

// parseStartTimeout resolves the health-check budget for `start`. Precedence:
// an explicit --timeout flag wins; otherwise the resolved health_wait_timeout
// tunable (env SYNTHORG_HEALTH_WAIT_TIMEOUT, default 90s) applies. This keeps
// the readiness-wait budget in one place across start and the wipe reinit path.
func parseStartTimeout(cmd *cobra.Command) (time.Duration, error) {
	if !cmd.Flags().Changed("timeout") {
		d := GetGlobalOpts(cmd.Context()).Tunables.HealthWaitTimeout
		if d <= 0 {
			d = config.DefaultHealthWaitTimeout
		}
		return d, nil
	}
	d, err := time.ParseDuration(startTimeout)
	if err != nil {
		return 0, fmt.Errorf("invalid --timeout %q: %w", startTimeout, err)
	}
	if !startNoWait && d <= 0 {
		return 0, fmt.Errorf("invalid --timeout %q: must be > 0", startTimeout)
	}
	return d, nil
}

// applyStartNoVerify mutates the GlobalOpts in cmd's context when
// --no-verify is set so downstream packages observe SkipVerify=true.
// Returns the (possibly refreshed) context.
func applyStartNoVerify(cmd *cobra.Command) context.Context {
	ctx := cmd.Context()
	if !startNoVerify {
		return ctx
	}
	opts := GetGlobalOpts(ctx)
	opts.SkipVerify = true
	cmd.SetContext(SetGlobalOpts(ctx, opts))
	return cmd.Context()
}

// loadStartState wraps config.Load so the start path can surface the
// three distinguishable failure shapes (parse / read / validate) with
// repair hints instead of a generic "loading config:" wrapper.
func loadStartState(dataDir string) (config.State, error) {
	state, err := config.Load(dataDir)
	if err == nil {
		return state, nil
	}
	switch {
	case errors.Is(err, config.ErrParsing):
		return config.State{}, fmt.Errorf(
			"config file is malformed (invalid JSON); "+
				"edit it manually or remove it and re-run "+
				"'synthorg init': %w", err,
		)
	case errors.Is(err, config.ErrReading):
		return config.State{}, fmt.Errorf(
			"config file is unreadable (check filesystem permissions): %w", err,
		)
	default:
		// Validation / DataDir canonicalisation is surfaced as-is with a
		// "config:" prefix so the operator reads the wrapped detail
		// directly.
		return config.State{}, fmt.Errorf("config: %w", err)
	}
}

func assertComposeExists(safeDir string) error {
	// safeDir is the output of safeStateDir -> config.SecurePath, which
	// canonicalises and validates the operator-supplied --data-dir before
	// it reaches this helper, so the os.Stat below operates on an
	// already-sanitised path. A static path-injection tracer may flag it
	// because it cannot follow the sanitiser across the helper boundary;
	// the upstream validation is the guarantee.
	composePath := filepath.Join(safeDir, "compose.yml")
	_, err := os.Stat(composePath)
	if err == nil {
		return nil
	}
	if errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("compose.yml not found in %s", safeDir)
	}
	return fmt.Errorf("checking compose.yml: %w", err)
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
	if startNoPull {
		out.HintNextStep("Remove --dry-run to start the stack (--no-pull: images will not be pulled or verified)")
	} else {
		out.HintNextStep("Remove --dry-run to start the stack")
	}
	return nil
}

func startContainers(ctx context.Context, cmd *cobra.Command, state config.State, safeDir string, out, errOut *ui.UI, healthTimeout time.Duration) error {
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
		refreshed, err := verifyAndPullStartImages(ctx, cmd, info, state, safeDir, out, errOut)
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

func verifyAndPullStartImages(ctx context.Context, cmd *cobra.Command, info docker.Info, state config.State, safeDir string, out, errOut *ui.UI) (config.State, error) {
	if GetGlobalOpts(ctx).SkipVerify {
		errOut.Warn("Image verification skipped (--skip-verify). Containers are NOT verified.")
		out.Blank()
		return pullAllImages(ctx, cmd, info, safeDir, state, out)
	}

	verifyCtx, cancel := context.WithTimeout(ctx, GetGlobalOpts(ctx).Tunables.ImageVerifyTimeout)
	defer cancel()
	result, err := verifyImagesWithCache(verifyCtx, info, state, out, errOut)
	if err != nil {
		return state, err
	}

	if result.SynthOrgReverified {
		if err := writeDigestPinnedCompose(state, synthOrgPins(result.Pins), safeDir); err != nil {
			return state, fmt.Errorf("pinning verified digests: %w", err)
		}
	}

	if result.SynthOrgReverified || result.DHIReverified {
		next, err := cacheVerifiedDigests(ctx, state, result.Pins, errOut)
		if err != nil {
			return state, err
		}
		state = next
	}

	out.Blank()
	return pullAllImages(ctx, cmd, info, safeDir, state, out)
}

// cacheVerifiedDigests stamps result.Pins onto state, persists, and
// reloads. A persist failure is non-fatal (warned to errOut); a reload
// failure is fatal because the live state would otherwise drift from
// disk after the next write.
func cacheVerifiedDigests(ctx context.Context, state config.State, pins map[string]string, errOut *ui.UI) (config.State, error) {
	state.VerifiedDigests = pins
	state.VerifiedImageTag = state.ImageTag
	if err := config.Save(state); err != nil {
		errOut.Warn(fmt.Sprintf("Could not cache verified digests: %v", err))
		return state, nil
	}
	reloaded, reloadErr := config.Load(GetGlobalOpts(ctx).DataDir)
	if reloadErr != nil {
		return state, fmt.Errorf("reloading config after verification: %w", reloadErr)
	}
	return reloaded, nil
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
		// localhost is correct: the CLI polls the docker-compose backend
		// it just started on the same host, via the published port.
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
	// localhost is correct: the started stack publishes these ports on
	// the operator's own host via docker-compose.
	readyLines := []string{
		fmt.Sprintf("%-16s%s", "Dashboard", fmt.Sprintf("http://localhost:%d", state.WebPort)),
		fmt.Sprintf("%-16s%s", "API", fmt.Sprintf("http://localhost:%d", state.BackendPort)),
	}
	out.Box("Ready", readyLines)
	out.Blank()
	out.Section(fmt.Sprintf("Open http://localhost:%d", state.WebPort))
	// Surface the --no-pull caveat BEFORE the routine status-watch tip:
	// "images not verified" is the more consequential message and must
	// not be buried under (or lost to the once-per-session dedup of) the
	// monitoring tip.
	if startNoPull {
		out.HintNextStep("Images not verified -- run 'synthorg update' to pull and verify latest images.")
	}
	out.HintTip("Run 'synthorg status --watch' to monitor container health.")
	return nil
}

// registryOverrideEnvVars lists every env var that, if set, overrides
// a registry / image-tag tunable for the current invocation. Mirrors
// the env precedence inputs ResolveTunables uses; checking these
// directly here avoids forcing callers to call ResolveTunables just
// for the override signal.
var registryOverrideEnvVars = []string{
	config.EnvRegistryHost,
	config.EnvImageRepoPrefix,
	config.EnvDHIRegistry,
	config.EnvPostgresImageTag,
	config.EnvNATSImageTag,
}

// stateHasRegistryOverrides reports whether ANY registry / image-tag
// override is active for the current invocation, taking BOTH the
// persisted State and the per-invocation env vars into account. State
// alone would miss `SYNTHORG_REGISTRY_HOST=ghcr.io synthorg start` (a
// one-shot override that never lands on disk).
//
// When this returns true the caller MUST drop state.VerifiedDigests
// for standalone image pulls (they would pin to default-registry
// digests that do not exist on the override registry) AND must emit
// the verification-disabled stderr warning so the operator knows
// image signature + SLSA verification is OFF for this run. The
// warning is unconditional (not suppressed by --quiet / --json) per
// the cli/CLAUDE.md override-precedence rules.
func stateHasRegistryOverrides(state config.State) bool {
	if state.RegistryHost != "" ||
		state.ImageRepoPrefix != "" ||
		state.DHIRegistry != "" ||
		state.PostgresImageTag != "" ||
		state.NATSImageTag != "" {
		return true
	}
	for _, env := range registryOverrideEnvVars {
		if os.Getenv(env) != "" {
			return true
		}
	}
	return false
}

// warnRegistryOverridesDisableVerification emits the mandatory
// stderr warning (unconditional, not gated by --quiet / --json) that
// image signature + SLSA verification is OFF for this invocation
// because a registry / image-tag override is active. Called once from
// the pull paths in start.go when stateHasRegistryOverrides is true.
func warnRegistryOverridesDisableVerification(cmd *cobra.Command) {
	_, _ = fmt.Fprintln(cmd.ErrOrStderr(),
		"warning: registry / image-tag override active; image signature + SLSA verification disabled for this invocation",
	)
}

// Health-check polling cadence shared by both start paths
// (startDetached and pullStartAndWait).
//
//   - healthPollInterval (2s): trades responsiveness against backend
//     load -- the readyz endpoint is cheap, but polling faster only
//     shaves sub-second latency from a multi-second container start.
//   - healthInitialDelay (5s): a typical compose-up cold start needs
//     a few seconds before /readyz is even bound; polling sooner just
//     burns connection refusals.
//   - dhiVerifyTimeout (120s): caps DHI cosign + SLSA verification per
//     batch; verification stalls past two minutes indicate a network or
//     transparency-log outage rather than a slow CDN.
//
// The total readiness-wait budget for the pullStartAndWait path (called from
// `synthorg wipe` after a destructive reset, which has no --timeout flag) is
// the resolved health_wait_timeout tunable, not a hardcoded constant.
const (
	healthPollInterval = 2 * time.Second
	healthInitialDelay = 5 * time.Second
	dhiVerifyTimeout   = 120 * time.Second
)

// pullStartAndWait pulls images, starts containers, and waits for health.
func pullStartAndWait(ctx context.Context, cmd *cobra.Command, info docker.Info, safeDir string, state config.State, out, errOut *ui.UI) error {
	if _, err := pullAllImages(ctx, cmd, info, safeDir, state, out); err != nil {
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
	healthTimeout := GetGlobalOpts(ctx).Tunables.HealthWaitTimeout
	if healthTimeout <= 0 {
		healthTimeout = config.DefaultHealthWaitTimeout
	}
	if err := health.WaitForHealthy(ctx, healthURL, healthTimeout, healthPollInterval, healthInitialDelay); err != nil {
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
	c := exec.CommandContext(ctx, dockerBin, args...) //nolint:gosec // G204: dockerBin is the resolved docker binary (info.DockerPath), args internally assembled, never attacker-controlled
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

// hasSynthOrgDigests reports whether the SynthOrg pin cache is current.
//
// Two conditions must hold: state.VerifiedImageTag must equal the current
// state.ImageTag (the pin values would otherwise describe images of the
// wrong tag), and a pin must be present for every SynthOrg image enabled
// by the current configuration. The first check mirrors the pin-comparison
// strictness of hasDHIDigests so SynthOrg and DHI cache validity move
// together: either both groups hit, both miss, or each invalidates for an
// independent reason; never one stale and one fresh.
func hasSynthOrgDigests(state config.State) bool {
	if state.VerifiedImageTag != state.ImageTag {
		return false
	}
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
func verifyDHIImages(ctx context.Context, _ docker.Info, state config.State, out, errOut *ui.UI) ([]verify.DHIVerifyResult, error) {
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

	if err != nil {
		// A short result set leaves trailing lines unfinished; mark them as
		// errored so the box shows a cross, not a dangling "...". Surface the
		// air-gapped recovery hint on a transport failure, mirroring the
		// SynthOrg verification path.
		lb.ErrorRemaining()
		if isTransportError(err) {
			errOut.HintError("Use --skip-verify for air-gapped environments")
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

// composeRun runs a docker compose command with output forwarded to the
// Cobra command's stdout/stderr.
func composeRun(ctx context.Context, cobraCmd *cobra.Command, info docker.Info, dir string, args ...string) error {
	fullArgs := make([]string, 0, len(info.ComposeCmd)-1+len(args))
	fullArgs = append(fullArgs, info.ComposeCmd[1:]...)
	fullArgs = append(fullArgs, args...)

	c := exec.CommandContext(ctx, info.ComposeCmd[0], fullArgs...) //nolint:gosec // G204: compose binary is CLI-detected (info.ComposeCmd), args internally assembled, never attacker-controlled
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
	c := exec.CommandContext(ctx, info.ComposeCmd[0], fullArgs...) //nolint:gosec // G204: compose binary is CLI-detected (info.ComposeCmd), args internally assembled, never attacker-controlled
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
