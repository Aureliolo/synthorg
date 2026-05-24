// Package cmd defines the CLI commands for SynthOrg.
package cmd

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
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
	Short: "SynthOrg CLI -- manage your synthetic organization",
	Long: `SynthOrg CLI manages the lifecycle of your synthetic organization.

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

	// Note: SYNTHORG_SKIP_VERIFY / SYNTHORG_NO_VERIFY env vars are resolved
	// inside setupGlobalOpts alongside all other env var overrides.
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

	if err := applyTunables(cmd, opts); err != nil {
		// Recovery commands must stay callable even when config.json is
		// unreadable or fails validation -- otherwise the user has no
		// way to repair a broken config without hand-editing the file
		// they would normally fix via the CLI.
		if isRecoveryCommand(cmd) {
			_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
				"Warning: tunable resolution failed (%v); continuing with defaults for recovery command.\n", err)
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

	if tun.CustomRegistry {
		opts.SkipVerify = true
		// Safety-critical warnings ignore --quiet / --json. The operator
		// opted into a custom registry by setting the override, and the
		// trust model demands a durable audit trail: a silent skip here
		// would let a scripted pipeline pull unsigned images without any
		// record. Write the warning with minimal UI options (no colour,
		// no spinners) so scripts that parse stderr still see it.
		warnOpts := opts.UIOptions()
		warnOpts.Quiet = false
		warnOpts.JSON = false
		warnOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), warnOpts)
		warnOut.Warn(
			"Custom registry detected (registry_host/image_repo_prefix/dhi_registry/" +
				"postgres_image_tag/nats_image_tag differs from default). Image signature " +
				"and SLSA provenance verification are DISABLED -- you are responsible for " +
				"the trust of this deployment. Unset the override or run " +
				"'synthorg config unset <key>' to restore verification.")
	}
	return nil
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
	if !flagSkipVerify && (envBool(EnvNoVerify) || envBool(EnvSkipVerify)) {
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
	var netErr *net.OpError
	if errors.As(err, &netErr) {
		return true
	}
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		return true
	}
	// Check for net.Error interface (covers timeout errors from HTTP clients).
	var netIface net.Error
	if errors.As(err, &netIface) && netIface.Timeout() {
		return true
	}
	return false
}

// Execute runs the root command.
func Execute() error {
	err := rootCmd.Execute()
	if err == nil {
		return nil
	}
	// ChildExitError / ExitError: main.go handles exit code propagation;
	// their internal messages are not user-facing.
	var ce *ChildExitError
	var ee *ExitError
	if errors.As(err, &ce) || errors.As(err, &ee) {
		return err
	}
	_, _ = fmt.Fprintln(rootCmd.ErrOrStderr(), err)
	if hint := errorHint(err); hint != "" {
		errUI := ui.NewUIWithOptions(rootCmd.ErrOrStderr(), globalUIOptions())
		errUI.HintError(hint)
	}
	return err
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
