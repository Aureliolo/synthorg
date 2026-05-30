package config

import (
	"errors"
	"fmt"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// ErrMissingMasterKey is returned (wrapped) by Validate when
// EncryptSecrets is true and MasterKey is empty. Exported as a sentinel
// so the init reinit flow can distinguish this recoverable legacy-config
// case from a hard validation failure and route through the
// LoadAllowMissingMasterKey path to regenerate the key on save.
var ErrMissingMasterKey = errors.New("master_key is required when encrypt_secrets is true")

// Per-section validators called by State.Validate. Each returns the first
// failure for the section it covers; Validate runs them in order and
// returns the first non-nil error.

func validatePorts(s State) error {
	if s.BackendPort < 1 || s.BackendPort > 65535 {
		return fmt.Errorf("invalid backend_port %d: must be 1-65535", s.BackendPort)
	}
	if s.WebPort < 1 || s.WebPort > 65535 {
		return fmt.Errorf("invalid web_port %d: must be 1-65535", s.WebPort)
	}
	if s.NatsClientPort != 0 && (s.NatsClientPort < 1 || s.NatsClientPort > 65535) {
		return fmt.Errorf("invalid nats_client_port %d: must be 1-65535", s.NatsClientPort)
	}
	return nil
}

// checkEnumRequired returns an "invalid …" error when value is not in
// the allowlist tested by valid. Empty values are rejected.
func checkEnumRequired(name, value string, valid func(string) bool, options string) error {
	if !valid(value) {
		return fmt.Errorf("invalid %s %q: must be one of %s", name, value, options)
	}
	return nil
}

// checkEnumOptional behaves like checkEnumRequired but treats an empty
// value as "use default" and skips it.
func checkEnumOptional(name, value string, valid func(string) bool, options string) error {
	if value == "" {
		return nil
	}
	return checkEnumRequired(name, value, valid, options)
}

func validateBackends(s State) error {
	// docker_sock_gid is a Linux GID: -1 disables the override, the upper
	// bound is uint32 max. Widen to int64 first so the untyped 4294967295
	// constant is representable where int is 32-bit.
	if gid := int64(s.DockerSockGID); gid < -1 || gid > 4294967295 {
		return fmt.Errorf("invalid docker_sock_gid %d: must be -1 to 4294967295", s.DockerSockGID)
	}
	if s.ImageTag != "" && !IsValidImageTag(s.ImageTag) {
		return fmt.Errorf("invalid image_tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", s.ImageTag)
	}
	if err := checkEnumRequired("persistence_backend", s.PersistenceBackend, IsValidPersistenceBackend, PersistenceBackendNames()); err != nil {
		return err
	}
	if err := checkEnumRequired("memory_backend", s.MemoryBackend, IsValidMemoryBackend, MemoryBackendNames()); err != nil {
		return err
	}
	if err := checkEnumOptional("bus_backend", s.BusBackend, IsValidBusBackend, BusBackendNames()); err != nil {
		return err
	}
	if err := checkEnumOptional("channel", s.Channel, IsValidChannel, ChannelNames()); err != nil {
		return err
	}
	return checkEnumOptional("log_level", s.LogLevel, IsValidLogLevel, LogLevelNames())
}

func validateDisplayModes(s State) error {
	if err := checkEnumOptional("color", s.Color, IsValidColorMode, ColorModeNames()); err != nil {
		return err
	}
	if err := checkEnumOptional("output", s.Output, IsValidOutputMode, OutputModeNames()); err != nil {
		return err
	}
	if err := checkEnumOptional("timestamps", s.Timestamps, IsValidTimestampMode, TimestampModeNames()); err != nil {
		return err
	}
	if err := checkEnumOptional("hints", s.Hints, IsValidHintsMode, HintsModeNames()); err != nil {
		return err
	}
	return checkEnumOptional("changelog_view", s.ChangelogView, IsValidChangelogView, ChangelogViewNames())
}

// validatePostgres validates Postgres-specific fields when the backend
// is Postgres. Returns nil for any other backend. Self-gating means the
// caller does not need an outer if and the function stays flat.
func validatePostgres(s State) error {
	if s.PersistenceBackend != "postgres" {
		return nil
	}
	if s.PostgresPort < 1 || s.PostgresPort > 65535 {
		return fmt.Errorf("invalid postgres_port %d: must be 1-65535", s.PostgresPort)
	}
	if strings.TrimSpace(s.PostgresPassword) == "" {
		return fmt.Errorf("postgres_password is required when persistence_backend is postgres")
	}
	if len(s.PostgresPassword) < 32 {
		return fmt.Errorf("postgres_password must be at least 32 characters, got %d", len(s.PostgresPassword))
	}
	// The password is interpolated into the Postgres DSN, written to the
	// compose.yml env block, and forwarded to docker. A stray newline
	// could split the DSN or produce a YAML value that deserializes to
	// something else.
	if strings.ContainsAny(s.PostgresPassword, "\x00\n\r\t") {
		return fmt.Errorf("postgres_password must not contain control characters (NUL, CR, LF, TAB)")
	}
	return nil
}

func validateMasterKey(s State) error {
	if !s.EncryptSecrets {
		return nil
	}
	if strings.TrimSpace(s.MasterKey) == "" {
		return ErrMissingMasterKey
	}
	if err := validateFernetKey(s.MasterKey); err != nil {
		return fmt.Errorf("invalid master_key: %w", err)
	}
	return nil
}

func validateFineTuning(s State) error {
	if s.FineTuning && !s.Sandbox {
		return fmt.Errorf("fine_tuning requires sandbox to be enabled")
	}
	if s.FineTuning && runtime.GOARCH != "amd64" {
		return fmt.Errorf("fine_tuning requires x86_64 (amd64) architecture; the fine-tune image is not available for %s", runtime.GOARCH)
	}
	// Variant validation is unconditional: an invalid persisted value that
	// went unnoticed while fine_tuning=false would silently coerce to "gpu"
	// the moment the user flipped the feature on. Reject typos at load time
	// regardless of the current toggle state.
	switch s.FineTuningVariant {
	case "", FineTuneVariantGPU, FineTuneVariantCPU:
		return nil
	default:
		return fmt.Errorf("fine_tuning_variant must be %q or %q, got %q", FineTuneVariantGPU, FineTuneVariantCPU, s.FineTuningVariant)
	}
}

func validateVerifiedDigests(s State) error {
	for name, digest := range s.VerifiedDigests {
		if !isValidDigestFormat(digest) {
			return fmt.Errorf("invalid verified_digests[%q]: %q is not a valid sha256 digest", name, digest)
		}
	}
	return nil
}

// checkFormat returns an "invalid …" error when value fails valid.
// Empty values are skipped (treated as "use default"). Unlike the
// enum-mode helpers the message embeds a regex-like rule rather than
// an allowlist.
func checkFormat(name, value string, valid func(string) bool, rule string) error {
	if value == "" {
		return nil
	}
	if !valid(value) {
		return fmt.Errorf("invalid %s %q: %s", name, value, rule)
	}
	return nil
}

// validateRegistryFields checks the registry/tag string fields against
// their per-field format rules.
func validateRegistryFields(s State) error {
	if err := checkFormat("registry_host", s.RegistryHost, IsValidRegistryHost, "must be a DNS hostname (optionally with :port)"); err != nil {
		return err
	}
	if err := checkFormat("dhi_registry", s.DHIRegistry, IsValidRegistryHost, "must be a DNS hostname (optionally with :port)"); err != nil {
		return err
	}
	if err := checkFormat("image_repo_prefix", s.ImageRepoPrefix, IsValidImageRepoPrefix, "must match [a-z0-9][a-z0-9._/-]*"); err != nil {
		return err
	}
	if err := checkFormat("postgres_image_tag", s.PostgresImageTag, IsValidImageTag, "must match [a-zA-Z0-9][a-zA-Z0-9._-]*"); err != nil {
		return err
	}
	if err := checkFormat("nats_image_tag", s.NATSImageTag, IsValidImageTag, "must match [a-zA-Z0-9][a-zA-Z0-9._-]*"); err != nil {
		return err
	}
	return checkFormat("default_nats_stream_prefix", s.DefaultNATSStreamPrefix, IsValidStreamPrefix, "must match [A-Z0-9][A-Z0-9_-]*")
}

// validateDurationFields parses each duration string and checks the
// per-field floor. image_verify_timeout has an additional minimum
// (MinImageVerifyTimeout) because shorter values silently bypass
// cosign/SLSA verification.
func validateDurationFields(s State) error {
	if err := validateOneDuration("backup_create_timeout", s.BackupCreateTimeout); err != nil {
		return err
	}
	if err := validateOneDuration("backup_restore_timeout", s.BackupRestoreTimeout); err != nil {
		return err
	}
	if err := validateOneDuration("health_check_timeout", s.HealthCheckTimeout); err != nil {
		return err
	}
	if err := validateOneDuration("self_update_http_timeout", s.SelfUpdateHTTPTimeout); err != nil {
		return err
	}
	if err := validateOneDuration("self_update_api_timeout", s.SelfUpdateAPITimeout); err != nil {
		return err
	}
	if err := validateOneDuration("tuf_fetch_timeout", s.TUFFetchTimeout); err != nil {
		return err
	}
	if err := validateOneDuration("attestation_http_timeout", s.AttestationHTTPTimeout); err != nil {
		return err
	}
	if err := validateOneDuration("image_verify_timeout", s.ImageVerifyTimeout); err != nil {
		return err
	}
	return validateOneDuration("image_pull_retry_delay", s.ImagePullRetryDelay)
}

func validateOneDuration(name, value string) error {
	if value == "" {
		return nil
	}
	parsed, err := time.ParseDuration(value)
	if err != nil {
		return fmt.Errorf("invalid %s %q: %w", name, value, err)
	}
	if parsed <= 0 {
		return fmt.Errorf("invalid %s %q: must be > 0", name, value)
	}
	if name == "image_verify_timeout" && parsed < MinImageVerifyTimeout {
		return fmt.Errorf(
			"invalid %s %q: %v is below the %v minimum floor; a shorter timeout would bypass cosign/SLSA verification by silently timing out",
			name, value, parsed, MinImageVerifyTimeout,
		)
	}
	return nil
}

func validateIntegerFields(s State) error {
	if s.ImagePullAttempts == "" {
		return nil
	}
	n, err := strconv.Atoi(s.ImagePullAttempts)
	if err != nil {
		return fmt.Errorf("invalid image_pull_attempts %q: %w", s.ImagePullAttempts, err)
	}
	if n < 1 || n > MaxImagePullAttempts {
		return fmt.Errorf("invalid image_pull_attempts %q: must be in [1, %d]", s.ImagePullAttempts, MaxImagePullAttempts)
	}
	return nil
}

// checkByteField returns an "invalid …" error when value is negative
// or above MaxBytesCeiling. Zero is treated as "use default" and
// skipped (the byte tunables are int64 with a sentinel-zero default).
func checkByteField(name string, value int64) error {
	if value == 0 {
		return nil
	}
	if value < 0 {
		return fmt.Errorf("invalid %s %d: must be positive", name, value)
	}
	if value > MaxBytesCeiling {
		return fmt.Errorf("invalid %s %d: exceeds ceiling %d (1 GiB)", name, value, MaxBytesCeiling)
	}
	return nil
}

func validateByteFields(s State) error {
	if err := checkByteField("max_api_response_bytes", s.MaxAPIResponseBytes); err != nil {
		return err
	}
	if err := checkByteField("max_binary_bytes", s.MaxBinaryBytes); err != nil {
		return err
	}
	return checkByteField("max_archive_entry_bytes", s.MaxArchiveEntryBytes)
}
