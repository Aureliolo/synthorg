package cmd

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/huh/v2"
	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
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
	Example: `  synthorg init                                         # interactive setup wizard
  synthorg init --backend-port 3001 --web-port 3000 --sandbox true  # non-interactive`,
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
		state.NatsClientPort = result.natsPort
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
	hintAfterInit(out, state)

	if result.startNow {
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
	hintAfterInit(out, state)
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
		d.busPort = strconv.Itoa(state.NatsClientPort)
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
func hintAfterInit(out *ui.UI, state config.State) {
	if state.Channel == "dev" {
		out.HintTip("Dev channel receives frequent pre-release updates. Run 'synthorg config set channel stable' to switch.")
	}
	out.HintGuidance("Customize settings later with 'synthorg config set <key> <value>'. Run 'synthorg config list' to see all options.")
}

// handleReinit loads the existing config, confirms overwrite (interactive
// or --yes), and preserves the settings key in state. Returns false if
// declined.
func handleReinit(cmd *cobra.Command, state *config.State, opts *GlobalOpts) (bool, error) {
	oldState, loadErr := config.Load(state.DataDir)
	if errors.Is(loadErr, config.ErrMissingMasterKey) {
		// Recovery path: encrypt_secrets is on but no master_key was
		// ever generated on disk. Re-read via the permissive variant so
		// reinit can carry forward the rest of the state; the new key
		// (already generated on `state`) is preserved through the
		// normal reinit-Yes / reinit-Interactive flows below.
		oldState, loadErr = config.LoadAllowMissingMasterKey(state.DataDir)
	}
	if loadErr != nil {
		return false, fmt.Errorf("existing config at %s is unreadable: %w (delete it manually to force a fresh init)",
			config.StatePath(state.DataDir), loadErr)
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
	kept, err := confirmReinit(cmd, oldState, opts)
	if err != nil {
		return false, err
	}
	if kept == nil {
		return false, nil
	}
	if *kept != "" {
		state.SettingsKey = *kept
	}
	// Preserve the secret-storage master key so existing ciphertext
	// stays decryptable after re-init. Regenerating would orphan every
	// stored connection secret.
	if oldState.MasterKey != "" {
		state.MasterKey = oldState.MasterKey
	}
	// Preserve the pagination cursor secret. Rotating it invalidates
	// every outstanding cursor token across every restart (same hazard
	// as MasterKey).
	if oldState.CursorSecret != "" {
		state.CursorSecret = oldState.CursorSecret
	}
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
	if oldState.PostgresPassword != "" {
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

// confirmReinit prompts the user to confirm overwriting existing config.
// Returns a pointer to the existing settings key to preserve, or nil if the
// user declined. An empty string means no key existed to preserve.
func confirmReinit(cmd *cobra.Command, oldState config.State, opts *GlobalOpts) (*string, error) {
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
		return nil, err
	}
	if !proceed {
		return nil, nil
	}
	key := oldState.SettingsKey
	return &key, nil
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

// setupDockerSockConfig validates the host Docker socket path and captures
// the owning GID so the compose template can render “group_add“ for the
// backend. Returns “-1“ for the GID when detection is not applicable
// (Windows named pipe, socket missing).
func setupDockerSockConfig(sandbox bool, dockerSock string) (string, int, error) {
	sock := strings.TrimSpace(dockerSock)
	if !sandbox {
		return sock, -1, nil
	}
	if err := validateDockerSock(sock); err != nil {
		return "", -1, err
	}
	gid := -1
	if detected, ok := config.DetectDockerSockGID(sock); ok {
		gid = detected
	}
	return sock, gid, nil
}

// setupPostgresConfig validates Postgres port collisions and generates
// a random password when the selected backend is “postgres“. Returns
// “(0, "", nil)“ for non-postgres backends so the State zero values
// are serialized cleanly.
func setupPostgresConfig(a setupAnswers, backendPort, webPort int) (int, string, error) {
	if a.persistenceBackend != "postgres" {
		return 0, "", nil
	}
	port := a.postgresPort
	if port == 0 {
		port = config.DefaultState().PostgresPort
	}
	// Validate the RESOLVED port against backend/web ports. The CLI-flag
	// check in validateInitFlags only fires when --postgres-port is
	// explicit; the default 3002 can still collide if the user set
	// --backend-port 3002 (or similar).
	if port == backendPort {
		return 0, "", fmt.Errorf(
			"postgres port %d conflicts with backend port %d", port, backendPort,
		)
	}
	if port == webPort {
		return 0, "", fmt.Errorf(
			"postgres port %d conflicts with web port %d", port, webPort,
		)
	}
	pw, err := compose.GeneratePassword(32)
	if err != nil {
		return 0, "", fmt.Errorf("generating postgres password: %w", err)
	}
	return port, pw, nil
}

func buildState(a setupAnswers) (config.State, error) {
	dir := strings.TrimSpace(a.dir)
	if !filepath.IsAbs(dir) {
		return config.State{}, fmt.Errorf("data directory must be an absolute path, got %q", dir)
	}

	backendPort, err := parsePort(a.backendPortStr, "backend")
	if err != nil {
		return config.State{}, err
	}
	webPort, err := parsePort(a.webPortStr, "web")
	if err != nil {
		return config.State{}, err
	}

	dockerSock, dockerSockGID, err := setupDockerSockConfig(a.sandbox, a.dockerSock)
	if err != nil {
		return config.State{}, err
	}

	jwtSecret, settingsKey, masterKey, cursorSecret, err := generateInitSecrets()
	if err != nil {
		return config.State{}, err
	}

	imageTag := resolveImageTag(a.imageTag)
	channel := "stable"
	if a.channel != "" {
		channel = a.channel
	}

	busBackend := a.busBackend
	if busBackend == "" {
		busBackend = "internal"
	}

	postgresPort, postgresPassword, err := setupPostgresConfig(a, backendPort, webPort)
	if err != nil {
		return config.State{}, err
	}

	return config.State{
		DataDir:            dir,
		ImageTag:           imageTag,
		Channel:            channel,
		BackendPort:        backendPort,
		WebPort:            webPort,
		Sandbox:            a.sandbox,
		DockerSock:         dockerSock,
		DockerSockGID:      dockerSockGID,
		LogLevel:           a.logLevel,
		JWTSecret:          jwtSecret,
		SettingsKey:        settingsKey,
		MasterKey:          masterKey,
		CursorSecret:       cursorSecret,
		EncryptSecrets:     a.encryptSecrets,
		PersistenceBackend: a.persistenceBackend,
		MemoryBackend:      a.memoryBackend,
		BusBackend:         busBackend,
		NatsClientPort:     config.DefaultState().NatsClientPort,
		PostgresPort:       postgresPort,
		PostgresPassword:   postgresPassword,
		TelemetryOptIn:     a.telemetryOptIn,
		FineTuning:         a.fineTuning,
		FineTuningVariant:  a.fineTuneVariant,
	}, nil
}

// writeInitFiles creates the data directory, generates compose.yml, and saves
// config. Returns the sanitized data directory path.
func writeInitFiles(state config.State) (string, error) {
	safeDir, err := config.SecurePath(state.DataDir)
	if err != nil {
		return "", err
	}
	state.DataDir = safeDir // normalize before persisting
	if err := os.MkdirAll(safeDir, 0o700); err != nil {
		return "", fmt.Errorf("creating data directory: %w", err)
	}

	params, err := compose.ParamsFromState(state)
	if err != nil {
		return "", fmt.Errorf("building compose params: %w", err)
	}
	composeYAML, err := compose.Generate(params)
	if err != nil {
		return "", fmt.Errorf("generating compose file: %w", err)
	}

	if err := compose.WriteComposeAndNATS("compose.yml", composeYAML, state.BusBackend, safeDir); err != nil {
		return "", fmt.Errorf("writing compose files: %w", err)
	}

	if err := config.Save(state); err != nil {
		return "", fmt.Errorf("saving config: %w", err)
	}
	return safeDir, nil
}

// resolveImageTag returns the image tag to use: the override if set,
// the CLI version, or "latest" for dev builds.
func resolveImageTag(override string) string {
	if override != "" {
		return override
	}
	if v := version.Version; v != "" && v != "dev" {
		return v
	}
	return "latest"
}

// generateInitSecrets creates the JWT, settings encryption, secret-storage
// master, and pagination cursor signing keys. The settings key and master
// key are 32 bytes (44-char URL-safe base64) each, matching the format
// required by Python cryptography.fernet.Fernet. Do NOT change byte counts.
// The cursor secret is 32 bytes (well above the backend's 16-byte minimum)
// so the boot guard accepts it unconditionally on every channel.
func generateInitSecrets() (jwtSecret, settingsKey, masterKey, cursorSecret string, err error) {
	jwtSecret, err = generateSecret(48)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating JWT secret: %w", err)
	}
	settingsKey, err = generateSecret(32)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating settings encryption key: %w", err)
	}
	masterKey, err = generateSecret(32)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating secret master key: %w", err)
	}
	cursorSecret, err = generateSecret(32)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating pagination cursor secret: %w", err)
	}
	return jwtSecret, settingsKey, masterKey, cursorSecret, nil
}

func validateDockerSock(path string) error {
	if !filepath.IsAbs(path) && !strings.HasPrefix(path, "//") {
		return fmt.Errorf("docker socket must be an absolute path, got %q", path)
	}
	if strings.ContainsAny(path, "\"'`$\n\r{}[]") {
		return fmt.Errorf("docker socket path %q contains unsafe characters", path)
	}
	return nil
}

func defaultDockerSock() string {
	if runtime.GOOS == "windows" {
		return "//./pipe/docker_engine"
	}
	return "/var/run/docker.sock"
}

func generateSecret(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.URLEncoding.EncodeToString(b), nil
}

// fileExists reports whether the given path exists on disk.
// The path must be absolute; relative paths are treated as non-existent.
func fileExists(path string) bool {
	safe, err := config.SecurePath(path)
	if err != nil {
		return false
	}
	_, err = os.Stat(safe)
	return err == nil
}

func parsePort(s, name string) (int, error) {
	s = strings.TrimSpace(s)
	n, err := strconv.Atoi(s)
	if err != nil || n < 1 || n > 65535 {
		return 0, fmt.Errorf("invalid %s port: %q (must be 1-65535)", name, s)
	}
	return n, nil
}
