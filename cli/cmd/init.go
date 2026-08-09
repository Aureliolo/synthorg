package cmd

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var (
	initBackendPort        int
	initWebPort            int
	initSandbox            string
	initImageTag           string
	initChannel            string
	initLogLevel           string
	initBusBackend         string
	initPersistenceBackend string
	initPostgresPort       int
	initEncryptSecrets     string // "", "true", "false" ("" = use default true)
)

var initCmd = &cobra.Command{
	Use:   "init",
	Short: "Interactive setup wizard for SynthOrg",
	Long: `Creates a data directory, generates a Docker Compose file, and optionally pulls images.

When all required flags are provided, the interactive wizard is skipped
(useful for CI/automation).`,
	Example: `  synthorg init                                                                                # interactive setup wizard
  synthorg init --backend-port 3001 --web-port 3000 --sandbox true --log-level info            # non-interactive`,
	RunE: runInit,
}

func init() {
	initCmd.Flags().IntVar(&initBackendPort, "backend-port", 0, "backend API port (1-65535)")
	initCmd.Flags().IntVar(&initWebPort, "web-port", 0, "web dashboard port (1-65535)")
	initCmd.Flags().StringVar(&initSandbox, "sandbox", "", "enable agent sandbox (\"true\" or \"false\")")
	initCmd.Flags().StringVar(&initImageTag, "image-tag", "", "container image tag")
	initCmd.Flags().StringVar(&initChannel, "channel", "", "update channel (\"stable\" or \"dev\")")
	initCmd.Flags().StringVar(&initLogLevel, "log-level", "", "log level (\"debug\", \"info\", \"warn\", \"error\")")
	initCmd.Flags().StringVar(&initBusBackend, "bus-backend", "", "message bus backend (\"internal\" or \"nats\"; defaults to \"internal\")")
	initCmd.Flags().StringVar(&initPersistenceBackend, "persistence-backend", "", "persistence backend (\"sqlite\" or \"postgres\"; defaults to \"sqlite\")")
	initCmd.Flags().IntVar(&initPostgresPort, "postgres-port", 0, "postgres port when --persistence-backend=postgres (1-65535, default 3002)")
	initCmd.Flags().StringVar(&initEncryptSecrets, "encrypt-secrets", "", "encrypt connection secrets at rest (\"true\" or \"false\"; default \"true\")")
	initCmd.GroupID = "core"
	rootCmd.AddCommand(initCmd)
}

// initAllFlagsSet returns true when all required init flags are provided,
// enabling fully non-interactive setup. The --image-tag and --channel flags
// are optional (default to CLI version and "stable" respectively).
// Telemetry opt-in is intentionally interactive-only; non-interactive
// init defaults to telemetry disabled (opt-in via "config set" or env var).
func initAllFlagsSet() bool {
	return initBackendPort > 0 && initWebPort > 0 && initSandbox != "" &&
		initLogLevel != ""
}

func runInit(cmd *cobra.Command, _ []string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	if err := validateInitFlags(opts.DataDir); err != nil {
		return fmt.Errorf("validating init flags: %w", err)
	}
	switch {
	case initAllFlagsSet():
		// Non-interactive: all required flags provided.
		answers := buildAnswersFromFlags(opts.DataDir)
		return runInitNonInteractive(cmd, out, answers, opts)
	case isInteractive():
		return runInitInteractive(cmd, out)
	default:
		return fmt.Errorf("synthorg init requires an interactive terminal (or provide all flags: --backend-port, --web-port, --sandbox, --log-level)")
	}
}

func runInitInteractive(cmd *cobra.Command, out *ui.UI) error {
	opts := GetGlobalOpts(cmd.Context())
	result, err := runInteractiveInit(cmd, opts)
	if err != nil {
		return fmt.Errorf("running interactive setup: %w", err)
	}
	if result == nil {
		return nil // user cancelled
	}

	state, err := buildState(result.answers)
	if err != nil {
		return fmt.Errorf("building state from TUI: %w", err)
	}
	if result.natsPort > 0 {
		state.NATSClientPort = result.natsPort
	}

	if err := reuseExistingStateForInteractive(cmd, &state, result); err != nil {
		return err
	}

	safeDir, err := writeInitFiles(state)
	if err != nil {
		return fmt.Errorf("writing init files: %w", err)
	}
	state.DataDir = safeDir

	out.Logo(version.Version)
	out.Success("SynthOrg initialized")
	out.Blank()
	out.Box("Configuration", summaryLines(buildSummaryFromState(state)))
	out.Blank()
	out.Warn("Keep compose.yml and config.json private -- they contain your secrets.")
	hintAfterInit(out, state, result.answers.imageTag)

	if result.startNow {
		// Pre-flight Docker reachability before re-exec'ing start. Without
		// this, an unreachable daemon surfaces as a start failure printed
		// after the "initialized" banner and config summary, implying the
		// auto-start was expected to succeed. Probing here lets us degrade to
		// a clear "run start once Docker is running" hint while still leaving
		// init reported as successful.
		if _, derr := docker.Detect(cmd.Context()); derr != nil {
			out.Blank()
			out.Warn(fmt.Sprintf("Docker is not available, so the stack was not started: %v", derr))
			out.Section("Next: start Docker, then run 'synthorg start'")
			return nil
		}
		out.Blank()
		_ = os.Setenv("SYNTHORG_NO_LOGO", "1")
		cmd.Root().SetArgs([]string{"start"})
		return cmd.Root().Execute()
	}
	out.Blank()
	out.Section("Next: synthorg start")
	return nil
}

func runInitNonInteractive(cmd *cobra.Command, out *ui.UI, answers setupAnswers, opts *GlobalOpts) error {
	state, err := buildState(answers)
	if err != nil {
		return fmt.Errorf("building state from flags: %w", err)
	}

	if existing := config.StatePath(state.DataDir); fileExists(existing) {
		proceed, err := handleReinit(cmd, &state, opts)
		if err != nil {
			return fmt.Errorf("handling re-init: %w", err)
		}
		if !proceed {
			return nil
		}
	}

	safeDir, err := writeInitFiles(state)
	if err != nil {
		return fmt.Errorf("writing init files: %w", err)
	}
	state.DataDir = safeDir

	out.Blank()
	out.Success("SynthOrg initialized")
	out.Blank()
	out.Box("Configuration", summaryLines(buildSummaryFromState(state)))
	out.Blank()
	out.Warn("Keep compose.yml and config.json private -- they contain your secrets.")
	hintAfterInit(out, state, answers.imageTag)
	out.Blank()
	out.Section("Next: synthorg start")
	return nil
}

// buildSummaryFromState creates a summaryData from a config.State for
// the non-interactive output path (shares rendering with the TUI).
func buildSummaryFromState(state config.State) summaryData {
	d := summaryData{
		dataDir:     state.DataDir,
		backendPort: strconv.Itoa(state.BackendPort),
		webPort:     strconv.Itoa(state.WebPort),
	}
	if state.PersistenceBackend == "postgres" {
		d.dbMode = "postgresql"
		d.dbPort = strconv.Itoa(state.PostgresPort)
	} else {
		d.dbMode = "sqlite"
	}
	if state.BusBackend == "nats" {
		d.busMode = "nats"
		d.busPort = strconv.Itoa(state.NATSClientPort)
	} else {
		d.busMode = "internal"
	}
	if state.FineTuning {
		d.fineTuning = "enabled (" + state.FineTuneVariantOrDefault() + ")"
	} else {
		d.fineTuning = "disabled"
	}
	if state.Sandbox {
		d.sandbox = "enabled"
	} else {
		d.sandbox = "disabled"
	}
	if state.TelemetryOptIn {
		d.telemetry = "enabled"
	} else {
		d.telemetry = "disabled"
	}
	return d
}

// hintAfterInit emits contextual guidance after a successful init.
//
// imageTagOverride is what --image-tag carried, empty when the operator
// gave none. The tag's value alone cannot stand in for it: `--image-tag
// dev` on a released binary produces the same string as the source-build
// fallback, and only one of those is worth explaining.
func hintAfterInit(out *ui.UI, state config.State, imageTagOverride string) {
	if state.Channel == "dev" {
		out.HintTip("Dev channel receives frequent pre-release updates. Run 'synthorg config set channel stable' to switch.")
	}
	// A source build has no matching release, so it pins the newest
	// prerelease. Say which tag was chosen: an operator who assumed a
	// release build would otherwise not know the images track main.
	if imageTagOverride == "" && state.ImageTag == config.SourceBuildImageTag {
		out.HintTip(fmt.Sprintf(
			"Images pinned to the %q tag (newest pre-release), because this "+
				"binary was built from source. Run 'synthorg config set "+
				"image_tag <version>' to pin a release.",
			config.SourceBuildImageTag,
		))
	}
	out.HintGuidance("Customize settings later with 'synthorg config set <key> <value>'. Run 'synthorg config list' to see all options.")
}

// handleReinit loads the existing config, confirms overwrite (interactive
// or --yes), and preserves the settings key in state. Returns false if
// declined.
func handleReinit(cmd *cobra.Command, state *config.State, opts *GlobalOpts) (bool, error) {
	oldState, loadErr := config.LoadForReinit(state.DataDir)
	if loadErr != nil {
		return false, unreadableExistingConfigError(config.StatePath(state.DataDir), loadErr)
	}
	if opts.Yes {
		return applyReinitYes(cmd, state, oldState)
	}
	if !isInteractive() {
		return false, fmt.Errorf("existing config found at %s; pass --yes to overwrite",
			config.StatePath(state.DataDir))
	}
	return applyReinitInteractive(cmd, state, oldState, opts)
}

// applyReinitYes is the --yes path: silently preserve secrets +
// Postgres settings and proceed.
func applyReinitYes(cmd *cobra.Command, state *config.State, oldState config.State) (bool, error) {
	copyPreservedSecrets(state, oldState)
	if err := preservePostgresFromOldState(cmd, state, oldState); err != nil {
		return false, err
	}
	return true, nil
}

// applyReinitInteractive is the prompt path: ask the user whether to
// keep the existing settings key, then preserve master key + cursor
// secret + Postgres settings.
func applyReinitInteractive(cmd *cobra.Command, state *config.State, oldState config.State, opts *GlobalOpts) (bool, error) {
	proceed, err := confirmReinit(cmd, oldState, opts)
	if err != nil || !proceed {
		return false, err
	}
	// Secrets carry forward on exactly the same terms as the --yes path,
	// so route them through the one helper rather than restating the
	// rules here and letting the two drift.
	copyPreservedSecrets(state, oldState)
	if err := preservePostgresFromOldState(cmd, state, oldState); err != nil {
		return false, err
	}
	return true, nil
}

// preservePostgresFromOldState carries forward Postgres password and port
// across a re-init. The decision is gated on the PERSISTED backend, not the
// new state's backend, so that omitting --persistence-backend on an existing
// Postgres deployment keeps the old settings. Explicit flags always win:
//
//   - If the user passed --persistence-backend with a non-postgres value,
//     the new backend takes effect and Postgres fields are cleared.
//   - If the user did not pass --persistence-backend, the new state inherits
//     the persisted backend (and its Postgres settings) when the old config
//     was already Postgres.
//   - --postgres-port is always honored when explicitly set, otherwise the
//     persisted port is carried over.
func preservePostgresFromOldState(
	cmd *cobra.Command,
	state *config.State,
	oldState config.State,
) error {
	backendFlagSet := cmd.Flags().Changed("persistence-backend")
	// If the user didn't change the backend and the old one was postgres,
	// inherit the old backend so the rest of the block applies.
	if !backendFlagSet && oldState.PersistenceBackend == "postgres" {
		state.PersistenceBackend = "postgres"
	}
	if state.PersistenceBackend != "postgres" {
		// Not a postgres deployment (either user switched away, or this
		// install was never postgres) -- clear any leaked postgres fields.
		state.PostgresPassword = ""
		state.PostgresPort = 0
		return nil
	}
	if strings.TrimSpace(oldState.PostgresPassword) != "" {
		state.PostgresPassword = oldState.PostgresPassword
	}
	if oldState.PostgresPort != 0 && !cmd.Flags().Changed("postgres-port") {
		state.PostgresPort = oldState.PostgresPort
	}
	// Re-validate the (possibly preserved) port against the new backend/web
	// ports: re-init can introduce a conflict if the user changed
	// --backend-port or --web-port to collide with the persisted postgres
	// port.
	if state.PostgresPort == state.BackendPort {
		return fmt.Errorf(
			"postgres port %d (from existing config) conflicts with backend port %d",
			state.PostgresPort, state.BackendPort,
		)
	}
	if state.PostgresPort == state.WebPort {
		return fmt.Errorf(
			"postgres port %d (from existing config) conflicts with web port %d",
			state.PostgresPort, state.WebPort,
		)
	}
	return nil
}

// confirmReinit prompts the user to confirm overwriting existing config,
// reporting whether they agreed. Which secrets survive is not a decision
// this prompt makes: it is a yes/no confirmation, and copyPreservedSecrets
// owns the carry-forward rules for every secret alike.
func confirmReinit(cmd *cobra.Command, oldState config.State, opts *GlobalOpts) (bool, error) {
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	errOut.Warn("Existing config at " + config.StatePath(oldState.DataDir) + " will be overwritten.")
	errOut.Warn("A new JWT secret will be generated -- running containers will need a restart.")
	if oldState.SettingsKey == "" {
		errOut.Warn("A new settings encryption key will also be generated.")
	}
	var proceed bool
	form := huh.NewForm(huh.NewGroup(
		huh.NewConfirm().Title("Overwrite existing configuration?").Value(&proceed),
	))
	if err := form.Run(); err != nil {
		return false, err
	}
	return proceed, nil
}

// setupAnswers holds raw form input before validation.
type setupAnswers struct {
	dir                string
	backendPortStr     string
	webPortStr         string
	sandbox            bool
	dockerSock         string
	logLevel           string
	persistenceBackend string
	memoryBackend      string
	busBackend         string
	postgresPort       int    // 0 = use DefaultState.PostgresPort (3002)
	channel            string // optional override (empty = default "stable")
	imageTag           string // optional override (empty = use CLI version)
	telemetryOptIn     bool
	fineTuning         bool   // enable fine-tuning pipeline (requires sandbox/Docker)
	fineTuneVariant    string // "gpu" (default) or "cpu"; ignored unless fineTuning is true
	encryptSecrets     bool   // encrypt connection secrets at rest (default true)
	reinitConfirmed    bool   // TUI reinit phase was shown and user confirmed
}

// validateInitFlags checks that provided CLI flag values are valid before
// the interactive/non-interactive branch. Only validates flags that were
// set. Per-section validators live in init_helpers.go.
func validateInitFlags(dataDir string) error {
	if err := validatePortFlags(); err != nil {
		return err
	}
	if err := validateEnumFlags(); err != nil {
		return err
	}
	return validatePostgresFlag(dataDir)
}

// buildAnswersFromFlags constructs setupAnswers from CLI flags for non-interactive mode.
func buildAnswersFromFlags(dataDir string) setupAnswers {
	defaults := config.DefaultState()
	busBackend := initBusBackend
	if busBackend == "" {
		busBackend = defaults.BusBackend
	}
	persistenceBackend := initPersistenceBackend
	if persistenceBackend == "" {
		persistenceBackend = defaults.PersistenceBackend
	}
	postgresPort := initPostgresPort
	if postgresPort == 0 {
		postgresPort = defaults.PostgresPort
	}
	sandboxEnabled := initSandbox == "true"
	// Default encryption to ON when the flag is omitted. Only an
	// explicit "false" turns encryption off.
	encryptSecrets := defaults.EncryptSecrets
	if initEncryptSecrets != "" {
		encryptSecrets = initEncryptSecrets == "true"
	}
	a := setupAnswers{
		dir:                dataDir,
		backendPortStr:     strconv.Itoa(initBackendPort),
		webPortStr:         strconv.Itoa(initWebPort),
		sandbox:            sandboxEnabled,
		dockerSock:         defaultDockerSock(),
		logLevel:           initLogLevel,
		persistenceBackend: persistenceBackend,
		memoryBackend:      defaults.MemoryBackend,
		busBackend:         busBackend,
		postgresPort:       postgresPort,
		channel:            initChannel,
		imageTag:           initImageTag,
		encryptSecrets:     encryptSecrets,
	}
	return a
}

// runSetupFormWithOverrides runs the interactive form with any CLI flag values
// pre-filled as defaults.
type interactiveResult struct {
	answers  setupAnswers
	startNow bool
	natsPort int // override for NATS port from TUI
}

// buildTUIModel assembles the TUI model with CLI flag overrides applied and
// the re-init phase configured when existing state is detected.
func buildTUIModel(opts *GlobalOpts, defaults config.State) setupTUI {
	dir := defaults.DataDir
	if opts.DataDir != "" {
		dir = opts.DataDir
	}
	backendPort := fmt.Sprintf("%d", defaults.BackendPort)
	if initBackendPort > 0 {
		backendPort = fmt.Sprintf("%d", initBackendPort)
	}
	webPort := fmt.Sprintf("%d", defaults.WebPort)
	if initWebPort > 0 {
		webPort = fmt.Sprintf("%d", initWebPort)
	}
	sandbox := defaults.Sandbox
	if initSandbox != "" {
		sandbox = initSandbox == "true"
	}
	model := newSetupTUI(dir, backendPort, webPort, version.Version, sandbox)
	applyFlagOverridesToModel(&model)
	if existing := config.StatePath(dir); fileExists(existing) {
		model.needReinit = true
		model.reinitPath = existing
		model.phase = phaseReinit
		model.focus = fReinitOverwrite
	}
	return model
}

// applyFlagOverridesToModel applies CLI flag overrides to the TUI model so
// flags like “--persistence-backend“ and “--encrypt-secrets“ are not
// silently dropped on confirmation.
func applyFlagOverridesToModel(model *setupTUI) {
	switch initBusBackend {
	case "nats":
		model.busBackend = 1
	case "internal":
		model.busBackend = 0
	}
	switch initPersistenceBackend {
	case "postgres":
		model.persistence = 1
	case "sqlite":
		model.persistence = 0
	}
	if initPostgresPort > 0 {
		model.postgresPort.SetValue(fmt.Sprintf("%d", initPostgresPort))
	}
	if initEncryptSecrets != "" {
		model.encryptSecrets = initEncryptSecrets == "true"
	}
}

// parsePortFromTUI validates an optional port entered in the TUI. Returns
// “0“ when the field is blank so callers can fall back to defaults.
func parsePortFromTUI(raw, name string) (int, error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return 0, nil
	}
	p, err := strconv.Atoi(trimmed)
	if err != nil {
		return 0, fmt.Errorf("invalid %s port %q: %w", name, trimmed, err)
	}
	if p < 1 || p > 65535 {
		return 0, fmt.Errorf("invalid %s port %d: must be 1-65535", name, p)
	}
	return p, nil
}

// buildInteractiveResult converts the finished TUI state into the
// “setupAnswers“ + follow-up fields consumed by the init driver.
func buildInteractiveResult(final setupTUI, defaults config.State) (*interactiveResult, error) {
	busBackends := []string{"internal", "nats"}
	bus := "internal"
	if final.busBackend >= 0 && final.busBackend < len(busBackends) {
		bus = busBackends[final.busBackend]
	}
	persist := "sqlite"
	if final.persistence == 1 {
		persist = "postgres"
	}
	pgPort := 0
	if persist == "postgres" {
		p, err := parsePortFromTUI(final.postgresPort.Value(), "postgres")
		if err != nil {
			return nil, err
		}
		pgPort = p
		if pgPort == 0 {
			pgPort = defaults.PostgresPort
		}
	}
	natsPort := 0
	if bus == "nats" {
		p, err := parsePortFromTUI(final.natsPort.Value(), "nats")
		if err != nil {
			return nil, err
		}
		natsPort = p
	}
	return &interactiveResult{
		answers: setupAnswers{
			dir:                final.dataDir.Value(),
			backendPortStr:     final.backendPort.Value(),
			webPortStr:         final.webPort.Value(),
			sandbox:            final.sandbox,
			dockerSock:         defaultDockerSock(),
			logLevel:           defaults.LogLevel,
			persistenceBackend: persist,
			memoryBackend:      defaults.MemoryBackend,
			busBackend:         bus,
			postgresPort:       pgPort,
			telemetryOptIn:     final.telemetry,
			fineTuning:         final.fineTuning,
			fineTuneVariant:    config.FineTuneVariantFromIndex(final.fineTuneVariant),
			encryptSecrets:     final.encryptSecrets,
			reinitConfirmed:    final.needReinit && !final.cancelled,
		},
		startNow: final.startNow,
		natsPort: natsPort,
	}, nil
}

func runInteractiveInit(_ *cobra.Command, opts *GlobalOpts) (*interactiveResult, error) {
	defaults := config.DefaultState()
	model := buildTUIModel(opts, defaults)

	result, err := tea.NewProgram(model).Run()
	if err != nil {
		return nil, fmt.Errorf("setup: %w", err)
	}
	final, ok := result.(setupTUI)
	if !ok {
		return nil, fmt.Errorf("unexpected model type from TUI: %T", result)
	}
	if final.cancelled {
		return nil, nil
	}
	return buildInteractiveResult(final, defaults)
}
