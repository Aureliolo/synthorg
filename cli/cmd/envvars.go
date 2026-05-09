package cmd

import (
	"os"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// Environment variable names for SynthOrg CLI configuration.
// Precedence: CLI flag > env var > config file > default.
const (
	EnvDataDir       = "SYNTHORG_DATA_DIR"
	EnvLogLevel      = "SYNTHORG_LOG_LEVEL"
	EnvBackendPort   = "SYNTHORG_BACKEND_PORT"
	EnvWebPort       = "SYNTHORG_WEB_PORT"
	EnvChannel       = "SYNTHORG_CHANNEL"
	EnvImageTag      = "SYNTHORG_IMAGE_TAG"
	EnvNoVerify      = "SYNTHORG_NO_VERIFY"
	EnvSkipVerify    = "SYNTHORG_SKIP_VERIFY" // backward-compat alias for EnvNoVerify
	EnvAutoUpdateCLI = "SYNTHORG_AUTO_UPDATE_CLI"
	EnvAutoPull      = "SYNTHORG_AUTO_PULL"
	EnvAutoRestart   = "SYNTHORG_AUTO_RESTART"
	EnvTelemetry     = "SYNTHORG_TELEMETRY_ENABLED"
	EnvQuiet         = "SYNTHORG_QUIET"
	EnvYes           = "SYNTHORG_YES" // suppresses ALL interactive confirmation prompts
)

// Tunable env vars are declared in the config package to keep
// ResolveTunables self-contained and avoid a cmd -> config -> cmd cycle.
// Re-export them here so the cmd layer uses a single symbol namespace.
const (
	EnvRegistryHost           = config.EnvRegistryHost
	EnvImageRepoPrefix        = config.EnvImageRepoPrefix
	EnvDHIRegistry            = config.EnvDHIRegistry
	EnvPostgresImageTag       = config.EnvPostgresImageTag
	EnvNATSImageTag           = config.EnvNATSImageTag
	EnvDefaultNATSStreamPfx   = config.EnvDefaultNATSStreamPfx
	EnvBackupCreateTimeout    = config.EnvBackupCreateTimeout
	EnvBackupRestoreTimeout   = config.EnvBackupRestoreTimeout
	EnvHealthCheckTimeout     = config.EnvHealthCheckTimeout
	EnvSelfUpdateHTTPTimeout  = config.EnvSelfUpdateHTTPTimeout
	EnvSelfUpdateAPITimeout   = config.EnvSelfUpdateAPITimeout
	EnvTUFFetchTimeout        = config.EnvTUFFetchTimeout
	EnvAttestationHTTPTimeout = config.EnvAttestationHTTPTimeout
	EnvImageVerifyTimeout     = config.EnvImageVerifyTimeout
	EnvImagePullAttempts      = config.EnvImagePullAttempts
	EnvImagePullRetryDelay    = config.EnvImagePullRetryDelay
	EnvMaxAPIResponseBytes    = config.EnvMaxAPIResponseBytes
	EnvMaxBinaryBytes         = config.EnvMaxBinaryBytes
	EnvMaxArchiveEntryBytes   = config.EnvMaxArchiveEntryBytes
)

// envBool returns true if the named env var is set to a truthy value
// ("1", "true", "yes", case-insensitive). All other values -- including
// "false", "0", "no", and empty string -- are treated as false.
// There is no way to explicitly negate a flag via env var; absence = off.
func envBool(name string) bool {
	v := strings.TrimSpace(os.Getenv(name))
	if v == "" {
		return false
	}
	switch strings.ToLower(v) {
	case "1", "true", "yes":
		return true
	}
	return false
}

// noColorFromEnv checks the standard environment signals for disabling color:
// NO_COLOR (any non-empty value), CLICOLOR=0, TERM=dumb.
func noColorFromEnv() bool {
	if os.Getenv("NO_COLOR") != "" {
		return true
	}
	if os.Getenv("CLICOLOR") == "0" {
		return true
	}
	if os.Getenv("TERM") == "dumb" {
		return true
	}
	return false
}
