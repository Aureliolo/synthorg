package cmd

import (
	"fmt"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

// Helper extractions for init.go to keep individual functions inside the
// per-function complexity budget. Logic is unchanged; structure mirrors
// the original control flow exactly.

// reuseExistingStateForInteractive carries forward secrets / Postgres
// settings from an existing config when the interactive TUI re-inits an
// existing install. The interactive path differs from the
// non-interactive handleReinit by reading answers from the TUI (e.g.
// the reinitConfirmed flag, the user-chosen backend/port) rather than
// from CLI flags.
func reuseExistingStateForInteractive(cmd *cobra.Command, state *config.State, result *interactiveResult) error {
	existing := config.StatePath(state.DataDir)
	if !fileExists(existing) {
		return nil
	}
	if !result.answers.reinitConfirmed {
		errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), GetGlobalOpts(cmd.Context()).UIOptions())
		errOut.Warn(fmt.Sprintf("Existing configuration found at %s -- secrets will be regenerated.", existing))
	}
	oldState, loadErr := config.Load(state.DataDir)
	if loadErr != nil {
		return fmt.Errorf("existing config unreadable: %w", loadErr)
	}
	copyPreservedSecrets(state, oldState)
	return reusePostgresAcrossInteractive(cmd, state, oldState, result.answers)
}

// copyPreservedSecrets copies SettingsKey, MasterKey, and CursorSecret
// from oldState into state when present. Regenerating these would
// orphan stored ciphertext or invalidate outstanding pagination cursor
// tokens, so init always preserves them across re-init.
func copyPreservedSecrets(state *config.State, oldState config.State) {
	if oldState.SettingsKey != "" {
		state.SettingsKey = oldState.SettingsKey
	}
	if oldState.MasterKey != "" {
		state.MasterKey = oldState.MasterKey
	}
	if oldState.CursorSecret != "" {
		state.CursorSecret = oldState.CursorSecret
	}
}

// reusePostgresAcrossInteractive carries forward Postgres settings when
// the user did not switch backends or change the Postgres port via the
// TUI. When the user changed only the port, the password is preserved
// so the running container can still authenticate against persisted
// data.
func reusePostgresAcrossInteractive(cmd *cobra.Command, state *config.State, oldState config.State, answers setupAnswers) error {
	userChangedBackend := answers.persistenceBackend != oldState.PersistenceBackend
	userChangedPostgresPort := answers.persistenceBackend == "postgres" &&
		answers.postgresPort != 0 &&
		answers.postgresPort != oldState.PostgresPort
	if !userChangedBackend && !userChangedPostgresPort {
		if err := preservePostgresFromOldState(cmd, state, oldState); err != nil {
			return fmt.Errorf("preserving postgres settings: %w", err)
		}
		return nil
	}
	if state.PersistenceBackend == "postgres" && oldState.PostgresPassword != "" {
		state.PostgresPassword = oldState.PostgresPassword
	}
	return nil
}

// validatePortFlags checks --backend-port / --web-port ranges and
// non-collision.
func validatePortFlags() error {
	if initBackendPort != 0 && (initBackendPort < 1 || initBackendPort > 65535) {
		return fmt.Errorf("invalid --backend-port %d: must be 1-65535", initBackendPort)
	}
	if initWebPort != 0 && (initWebPort < 1 || initWebPort > 65535) {
		return fmt.Errorf("invalid --web-port %d: must be 1-65535", initWebPort)
	}
	if initBackendPort != 0 && initWebPort != 0 && initBackendPort == initWebPort {
		return fmt.Errorf("--backend-port and --web-port must differ, both are %d", initBackendPort)
	}
	return nil
}

// validateEnumFlags checks the string-enum flags against their allowlists.
// Empty values are skipped (no flag provided).
func validateEnumFlags() error {
	type enumFlag struct {
		name    string
		value   string
		valid   func(string) bool
		options string
	}
	flags := []enumFlag{
		{"--sandbox", initSandbox, config.IsValidBool, "\"true\" or \"false\""},
		{"--encrypt-secrets", initEncryptSecrets, config.IsValidBool, "\"true\" or \"false\""},
		{"--log-level", initLogLevel, config.IsValidLogLevel, config.LogLevelNames()},
		{"--channel", initChannel, config.IsValidChannel, config.ChannelNames()},
		{"--bus-backend", initBusBackend, config.IsValidBusBackend, config.BusBackendNames()},
		{"--persistence-backend", initPersistenceBackend, config.IsValidPersistenceBackend, config.PersistenceBackendNames()},
	}
	for _, f := range flags {
		if f.value == "" {
			continue
		}
		if !f.valid(f.value) {
			return fmt.Errorf("invalid %s %q: must be one of %s", f.name, f.value, f.options)
		}
	}
	if initImageTag != "" && !config.IsValidImageTag(initImageTag) {
		return fmt.Errorf("invalid --image-tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", initImageTag)
	}
	return nil
}

// resolveEffectiveBackend determines which persistence backend
// --postgres-port should be evaluated against. Explicit
// --persistence-backend wins; otherwise the persisted backend from
// dataDir is preloaded best-effort; otherwise the State default.
func resolveEffectiveBackend(dataDir string) string {
	if initPersistenceBackend != "" {
		return initPersistenceBackend
	}
	if dataDir != "" {
		// Best-effort preload: if the config doesn't exist yet or can't
		// be parsed, fall through to the State default and let the real
		// error surface during writeInitFiles. A corrupted config is
		// not a reason to reject a valid --postgres-port flag here.
		if oldState, err := config.Load(dataDir); err == nil && oldState.PersistenceBackend != "" {
			return oldState.PersistenceBackend
		}
	}
	return config.DefaultState().PersistenceBackend
}

// validatePostgresFlag checks --postgres-port: the backend must be
// Postgres, the port must be in range, and it must not collide with the
// backend/web ports.
func validatePostgresFlag(dataDir string) error {
	if initPostgresPort == 0 {
		return nil
	}
	effectiveBackend := resolveEffectiveBackend(dataDir)
	if effectiveBackend != "postgres" {
		return fmt.Errorf(
			"--postgres-port %d is only valid with --persistence-backend postgres "+
				"(current effective backend: %q)",
			initPostgresPort, effectiveBackend,
		)
	}
	if initPostgresPort < 1 || initPostgresPort > 65535 {
		return fmt.Errorf("invalid --postgres-port %d: must be 1-65535", initPostgresPort)
	}
	if initBackendPort != 0 && initPostgresPort == initBackendPort {
		return fmt.Errorf(
			"invalid --postgres-port %d: conflicts with --backend-port %d",
			initPostgresPort, initBackendPort,
		)
	}
	if initWebPort != 0 && initPostgresPort == initWebPort {
		return fmt.Errorf(
			"invalid --postgres-port %d: conflicts with --web-port %d",
			initPostgresPort, initWebPort,
		)
	}
	return nil
}
