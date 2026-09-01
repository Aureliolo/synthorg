// Package cmd defines the CLI commands for SynthOrg.
package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/Aureliolo/synthorg/cli/internal/completion"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/diagnostics"
	"github.com/Aureliolo/synthorg/cli/internal/health"
	"github.com/Aureliolo/synthorg/cli/internal/selfupdate"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
	"github.com/spf13/cobra"
)

// Flag variables for persistent flags.
var (
	flagDataDir    string
	flagSkipVerify bool
	flagQuiet      bool
	flagVerbose    int
	flagNoColor    bool
	flagPlain      bool
	flagJSON       bool
	flagYes        bool
	flagHelpAll    bool
)

var rootCmd = &cobra.Command{
	Use:   "synthorg",
	Short: "SynthOrg CLI -- manage your SynthOrg installation",
	Long: `SynthOrg CLI manages the lifecycle of your SynthOrg installation.

Run 'synthorg init' to set up a new installation, then 'synthorg start'
to launch the backend and web dashboard containers.`,
	SilenceUsage:  true,
	SilenceErrors: true,
	// IMPORTANT: Cobra does NOT chain PersistentPreRunE. If any subcommand
	// defines its own PersistentPreRunE or PreRunE, this hook is silently
	// skipped and GlobalOpts will fall back to zero-value defaults. Always
	// call setupGlobalOpts explicitly in any subcommand pre-run hook.
	PersistentPreRunE: func(cmd *cobra.Command, _ []string) error {
		if flagHelpAll {
			printAllHelp(cmd.Root(), 0)
			return NewExitError(ExitSuccess, nil)
		}
		return setupGlobalOpts(cmd)
	},
}

func init() {
	// Command groups for organized --help output.
	rootCmd.AddGroup(
		&cobra.Group{ID: "core", Title: "Core Commands:"},
		&cobra.Group{ID: "lifecycle", Title: "Lifecycle Commands:"},
		&cobra.Group{ID: "data", Title: "Data Commands:"},
		&cobra.Group{ID: "diagnostics", Title: "Diagnostics:"},
	)

	// Did-you-mean suggestions for mistyped commands.
	rootCmd.SuggestionsMinimumDistance = 2

	pf := rootCmd.PersistentFlags()
	pf.StringVar(&flagDataDir, "data-dir", "", "data directory (default: platform-appropriate)")
	pf.BoolVar(&flagSkipVerify, "skip-verify", false,
		"skip container image signature and provenance verification (NOT RECOMMENDED)")
	pf.BoolVarP(&flagQuiet, "quiet", "q", false, "suppress non-essential output (errors only)")
	pf.CountVarP(&flagVerbose, "verbose", "v", "increase verbosity (-v=verbose, -vv=trace)")
	pf.BoolVar(&flagNoColor, "no-color", false, "disable ANSI color output")
	pf.BoolVar(&flagPlain, "plain", false, "ASCII-only output (no Unicode, no spinners, no box drawing)")
	pf.BoolVar(&flagJSON, "json", false, "output machine-readable JSON")
	pf.BoolVarP(&flagYes, "yes", "y", false, "assume yes for all prompts (non-interactive mode)")
	pf.BoolVar(&flagHelpAll, "help-all", false, "show help for all commands (recursive)")

	// Note: the SYNTHORG_SKIP_VERIFY env var is resolved inside
	// setupGlobalOpts alongside all other env var overrides.
}

// setupGlobalOpts resolves the effective configuration from flags, env vars,
// and config file, then stores GlobalOpts in the command context.
func setupGlobalOpts(cmd *cobra.Command) error {
	noColor, quiet, yes, skipVerify := resolveEnvOverrides()

	if quiet && flagVerbose > 0 {
		return fmt.Errorf("--quiet and --verbose are mutually exclusive")
	}
	if flagPlain && flagJSON {
		return fmt.Errorf("--plain and --json are mutually exclusive")
	}

	opts := &GlobalOpts{
		DataDir:    resolveDataDir(),
		SkipVerify: skipVerify,
		Quiet:      quiet,
		Verbose:    flagVerbose,
		NoColor:    noColor,
		Plain:      flagPlain,
		JSON:       flagJSON,
		Yes:        yes,
		Hints:      "auto",
		Tunables:   config.DefaultTunables(),
	}

	applyConfigOverrides(opts)

	if !validHintsMode(opts.Hints) {
		return fmt.Errorf("invalid hints mode %q: must be always, auto, or never", opts.Hints)
	}

	if err := config.ValidateAPIPrefix(); err != nil {
		// The same variable is interpolated into the compose file, so a
		// value the CLI refuses is one the backend would not serve either.
		// Recovery commands still run: an operator has to be able to reach
		// doctor and wipe while their environment is wrong.
		if isRecoveryCommand(cmd) {
			ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions()).WarnAlways(fmt.Sprintf(
				"%v; continuing against the default API prefix so this command "+
					"can still repair the install.", err))
		} else {
			return err
		}
	}

	if err := applyTunables(cmd, opts); err != nil {
		// Recovery commands must stay callable even when config.json is
		// unreadable or fails validation -- otherwise the user has no
		// way to repair a broken config without hand-editing the file
		// they would normally fix via the CLI.
		if isRecoveryCommand(cmd) {
			ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions()).Warn(fmt.Sprintf(
				"Tunable resolution failed (%v); continuing with defaults so this "+
					"command can still repair the install.", err))
			opts.Tunables = config.DefaultTunables()
		} else {
			return err
		}
	}

	cmd.SetContext(SetGlobalOpts(cmd.Context(), opts))
	return nil
}

// isRecoveryCommand reports whether cmd is one of the commands that must
// stay usable when config.json is broken. These commands exist precisely
// to repair, inspect, or erase the install, so refusing to run them
// because the file they are meant to fix/remove is invalid would leave
// the user stranded with no in-CLI path out.
//
// Included:
//   - config edit/path/show + bare `config`: needed to repair a bad file
//   - doctor: diagnoses the exact breakage
//   - version, help: pure informational, must never depend on state
//   - init: replaces/creates the config from scratch
//   - wipe, uninstall: tear down the install even when config is garbage
func isRecoveryCommand(cmd *cobra.Command) bool {
	switch cmd.CommandPath() {
	case "synthorg config edit",
		"synthorg config path",
		"synthorg config show",
		"synthorg config",
		"synthorg doctor",
		"synthorg version",
		"synthorg help",
		"synthorg init",
		"synthorg wipe",
		"synthorg uninstall":
		return true
	}
	return false
}

// applyTunables resolves the effective tunable values (env > state >
// default) and seeds every consumer package that reads them from a
// package-level variable. Consumers are Configure()d exactly once per
// CLI invocation so later goroutines can read without locking.
//
// When the resolved tunables point at a custom registry or image tag
// (CustomRegistry=true) this function also forces SkipVerify and emits
// a loud one-shot warning. The pinned DHI digests and the sigstore SAN
// regex are both bound to the default registry+tags, so image signature
// and SLSA provenance verification cannot succeed against a user-chosen
// deployment target. The operator explicitly opted into that deployment
// by setting the override, so the CLI does not refuse to run; it simply
// transfers trust to the operator and makes the trade-off visible.
func applyTunables(cmd *cobra.Command, opts *GlobalOpts) error {
	// config.Load already returns a DefaultState (not an error) when the
	// file is absent, so any error here is a real failure (corrupted
	// JSON, permission denied, validation failure, invalid path). Fail
	// fast so persisted overrides and trust-transfer detection are
	// never silently dropped by swallowing this error.
	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	warnCoercedConfigFields(cmd, opts, state)
	tun, err := config.ResolveTunables(state)
	if err != nil {
		return fmt.Errorf("resolving tunables: %w", err)
	}
	opts.Tunables = tun

	verify.Configure(
		tun.RegistryHost, tun.ImageRepoPrefix,
		tun.DHIRegistry, tun.PostgresImageTag, tun.NATSImageTag,
		tun.TUFFetchTimeout, tun.AttestationHTTPTimeout,
	)
	selfupdate.Configure(
		tun.MaxAPIResponseBytes, tun.MaxBinaryBytes, tun.MaxArchiveEntryBytes,
		tun.SelfUpdateHTTPTimeout, tun.SelfUpdateAPITimeout, tun.TUFFetchTimeout,
	)
	health.Configure(tun.HealthCheckTimeout)
	completion.Configure(tun.CompletionProbeTimeout)
	diagnostics.Configure(tun.DiagnosticsDialTimeout)
	// backup.go and root.go share package cmd, so the resolved cap is set
	// directly on the package var rather than via a Configure() seam.
	maxBackupResponseBytes = tun.MaxAPIResponseBytes

	if tun.CustomRegistry {
		opts.SkipVerify = true
		warnCustomRegistry(cmd, opts)
	}
	return nil
}

// unsuppressibleWarner returns a UI that writes to stderr regardless of
// --quiet / --json. Reserved for warnings whose whole value is that they
// cannot be lost: a scripted pipeline that never sees them would deploy
// against unverified images or unchosen settings with no record either way.
// Colour and spinners stay off so a script parsing stderr still reads them.
func unsuppressibleWarner(cmd *cobra.Command, opts *GlobalOpts) *ui.UI {
	warnOpts := opts.UIOptions()
	warnOpts.Quiet = false
	warnOpts.JSON = false
	return ui.NewUIWithOptions(cmd.ErrOrStderr(), warnOpts)
}

// warnCustomRegistry records that image verification has been turned off.
// The operator opted into a custom registry by setting the override, and
// the trust model demands a durable audit trail of the consequence.
func warnCustomRegistry(cmd *cobra.Command, opts *GlobalOpts) {
	unsuppressibleWarner(cmd, opts).Warn(
		"Custom registry detected (registry_host/image_repo_prefix/dhi_registry/" +
			"postgres_image_tag/nats_image_tag differs from default). Image signature " +
			"and SLSA provenance verification are DISABLED -- you are responsible for " +
			"the trust of this deployment. Unset the override or run " +
			"'synthorg config unset <key>' to restore verification.")
}

// warnCoercedConfigFields reports every persisted setting this binary
// could not use as written and replaced at load time (see config.Coerce).
//
// Ignores --quiet / --json for the same reason the custom-registry warning
// does: the stack is running with a value the operator did not choose, and
// a scripted pipeline that never sees that would silently deploy against
// the wrong setting. The warning stops on its own once any command
// persists state, because Save writes the coerced value through.
func warnCoercedConfigFields(cmd *cobra.Command, opts *GlobalOpts, state config.State) {
	if len(state.Coerced) == 0 {
		return
	}
	// "could not use as written" rather than "does not recognise": a
	// coercion also covers a setting left empty that has no usable empty
	// form, and calling that unrecognised would read as a CLI bug.
	warnOut := unsuppressibleWarner(cmd, opts)
	warnOut.Warn(fmt.Sprintf(
		"%s holds settings this version could not use as written; the substitutions "+
			"below are in effect. Run 'synthorg config set <key> <value>' to choose "+
			"explicitly.",
		config.StatePath(state.DataDir),
	))
	for _, c := range state.Coerced {
		warnOut.Warn("  " + c.String())
	}
}

// resolveEnvOverrides merges environment variable overrides with flag values.
// Use flag variables directly (already populated by Cobra) rather than
// cmd.Flags().Changed() which only sees local flags on subcommands.
func resolveEnvOverrides() (noColor, quiet, yes, skipVerify bool) {
	noColor = flagNoColor
	if !flagNoColor && noColorFromEnv() {
		noColor = true
	}
	quiet = flagQuiet
	if !flagQuiet && envBool(EnvQuiet) {
		quiet = true
	}
	yes = flagYes
	if !flagYes && envBool(EnvYes) {
		yes = true
	}
	skipVerify = flagSkipVerify
	if !flagSkipVerify && envBool(EnvSkipVerify) {
		skipVerify = true
	}
	return
}

// applyConfigOverrides loads persisted config and applies display preferences.
// Only applies when neither a flag nor an env var already set the value,
// preserving flag > env > config > default precedence.
func applyConfigOverrides(opts *GlobalOpts) {
	state, loadErr := config.Load(opts.DataDir)
	if loadErr != nil {
		return
	}
	if state.Hints != "" {
		opts.Hints = state.Hints
	}
	applyColorOverride(opts, state.Color)
	// Persisted `output=json` is honoured only when the operator did
	// not request --plain (or set its env equivalent). --plain implies
	// "ASCII-only, no machine output"; silently upgrading to JSON
	// because of stale state would defeat the explicit user choice.
	if !flagJSON && !opts.JSON && !flagPlain && !opts.Plain && state.Output == "json" {
		opts.JSON = true
	}
}

// applyColorOverride applies the persisted color preference, respecting
// the flag > env > config precedence. A flag or env value already
// forcing no-color preempts the config; otherwise "never" forces
// no-color on and "always" forces it off.
func applyColorOverride(opts *GlobalOpts, color string) {
	if flagNoColor || opts.NoColor {
		// Flag or env already set; config "always" must not override.
		return
	}
	switch color {
	case "never":
		opts.NoColor = true
	case "always":
		opts.NoColor = false
	}
}

// resolveDataDir returns the effective data directory, using the flag value,
// env var, or the platform default. The result is normalized to an absolute
// path and symlinks are resolved to prevent traversal.
func resolveDataDir() string {
	dir := flagDataDir
	if dir == "" {
		dir = os.Getenv(EnvDataDir)
	}
	if dir == "" {
		dir = config.DataDir()
	}
	// Normalize to absolute path before any filesystem use.
	if abs, err := filepath.Abs(dir); err == nil {
		dir = abs
	}
	// Resolve symlinks to prevent traversal.
	if resolved, err := filepath.EvalSymlinks(dir); err == nil {
		return resolved
	}
	return dir
}

// safeStateDir returns a validated absolute path from the loaded state's DataDir.
// This satisfies CodeQL's go/path-injection by applying SecurePath at the call site.
func safeStateDir(state config.State) (string, error) {
	return config.SecurePath(state.DataDir)
}

// isInteractive returns true if stdin is a terminal (not piped or in CI).
// Prefer GlobalOpts.ShouldPrompt() which additionally respects --yes.
// This function is retained for destructive commands (wipe, uninstall) where
// the --yes flag and TTY check must be evaluated separately.
func isInteractive() bool {
	fi, err := os.Stdin.Stat()
	if err != nil {
		return false
	}
	return fi.Mode()&os.ModeCharDevice != 0
}

// isTransportError returns true when err is caused by a network/transport
// problem (DNS failure, connection refused, timeout) rather than a
// cryptographic verification failure. Used to conditionally suggest
// --skip-verify only when the issue is connectivity, not a tampered image.
func isTransportError(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	if _, ok := errors.AsType[*net.OpError](err); ok {
		return true
	}
	if _, ok := errors.AsType[*net.DNSError](err); ok {
		return true
	}
	// Check for net.Error interface (covers timeout errors from HTTP clients).
	var netIface net.Error
	if errors.As(err, &netIface) && netIface.Timeout() {
		return true
	}
	return false
}

// Execute runs the root command under a context that a SIGINT/SIGTERM
// cancels, so a long-running step already threading cmd.Context() through
// (an image pull, a network verification call) unwinds through its own
// error-handling path instead of the process dying mid-write.
//
// update's pullAndPersist depends on this: it writes compose.yml with the
// new image pins before the pull runs (docker compose pull reads the image
// refs it pulls from that file), and only persists config.json after the
// pull succeeds -- rolling compose.yml back to its prior contents on any
// error, cancellation included. Without a caught signal, Ctrl+C during a
// pull killed the process before that rollback ever ran, leaving
// compose.yml ahead of config.json; the next `update` then regenerated
// compose from the stale config and proposed reverting the file it had
// already correctly written.
//
// A second interrupt force-exits: if a step is wedged (a stuck Docker
// daemon, a network call whose own timeout has not yet tripped), the
// operator needs an escape that does not depend on that step unwinding.
func Execute() error {
	// Registered before NotifyContext so no signal delivered between the two
	// listeners coming up can be missed -- see forceExitOnSecondInterrupt.
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(c)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go forceExitOnSecondInterrupt(ctx, c)

	err := rootCmd.ExecuteContext(ctx)
	return reportExecuteError(ctx, rootCmd.ErrOrStderr(), err)
}

// reportExecuteError turns rootCmd.ExecuteContext's result into what
// Execute returns, split out from Execute so the ctx-cancelled branch is
// testable without standing up a real signal delivery.
func reportExecuteError(ctx context.Context, errOut io.Writer, err error) error {
	if err == nil {
		return nil
	}
	if ctx.Err() != nil {
		// The command was cancelled by our own signal handler: report the
		// operator's own Ctrl+C plainly rather than whatever raw error the
		// interrupted subprocess happened to return (e.g. "signal: killed"),
		// which reads as a failure rather than the cancellation it is.
		//
		// Deliberately does not claim any specific outcome (e.g. "compose.yml
		// was rolled back"): this handler runs for every command, has no
		// knowledge of what the interrupted command was doing, and a command
		// whose own rollback failed, was a no-op, or does not touch
		// compose/config at all (e.g. `status --watch`) would make that claim
		// false. A command with a durable side effect to report on interrupt
		// (update's compose.yml rollback) does so from its own code, where it
		// actually knows the outcome.
		//
		// WarnAlways, not Warn: this is the one line telling a --quiet/--json
		// scripted invocation why it exited ExitInterrupted instead of
		// completing, so it must survive --quiet the same way
		// unsuppressibleWarner's callers do.
		errUI := ui.NewUIWithOptions(errOut, globalUIOptions())
		errUI.WarnAlways("Interrupted.")
		return NewExitError(ExitInterrupted, err)
	}
	// ChildExitError / ExitError: main.go handles exit code propagation;
	// their internal messages are not user-facing.
	_, isChildExitErr := errors.AsType[*ChildExitError](err)
	_, isExitErr := errors.AsType[*ExitError](err)
	if isChildExitErr || isExitErr {
		return err
	}
	_, _ = fmt.Fprintln(errOut, err)
	if hint := errorHint(err); hint != "" {
		errUI := ui.NewUIWithOptions(errOut, globalUIOptions())
		errUI.HintError(hint)
	}
	return err
}

// armSecondSignal blocks until ctx is cancelled by the first signal (see
// Execute), then waits for a genuinely second SIGINT/SIGTERM on c. c is
// registered by Execute before NotifyContext's own listener comes up, so no
// gap exists between "not yet listening for a second signal" and "listening
// for one" -- the gap forceExitOnSecondInterrupt used to have by creating
// and registering its own channel only after ctx.Done() fired, during which
// a second signal was delivered to NotifyContext's already-spent internal
// channel and silently dropped.
//
// Go's signal package fans a delivered signal out to every channel
// registered via signal.Notify in one synchronous step before any consumer
// goroutine observes it, so by the time ctx.Done() unblocks here, c already
// holds a copy of that same first signal; that copy is drained before
// waiting for the next one.
func armSecondSignal(ctx context.Context, c chan os.Signal) {
	<-ctx.Done()
	<-c
	<-c
}

// forceExitOnSecondInterrupt arms the second-signal listener (see
// armSecondSignal) and exits immediately once it fires. A second
// SIGINT/SIGTERM after the first means the graceful shutdown itself is
// stuck, so it exits immediately rather than leaving the operator with no
// way out short of killing the process externally.
//
// Assumes Execute is called at most once per process (true today: only
// main.go calls it). A second concurrent Execute call would leave this
// goroutine running for the life of the process past the first call's own
// return, since nothing here ever cancels it independently of ctx.
func forceExitOnSecondInterrupt(ctx context.Context, c chan os.Signal) {
	armSecondSignal(ctx, c)
	os.Exit(ExitInterrupted)
}

// printAllHelp recursively prints help for all available commands.
func printAllHelp(cmd *cobra.Command, depth int) {
	out := cmd.OutOrStdout()
	if depth > 0 {
		_, _ = fmt.Fprintf(out, "\n%s\n\n", strings.Repeat("=", 60))
	}
	_ = cmd.Help()
	for _, sub := range cmd.Commands() {
		if !sub.IsAvailableCommand() || sub.IsAdditionalHelpTopicCommand() {
			continue
		}
		printAllHelp(sub, depth+1)
	}
}

// globalUIOptions returns ui.Options derived from flag variables for use
// in the error path of Execute(), where GlobalOpts may not be in context.
func globalUIOptions() ui.Options {
	return ui.Options{
		Quiet:   flagQuiet || flagJSON,
		NoColor: flagNoColor || noColorFromEnv(),
		Plain:   flagPlain,
		JSON:    flagJSON,
		Hints:   "auto",
	}
}

// errorHintRule maps an error-message substring (or any-of-many
// substrings) to a contextual hint.
type errorHintRule struct {
	substrings []string
	hint       string
	guard      func(error) bool
}

var errorHintRules = []errorHintRule{
	{substrings: []string{"connection refused", "backend unreachable"}, hint: "Is Docker running? Try 'synthorg doctor' for diagnostics."},
	{substrings: []string{"compose.yml not found"}, hint: "Run 'synthorg init' to set up your installation."},
	{substrings: []string{"loading config"}, hint: "Run 'synthorg init' to create a configuration."},
	{substrings: []string{"permission denied"}, hint: "Check file permissions on the data directory."},
	{substrings: []string{"image verification failed"}, hint: "Try --skip-verify for air-gapped environments.", guard: isTransportError},
	// Init-specific must precede the generic "requires an interactive
	// terminal" rule: init does NOT accept --yes for full automation
	// (it needs explicit flags), so the generic "Use --yes" hint is
	// misleading. The init error already lists the four required
	// flags; this hint surfaces the optional ones operators commonly
	// want when scripting an install.
	{substrings: []string{"synthorg init requires"}, hint: "Optional init flags: --image-tag, --channel, --bus-backend, --persistence-backend, --postgres-port, --encrypt-secrets."},
	{substrings: []string{"requires an interactive terminal"}, hint: "Use --yes for non-interactive mode."},
	{substrings: []string{"Docker not available", "docker: not found", "Cannot connect to the Docker daemon"}, hint: "Ensure Docker is installed and running."},
}

// errorHint returns a contextual suggestion for common error patterns.
// Returns "" if no hint is applicable.
func errorHint(err error) string {
	msg := err.Error()
	for _, rule := range errorHintRules {
		if !messageMatches(msg, rule.substrings) {
			continue
		}
		if rule.guard != nil && !rule.guard(err) {
			continue
		}
		return rule.hint
	}
	return ""
}

// messageMatches reports whether msg contains any of the given
// substrings.
func messageMatches(msg string, substrings []string) bool {
	for _, s := range substrings {
		if strings.Contains(msg, s) {
			return true
		}
	}
	return false
}
