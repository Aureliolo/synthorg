package cmd

import (
	"strconv"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// tunableSpec describes one tunable config key end-to-end: how it is
// set, reset, read back for display, and which SYNTHORG_* env var
// shadows it. Centralising the per-key info in one struct lets the four
// dispatchers (applyTunableConfigValue, resetTunableConfigValue,
// tunableConfigGetValue, tunableEnvVarForKey) collapse to a single map
// lookup each.
type tunableSpec struct {
	set    func(state *config.State, value string) error
	reset  func(state *config.State)
	get    func(state config.State) string
	envVar string
}

// tunableSpecs maps every tunable key to its spec. Spec entries are
// hand-rolled rather than reflected from struct tags because the
// per-key validators (DNS hostname, repo prefix, image tag, NATS
// stream prefix, duration, integer range, byte size) and per-key
// default fallbacks vary in shape.
var tunableSpecs = map[string]tunableSpec{
	"registry_host": {
		set:    func(s *config.State, v string) error { return setRegistryHost(v, "registry_host", &s.RegistryHost) },
		reset:  func(s *config.State) { s.RegistryHost = "" },
		get:    func(s config.State) string { return displayOrFallback(s.RegistryHost, config.DefaultRegistryHost) },
		envVar: EnvRegistryHost,
	},
	"image_repo_prefix": {
		set:   func(s *config.State, v string) error { return setImageRepoPrefix(v, &s.ImageRepoPrefix) },
		reset: func(s *config.State) { s.ImageRepoPrefix = "" },
		get: func(s config.State) string {
			return displayOrFallback(s.ImageRepoPrefix, config.DefaultImageRepoPrefix)
		},
		envVar: EnvImageRepoPrefix,
	},
	"dhi_registry": {
		set:    func(s *config.State, v string) error { return setRegistryHost(v, "dhi_registry", &s.DHIRegistry) },
		reset:  func(s *config.State) { s.DHIRegistry = "" },
		get:    func(s config.State) string { return displayOrFallback(s.DHIRegistry, config.DefaultDHIRegistry) },
		envVar: EnvDHIRegistry,
	},
	"postgres_image_tag": {
		set:   func(s *config.State, v string) error { return setTag(v, "postgres_image_tag", &s.PostgresImageTag) },
		reset: func(s *config.State) { s.PostgresImageTag = "" },
		get: func(s config.State) string {
			return displayOrFallback(s.PostgresImageTag, config.DefaultPostgresImageTag)
		},
		envVar: EnvPostgresImageTag,
	},
	"nats_image_tag": {
		set:    func(s *config.State, v string) error { return setTag(v, "nats_image_tag", &s.NATSImageTag) },
		reset:  func(s *config.State) { s.NATSImageTag = "" },
		get:    func(s config.State) string { return displayOrFallback(s.NATSImageTag, config.DefaultNATSImageTag) },
		envVar: EnvNATSImageTag,
	},
	"default_nats_stream_prefix": {
		set:   func(s *config.State, v string) error { return setStreamPrefix(v, &s.DefaultNATSStreamPrefix) },
		reset: func(s *config.State) { s.DefaultNATSStreamPrefix = "" },
		get: func(s config.State) string {
			return displayOrFallback(s.DefaultNATSStreamPrefix, config.DefaultNATSStreamPrefixValue)
		},
		envVar: EnvDefaultNATSStreamPfx,
	},
	"backup_create_timeout":    durationTunable("backup_create_timeout", config.DefaultBackupCreateTimeout, EnvBackupCreateTimeout, func(s *config.State) *string { return &s.BackupCreateTimeout }),
	"backup_restore_timeout":   durationTunable("backup_restore_timeout", config.DefaultBackupRestoreTimeout, EnvBackupRestoreTimeout, func(s *config.State) *string { return &s.BackupRestoreTimeout }),
	"health_check_timeout":     durationTunable("health_check_timeout", config.DefaultHealthCheckTimeout, EnvHealthCheckTimeout, func(s *config.State) *string { return &s.HealthCheckTimeout }),
	"self_update_http_timeout": durationTunable("self_update_http_timeout", config.DefaultSelfUpdateHTTPTimeout, EnvSelfUpdateHTTPTimeout, func(s *config.State) *string { return &s.SelfUpdateHTTPTimeout }),
	"self_update_api_timeout":  durationTunable("self_update_api_timeout", config.DefaultSelfUpdateAPITimeout, EnvSelfUpdateAPITimeout, func(s *config.State) *string { return &s.SelfUpdateAPITimeout }),
	"tuf_fetch_timeout":        durationTunable("tuf_fetch_timeout", config.DefaultTUFFetchTimeout, EnvTUFFetchTimeout, func(s *config.State) *string { return &s.TUFFetchTimeout }),
	"attestation_http_timeout": durationTunable("attestation_http_timeout", config.DefaultAttestationHTTPTimeout, EnvAttestationHTTPTimeout, func(s *config.State) *string { return &s.AttestationHTTPTimeout }),
	"image_verify_timeout":     durationTunable("image_verify_timeout", config.DefaultImageVerifyTimeout, EnvImageVerifyTimeout, func(s *config.State) *string { return &s.ImageVerifyTimeout }),
	"image_pull_retry_delay":   durationTunable("image_pull_retry_delay", config.DefaultImagePullRetryDelay, EnvImagePullRetryDelay, func(s *config.State) *string { return &s.ImagePullRetryDelay }),
	"image_pull_attempts": {
		set: func(s *config.State, v string) error {
			return setIntInRange(v, "image_pull_attempts", 1, config.MaxImagePullAttempts, &s.ImagePullAttempts)
		},
		reset: func(s *config.State) { s.ImagePullAttempts = "" },
		get: func(s config.State) string {
			return displayOrFallback(s.ImagePullAttempts, strconv.Itoa(config.DefaultImagePullAttempts))
		},
		envVar: EnvImagePullAttempts,
	},
	"max_api_response_bytes":  byteSizeTunable("max_api_response_bytes", config.DefaultMaxAPIResponseBytes, EnvMaxAPIResponseBytes, func(s *config.State) *int64 { return &s.MaxAPIResponseBytes }),
	"max_binary_bytes":        byteSizeTunable("max_binary_bytes", config.DefaultMaxBinaryBytes, EnvMaxBinaryBytes, func(s *config.State) *int64 { return &s.MaxBinaryBytes }),
	"max_archive_entry_bytes": byteSizeTunable("max_archive_entry_bytes", config.DefaultMaxArchiveEntryBytes, EnvMaxArchiveEntryBytes, func(s *config.State) *int64 { return &s.MaxArchiveEntryBytes }),
}

// durationTunable constructs the spec for a string-typed duration
// tunable. The duration is stored as its normalised time.Duration
// string form so config.json stays human-readable.
func durationTunable(key string, def interface{ String() string }, env string, accessor func(*config.State) *string) tunableSpec {
	return tunableSpec{
		set:    func(s *config.State, v string) error { return setDuration(v, key, accessor(s)) },
		reset:  func(s *config.State) { *accessor(s) = "" },
		get:    func(s config.State) string { return displayOrFallback(*accessor(&s), def.String()) },
		envVar: env,
	}
}

// byteSizeTunable constructs the spec for an int64-typed byte-size
// tunable. ParseBytes converts the human-readable input ("1MiB") into
// the stored int64.
func byteSizeTunable(key string, def int64, env string, accessor func(*config.State) *int64) tunableSpec {
	return tunableSpec{
		set:    func(s *config.State, v string) error { return setByteSize(v, key, accessor(s)) },
		reset:  func(s *config.State) { *accessor(s) = 0 },
		get:    func(s config.State) string { return int64OrDefault(*accessor(&s), def) },
		envVar: env,
	}
}
