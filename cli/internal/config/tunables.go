package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Environment variable names for every tunable. Duplicated from cli/cmd/envvars.go
// so the config package can resolve without a circular import. Keep these in
// sync with cli/cmd/envvars.go.
const (
	EnvRegistryHost           = "SYNTHORG_REGISTRY_HOST"
	EnvImageRepoPrefix        = "SYNTHORG_IMAGE_REPO_PREFIX"
	EnvDHIRegistry            = "SYNTHORG_DHI_REGISTRY"
	EnvPostgresImageTag       = "SYNTHORG_POSTGRES_IMAGE_TAG"
	EnvNATSImageTag           = "SYNTHORG_NATS_IMAGE_TAG"
	EnvDefaultNATSStreamPfx   = "SYNTHORG_DEFAULT_NATS_STREAM_PREFIX"
	EnvBackupCreateTimeout    = "SYNTHORG_BACKUP_CREATE_TIMEOUT"
	EnvBackupRestoreTimeout   = "SYNTHORG_BACKUP_RESTORE_TIMEOUT"
	EnvHealthCheckTimeout     = "SYNTHORG_HEALTH_CHECK_TIMEOUT"
	EnvSelfUpdateHTTPTimeout  = "SYNTHORG_SELF_UPDATE_HTTP_TIMEOUT"
	EnvSelfUpdateAPITimeout   = "SYNTHORG_SELF_UPDATE_API_TIMEOUT"
	EnvTUFFetchTimeout        = "SYNTHORG_TUF_FETCH_TIMEOUT"
	EnvAttestationHTTPTimeout = "SYNTHORG_ATTESTATION_HTTP_TIMEOUT"
	EnvImageVerifyTimeout     = "SYNTHORG_IMAGE_VERIFY_TIMEOUT"
	EnvImagePullAttempts      = "SYNTHORG_IMAGE_PULL_ATTEMPTS"
	EnvImagePullRetryDelay    = "SYNTHORG_IMAGE_PULL_RETRY_DELAY"
	EnvMaxAPIResponseBytes    = "SYNTHORG_MAX_API_RESPONSE_BYTES"
	EnvMaxBinaryBytes         = "SYNTHORG_MAX_BINARY_BYTES"
	EnvMaxArchiveEntryBytes   = "SYNTHORG_MAX_ARCHIVE_ENTRY_BYTES"
)

// Tunables holds the resolved tunable values after merging compiled-in
// defaults, persisted state, and environment variable overrides.
// Precedence: env > state > default.
type Tunables struct {
	RegistryHost     string
	ImageRepoPrefix  string
	DHIRegistry      string
	PostgresImageTag string
	NATSImageTag     string

	DefaultNATSStreamPrefix string

	BackupCreateTimeout    time.Duration
	BackupRestoreTimeout   time.Duration
	HealthCheckTimeout     time.Duration
	SelfUpdateHTTPTimeout  time.Duration
	SelfUpdateAPITimeout   time.Duration
	TUFFetchTimeout        time.Duration
	AttestationHTTPTimeout time.Duration
	ImageVerifyTimeout     time.Duration
	ImagePullRetryDelay    time.Duration
	ImagePullAttempts      int

	MaxAPIResponseBytes  int64
	MaxBinaryBytes       int64
	MaxArchiveEntryBytes int64

	// CustomRegistry is true if any of the registry/tag fields resolved to
	// something other than the compiled-in default. Consumers use this to
	// force SkipVerify and emit a trust-transfer warning: the pinned SAN
	// regex and DHI digest map are bound to the default registry/tags, so
	// verification cannot succeed against a custom deployment.
	CustomRegistry bool
}

// DefaultTunables returns a Tunables populated entirely with compiled-in
// defaults. Useful for tests and as the baseline for ResolveTunables.
func DefaultTunables() Tunables {
	return Tunables{
		RegistryHost:            DefaultRegistryHost,
		ImageRepoPrefix:         DefaultImageRepoPrefix,
		DHIRegistry:             DefaultDHIRegistry,
		PostgresImageTag:        DefaultPostgresImageTag,
		NATSImageTag:            DefaultNATSImageTag,
		DefaultNATSStreamPrefix: DefaultNATSStreamPrefixValue,
		BackupCreateTimeout:     DefaultBackupCreateTimeout,
		BackupRestoreTimeout:    DefaultBackupRestoreTimeout,
		HealthCheckTimeout:      DefaultHealthCheckTimeout,
		SelfUpdateHTTPTimeout:   DefaultSelfUpdateHTTPTimeout,
		SelfUpdateAPITimeout:    DefaultSelfUpdateAPITimeout,
		TUFFetchTimeout:         DefaultTUFFetchTimeout,
		AttestationHTTPTimeout:  DefaultAttestationHTTPTimeout,
		ImageVerifyTimeout:      DefaultImageVerifyTimeout,
		ImagePullRetryDelay:     DefaultImagePullRetryDelay,
		ImagePullAttempts:       DefaultImagePullAttempts,
		MaxAPIResponseBytes:     DefaultMaxAPIResponseBytes,
		MaxBinaryBytes:          DefaultMaxBinaryBytes,
		MaxArchiveEntryBytes:    DefaultMaxArchiveEntryBytes,
	}
}

// ResolveTunables computes the final tunable values from state + env, applying
// precedence env > state > default. Returns a validated Tunables or a detailed
// error if any env/state override is malformed. Safe to call more than once
// but typically invoked exactly once from root.go PersistentPreRunE.
func ResolveTunables(s State) (Tunables, error) {
	t := DefaultTunables()

	// Registry / tag strings.
	t.RegistryHost = firstNonEmpty(os.Getenv(EnvRegistryHost), s.RegistryHost, t.RegistryHost)
	t.ImageRepoPrefix = firstNonEmpty(os.Getenv(EnvImageRepoPrefix), s.ImageRepoPrefix, t.ImageRepoPrefix)
	t.DHIRegistry = firstNonEmpty(os.Getenv(EnvDHIRegistry), s.DHIRegistry, t.DHIRegistry)
	t.PostgresImageTag = firstNonEmpty(os.Getenv(EnvPostgresImageTag), s.PostgresImageTag, t.PostgresImageTag)
	t.NATSImageTag = firstNonEmpty(os.Getenv(EnvNATSImageTag), s.NATSImageTag, t.NATSImageTag)

	if !IsValidRegistryHost(t.RegistryHost) {
		return Tunables{}, fmt.Errorf("invalid registry_host %q", t.RegistryHost)
	}
	if !IsValidRegistryHost(t.DHIRegistry) {
		return Tunables{}, fmt.Errorf("invalid dhi_registry %q", t.DHIRegistry)
	}
	if !IsValidImageRepoPrefix(t.ImageRepoPrefix) {
		return Tunables{}, fmt.Errorf("invalid image_repo_prefix %q", t.ImageRepoPrefix)
	}
	if !IsValidImageTag(t.PostgresImageTag) {
		return Tunables{}, fmt.Errorf("invalid postgres_image_tag %q", t.PostgresImageTag)
	}
	if !IsValidImageTag(t.NATSImageTag) {
		return Tunables{}, fmt.Errorf("invalid nats_image_tag %q", t.NATSImageTag)
	}

	// NATS stream prefix default. The NATS URL itself is no longer
	// resolved here -- the worker reads ``SYNTHORG_NATS_URL`` directly
	// so the CLI and the backend's ``communication.nats_url`` setting
	// share a single env var.
	t.DefaultNATSStreamPrefix = firstNonEmpty(os.Getenv(EnvDefaultNATSStreamPfx), s.DefaultNATSStreamPrefix, t.DefaultNATSStreamPrefix)
	if !IsValidStreamPrefix(t.DefaultNATSStreamPrefix) {
		return Tunables{}, fmt.Errorf("invalid default_nats_stream_prefix %q", t.DefaultNATSStreamPrefix)
	}

	// Durations.
	var err error
	if t.BackupCreateTimeout, err = resolveDuration(EnvBackupCreateTimeout, s.BackupCreateTimeout, t.BackupCreateTimeout); err != nil {
		return Tunables{}, fmt.Errorf("backup_create_timeout: %w", err)
	}
	if t.BackupRestoreTimeout, err = resolveDuration(EnvBackupRestoreTimeout, s.BackupRestoreTimeout, t.BackupRestoreTimeout); err != nil {
		return Tunables{}, fmt.Errorf("backup_restore_timeout: %w", err)
	}
	if t.HealthCheckTimeout, err = resolveDuration(EnvHealthCheckTimeout, s.HealthCheckTimeout, t.HealthCheckTimeout); err != nil {
		return Tunables{}, fmt.Errorf("health_check_timeout: %w", err)
	}
	if t.SelfUpdateHTTPTimeout, err = resolveDuration(EnvSelfUpdateHTTPTimeout, s.SelfUpdateHTTPTimeout, t.SelfUpdateHTTPTimeout); err != nil {
		return Tunables{}, fmt.Errorf("self_update_http_timeout: %w", err)
	}
	if t.SelfUpdateAPITimeout, err = resolveDuration(EnvSelfUpdateAPITimeout, s.SelfUpdateAPITimeout, t.SelfUpdateAPITimeout); err != nil {
		return Tunables{}, fmt.Errorf("self_update_api_timeout: %w", err)
	}
	if t.TUFFetchTimeout, err = resolveDuration(EnvTUFFetchTimeout, s.TUFFetchTimeout, t.TUFFetchTimeout); err != nil {
		return Tunables{}, fmt.Errorf("tuf_fetch_timeout: %w", err)
	}
	if t.AttestationHTTPTimeout, err = resolveDuration(EnvAttestationHTTPTimeout, s.AttestationHTTPTimeout, t.AttestationHTTPTimeout); err != nil {
		return Tunables{}, fmt.Errorf("attestation_http_timeout: %w", err)
	}
	if t.ImageVerifyTimeout, err = resolveDuration(EnvImageVerifyTimeout, s.ImageVerifyTimeout, t.ImageVerifyTimeout); err != nil {
		return Tunables{}, fmt.Errorf("image_verify_timeout: %w", err)
	}
	if t.ImageVerifyTimeout < MinImageVerifyTimeout {
		return Tunables{}, fmt.Errorf(
			"image_verify_timeout: %v is below the %v minimum floor; a shorter timeout would bypass cosign/SLSA verification by silently timing out",
			t.ImageVerifyTimeout, MinImageVerifyTimeout,
		)
	}
	if t.ImagePullRetryDelay, err = resolveDuration(EnvImagePullRetryDelay, s.ImagePullRetryDelay, t.ImagePullRetryDelay); err != nil {
		return Tunables{}, fmt.Errorf("image_pull_retry_delay: %w", err)
	}
	if t.ImagePullAttempts, err = resolveInt(EnvImagePullAttempts, s.ImagePullAttempts, t.ImagePullAttempts, 1, MaxImagePullAttempts); err != nil {
		return Tunables{}, fmt.Errorf("image_pull_attempts: %w", err)
	}

	// Byte sizes.
	if t.MaxAPIResponseBytes, err = resolveBytes(EnvMaxAPIResponseBytes, s.MaxAPIResponseBytes, t.MaxAPIResponseBytes); err != nil {
		return Tunables{}, fmt.Errorf("max_api_response_bytes: %w", err)
	}
	if t.MaxBinaryBytes, err = resolveBytes(EnvMaxBinaryBytes, s.MaxBinaryBytes, t.MaxBinaryBytes); err != nil {
		return Tunables{}, fmt.Errorf("max_binary_bytes: %w", err)
	}
	if t.MaxArchiveEntryBytes, err = resolveBytes(EnvMaxArchiveEntryBytes, s.MaxArchiveEntryBytes, t.MaxArchiveEntryBytes); err != nil {
		return Tunables{}, fmt.Errorf("max_archive_entry_bytes: %w", err)
	}

	t.CustomRegistry = t.RegistryHost != DefaultRegistryHost ||
		t.ImageRepoPrefix != DefaultImageRepoPrefix ||
		t.DHIRegistry != DefaultDHIRegistry ||
		t.PostgresImageTag != DefaultPostgresImageTag ||
		t.NATSImageTag != DefaultNATSImageTag

	return t, nil
}

// firstNonEmpty returns the first whitespace-trimmed non-empty string
// from the arguments. Trims consistently: if the caller's string was
// accepted as non-empty, the surrounding whitespace is stripped before
// returning so downstream consumers see canonical values.
func firstNonEmpty(vs ...string) string {
	for _, v := range vs {
		if trimmed := strings.TrimSpace(v); trimmed != "" {
			return trimmed
		}
	}
	return ""
}

// resolveDuration returns the first valid duration from env > state > def.
// Empty env/state values are skipped (not treated as errors).
func resolveDuration(envName, stateValue string, def time.Duration) (time.Duration, error) {
	if v := strings.TrimSpace(os.Getenv(envName)); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return 0, fmt.Errorf("env %s=%q: %w", envName, v, err)
		}
		if d <= 0 {
			return 0, fmt.Errorf("env %s=%q: must be > 0", envName, v)
		}
		return d, nil
	}
	if v := strings.TrimSpace(stateValue); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return 0, fmt.Errorf("state %q: %w", v, err)
		}
		if d <= 0 {
			return 0, fmt.Errorf("state %q: must be > 0", v)
		}
		return d, nil
	}
	return def, nil
}

// resolveInt returns the first valid integer from env > state > def.
// Both env and state values must parse as integers and fall within
// [minValue, maxValue] (inclusive); empty values are skipped.
func resolveInt(envName, stateValue string, def, minValue, maxValue int) (int, error) {
	if v := strings.TrimSpace(os.Getenv(envName)); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return 0, fmt.Errorf("env %s=%q: %w", envName, v, err)
		}
		if n < minValue || n > maxValue {
			return 0, fmt.Errorf("env %s=%q: must be in [%d, %d]", envName, v, minValue, maxValue)
		}
		return n, nil
	}
	if v := strings.TrimSpace(stateValue); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return 0, fmt.Errorf("state %q: %w", v, err)
		}
		if n < minValue || n > maxValue {
			return 0, fmt.Errorf("state %q: must be in [%d, %d]", v, minValue, maxValue)
		}
		return n, nil
	}
	return def, nil
}

// resolveBytes returns the first valid byte count from env > state > def.
// The env value accepts plain bytes ("1048576") or IEC suffixes ("1MiB",
// "256MiB", "128MiB"). SI suffixes ("1MB" = 1000000) are also supported.
// State values are plain int64 bytes.
func resolveBytes(envName string, stateValue, def int64) (int64, error) {
	if v := strings.TrimSpace(os.Getenv(envName)); v != "" {
		n, err := ParseBytes(v)
		if err != nil {
			return 0, fmt.Errorf("env %s=%q: %w", envName, v, err)
		}
		if n <= 0 {
			return 0, fmt.Errorf("env %s=%q: must be > 0", envName, v)
		}
		if n > MaxBytesCeiling {
			return 0, fmt.Errorf("env %s=%q: exceeds ceiling %d", envName, v, MaxBytesCeiling)
		}
		return n, nil
	}
	if stateValue > 0 {
		if stateValue > MaxBytesCeiling {
			return 0, fmt.Errorf("state %d: exceeds ceiling %d", stateValue, MaxBytesCeiling)
		}
		return stateValue, nil
	}
	if stateValue < 0 {
		return 0, fmt.Errorf("state %d: must be positive", stateValue)
	}
	return def, nil
}

// ParseBytes parses a human-readable byte count. Accepts plain integers
// ("1048576"), IEC binary suffixes (B, KiB, MiB, GiB), and SI decimal
// suffixes (KB, MB, GB). Case-insensitive. Rejects negative values and
// inputs large enough to overflow int64 (computed safely without silently
// wrapping around negative).
func ParseBytes(s string) (int64, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, fmt.Errorf("empty value")
	}
	// Split trailing alphabetic suffix from the leading numeric part.
	// Only digits and a single decimal point may appear; a leading '-'
	// or any other character fails parsing (rather than producing a
	// negative number that would be rejected later -- catching it here
	// produces a clearer error and avoids float edge cases).
	cut := len(s)
	for i, r := range s {
		if (r >= '0' && r <= '9') || r == '.' {
			continue
		}
		cut = i
		break
	}
	numPart := s[:cut]
	unit := strings.ToLower(strings.TrimSpace(s[cut:]))
	n, err := strconv.ParseFloat(numPart, 64)
	if err != nil {
		return 0, fmt.Errorf("parse number %q: %w", numPart, err)
	}
	if n <= 0 {
		// Per CLAUDE.md tunable-value spec: byte sizes reject negative
		// AND zero values. Tunables that feed io.LimitReader or HTTP
		// response-size caps would disable the protection entirely at
		// zero, so the contract is "strictly positive".
		return 0, fmt.Errorf("non-positive size %v", n)
	}
	var mult float64
	switch unit {
	case "", "b":
		mult = 1
	case "k", "kb":
		mult = 1000
	case "ki", "kib":
		mult = 1024
	case "m", "mb":
		mult = 1000 * 1000
	case "mi", "mib":
		mult = 1024 * 1024
	case "g", "gb":
		mult = 1000 * 1000 * 1000
	case "gi", "gib":
		mult = 1024 * 1024 * 1024
	default:
		return 0, fmt.Errorf("unknown unit %q", unit)
	}
	// Reject values that exceed the runtime ceiling while still in
	// float64 space, BEFORE the cast to int64. Comparing against
	// MaxBytesCeiling (1 GiB) rather than math.MaxInt64 avoids float64
	// rounding edge cases near int64 limits: float64(math.MaxInt64)
	// rounds up to 2^63, so a product equal to 2^63 passes the
	// float64 check and then yields math.MinInt64 after the cast on
	// amd64. MaxBytesCeiling is exactly representable in float64, so
	// no rounding ambiguity exists at the boundary.
	product := n * mult
	if product > float64(MaxBytesCeiling) {
		return 0, fmt.Errorf("size %s exceeds ceiling %d bytes (1 GiB)", s, MaxBytesCeiling)
	}
	result := int64(product)
	if result <= 0 {
		// Sub-byte fractions (e.g. "0.5B", ".000001KiB") truncate to 0
		// after the cast even though the pre-cast float is > 0. That
		// would silently disable any downstream io.LimitReader cap, so
		// reject anything that cannot represent at least one byte.
		return 0, fmt.Errorf("size %s resolves to non-positive byte count", s)
	}
	return result, nil
}
