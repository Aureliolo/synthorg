package config

import (
	"fmt"
	"runtime"
	"strconv"
	"strings"
	"time"
)

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

// enumCheck describes a config-field-against-allowlist check. emptyOK
// means an empty value is allowed (the field defaults at read time).
type enumCheck struct {
	name    string
	value   string
	valid   func(string) bool
	options string
	emptyOK bool
}

// runEnumChecks returns the first failing check, or nil. Empty values
// for checks with emptyOK=true are skipped.
func runEnumChecks(checks []enumCheck) error {
	for _, c := range checks {
		if c.emptyOK && c.value == "" {
			continue
		}
		if !c.valid(c.value) {
			return fmt.Errorf("invalid %s %q: must be one of %s", c.name, c.value, c.options)
		}
	}
	return nil
}

func validateBackends(s State) error {
	if s.DockerSockGID < -1 || s.DockerSockGID > 4294967295 {
		return fmt.Errorf("invalid docker_sock_gid %d: must be -1 to 4294967295", s.DockerSockGID)
	}
	if s.ImageTag != "" && !IsValidImageTag(s.ImageTag) {
		return fmt.Errorf("invalid image_tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", s.ImageTag)
	}
	return runEnumChecks([]enumCheck{
		{"persistence_backend", s.PersistenceBackend, IsValidPersistenceBackend, sortedKeys(validPersistenceBackends), false},
		{"memory_backend", s.MemoryBackend, IsValidMemoryBackend, sortedKeys(validMemoryBackends), false},
		{"bus_backend", s.BusBackend, IsValidBusBackend, sortedKeys(validBusBackends), true},
		{"channel", s.Channel, IsValidChannel, sortedKeys(validChannels), true},
		{"log_level", s.LogLevel, IsValidLogLevel, sortedKeys(validLogLevels), true},
	})
}

func validateDisplayModes(s State) error {
	return runEnumChecks([]enumCheck{
		{"color", s.Color, IsValidColorMode, ColorModeNames(), true},
		{"output", s.Output, IsValidOutputMode, OutputModeNames(), true},
		{"timestamps", s.Timestamps, IsValidTimestampMode, TimestampModeNames(), true},
		{"hints", s.Hints, IsValidHintsMode, HintsModeNames(), true},
		{"changelog_view", s.ChangelogView, IsValidChangelogView, ChangelogViewNames(), true},
	})
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
	if !s.EncryptSecrets || strings.TrimSpace(s.MasterKey) == "" {
		return nil
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

// validateRegistryFields checks the registry/tag string fields against
// their per-field format rules. Empty fields are skipped (treated as
// "use default"). Unlike the enum-mode checks the error message includes
// a regex-like rule rather than an allowlist, so the table records the
// rule alongside the predicate.
func validateRegistryFields(s State) error {
	type formatCheck struct {
		name  string
		value string
		valid func(string) bool
		rule  string
	}
	checks := []formatCheck{
		{"registry_host", s.RegistryHost, IsValidRegistryHost, "must be a DNS hostname (optionally with :port)"},
		{"dhi_registry", s.DHIRegistry, IsValidRegistryHost, "must be a DNS hostname (optionally with :port)"},
		{"image_repo_prefix", s.ImageRepoPrefix, IsValidImageRepoPrefix, "must match [a-z0-9][a-z0-9._/-]*"},
		{"postgres_image_tag", s.PostgresImageTag, IsValidImageTag, "must match [a-zA-Z0-9][a-zA-Z0-9._-]*"},
		{"nats_image_tag", s.NATSImageTag, IsValidImageTag, "must match [a-zA-Z0-9][a-zA-Z0-9._-]*"},
		{"default_nats_stream_prefix", s.DefaultNATSStreamPrefix, IsValidStreamPrefix, "must match [A-Z0-9][A-Z0-9_-]*"},
	}
	for _, c := range checks {
		if c.value == "" {
			continue
		}
		if !c.valid(c.value) {
			return fmt.Errorf("invalid %s %q: %s", c.name, c.value, c.rule)
		}
	}
	return nil
}

// validateDurationFields parses each duration string and checks the
// per-field floor. image_verify_timeout has an additional minimum
// (MinImageVerifyTimeout) because shorter values silently bypass
// cosign/SLSA verification.
func validateDurationFields(s State) error {
	durations := []struct {
		name, value string
	}{
		{"backup_create_timeout", s.BackupCreateTimeout},
		{"backup_restore_timeout", s.BackupRestoreTimeout},
		{"health_check_timeout", s.HealthCheckTimeout},
		{"self_update_http_timeout", s.SelfUpdateHTTPTimeout},
		{"self_update_api_timeout", s.SelfUpdateAPITimeout},
		{"tuf_fetch_timeout", s.TUFFetchTimeout},
		{"attestation_http_timeout", s.AttestationHTTPTimeout},
		{"image_verify_timeout", s.ImageVerifyTimeout},
		{"image_pull_retry_delay", s.ImagePullRetryDelay},
	}
	for _, d := range durations {
		if err := validateOneDuration(d.name, d.value); err != nil {
			return err
		}
	}
	return nil
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

func validateByteFields(s State) error {
	byteFields := []struct {
		name  string
		value int64
	}{
		{"max_api_response_bytes", s.MaxAPIResponseBytes},
		{"max_binary_bytes", s.MaxBinaryBytes},
		{"max_archive_entry_bytes", s.MaxArchiveEntryBytes},
	}
	for _, b := range byteFields {
		if b.value == 0 {
			continue
		}
		if b.value < 0 {
			return fmt.Errorf("invalid %s %d: must be positive", b.name, b.value)
		}
		if b.value > MaxBytesCeiling {
			return fmt.Errorf("invalid %s %d: exceeds ceiling %d (1 GiB)", b.name, b.value, MaxBytesCeiling)
		}
	}
	return nil
}
