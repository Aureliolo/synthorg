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
	if err := resolveRegistryTunables(&t, s); err != nil {
		return Tunables{}, err
	}
	if err := resolveDurationTunables(&t, s); err != nil {
		return Tunables{}, err
	}
	if err := resolveCountTunables(&t, s); err != nil {
		return Tunables{}, err
	}
	t.CustomRegistry = t.RegistryHost != DefaultRegistryHost ||
		t.ImageRepoPrefix != DefaultImageRepoPrefix ||
		t.DHIRegistry != DefaultDHIRegistry ||
		t.PostgresImageTag != DefaultPostgresImageTag ||
		t.NATSImageTag != DefaultNATSImageTag
	return t, nil
}

// resolveRegistryTunables fills the registry/tag string fields on t,
// applying the env > state > default precedence and validating each
// against its format predicate.
func resolveRegistryTunables(t *Tunables, s State) error {
	t.RegistryHost = firstNonEmpty(os.Getenv(EnvRegistryHost), s.RegistryHost, t.RegistryHost)
	t.ImageRepoPrefix = firstNonEmpty(os.Getenv(EnvImageRepoPrefix), s.ImageRepoPrefix, t.ImageRepoPrefix)
	t.DHIRegistry = firstNonEmpty(os.Getenv(EnvDHIRegistry), s.DHIRegistry, t.DHIRegistry)
	t.PostgresImageTag = firstNonEmpty(os.Getenv(EnvPostgresImageTag), s.PostgresImageTag, t.PostgresImageTag)
	t.NATSImageTag = firstNonEmpty(os.Getenv(EnvNATSImageTag), s.NATSImageTag, t.NATSImageTag)
	t.DefaultNATSStreamPrefix = firstNonEmpty(os.Getenv(EnvDefaultNATSStreamPfx), s.DefaultNATSStreamPrefix, t.DefaultNATSStreamPrefix)

	checks := []struct {
		name  string
		value string
		valid func(string) bool
	}{
		{"registry_host", t.RegistryHost, IsValidRegistryHost},
		{"dhi_registry", t.DHIRegistry, IsValidRegistryHost},
		{"image_repo_prefix", t.ImageRepoPrefix, IsValidImageRepoPrefix},
		{"postgres_image_tag", t.PostgresImageTag, IsValidImageTag},
		{"nats_image_tag", t.NATSImageTag, IsValidImageTag},
		{"default_nats_stream_prefix", t.DefaultNATSStreamPrefix, IsValidStreamPrefix},
	}
	for _, c := range checks {
		if !c.valid(c.value) {
			return fmt.Errorf("invalid %s %q", c.name, c.value)
		}
	}
	return nil
}

// resolveDurationField looks up env > stateValue > current *dst, parses
// the chosen value into a duration, and writes it back through dst.
// Using a pointer instead of a setter closure keeps the per-call alloc
// count at zero (the closure pattern allocates one func value per
// duration, which is hot on the ResolveTunables path).
func resolveDurationField(key, envName, stateValue string, dst *time.Duration) error {
	d, err := resolveDuration(envName, stateValue, *dst)
	if err != nil {
		return fmt.Errorf("%s: %w", key, err)
	}
	*dst = d
	return nil
}

// resolveDurationTunables fills every duration field on t, plus the
// image-verify floor and image_pull_attempts integer (kept together
// because both gate image-pull behaviour).
//
// The bindings table is built as a stack-local fixed-size array of
// plain data (no closures, no function pointers) so iteration is a
// direct call into resolveDurationField with no indirect-call
// escape forcing State to the heap. That eliminates the per-call
// 208-byte State copy the previous closure-based table introduced.
func resolveDurationTunables(t *Tunables, s State) error {
	bindings := [...]struct {
		key        string
		envName    string
		stateValue string
		dst        *time.Duration
	}{
		{"backup_create_timeout", EnvBackupCreateTimeout, s.BackupCreateTimeout, &t.BackupCreateTimeout},
		{"backup_restore_timeout", EnvBackupRestoreTimeout, s.BackupRestoreTimeout, &t.BackupRestoreTimeout},
		{"health_check_timeout", EnvHealthCheckTimeout, s.HealthCheckTimeout, &t.HealthCheckTimeout},
		{"self_update_http_timeout", EnvSelfUpdateHTTPTimeout, s.SelfUpdateHTTPTimeout, &t.SelfUpdateHTTPTimeout},
		{"self_update_api_timeout", EnvSelfUpdateAPITimeout, s.SelfUpdateAPITimeout, &t.SelfUpdateAPITimeout},
		{"tuf_fetch_timeout", EnvTUFFetchTimeout, s.TUFFetchTimeout, &t.TUFFetchTimeout},
		{"attestation_http_timeout", EnvAttestationHTTPTimeout, s.AttestationHTTPTimeout, &t.AttestationHTTPTimeout},
		{"image_verify_timeout", EnvImageVerifyTimeout, s.ImageVerifyTimeout, &t.ImageVerifyTimeout},
		{"image_pull_retry_delay", EnvImagePullRetryDelay, s.ImagePullRetryDelay, &t.ImagePullRetryDelay},
	}
	for _, b := range bindings {
		if err := resolveDurationField(b.key, b.envName, b.stateValue, b.dst); err != nil {
			return err
		}
	}
	if t.ImageVerifyTimeout < MinImageVerifyTimeout {
		return fmt.Errorf(
			"image_verify_timeout: %v is below the %v minimum floor; a shorter timeout would bypass cosign/SLSA verification by silently timing out",
			t.ImageVerifyTimeout, MinImageVerifyTimeout,
		)
	}
	return nil
}

// resolveBytesField looks up env > stateValue > current *dst, parses the
// chosen value into a byte count, and writes it back through dst.
// Pointer-based for the same zero-alloc reason as resolveDurationField.
func resolveBytesField(key, envName string, stateValue int64, dst *int64) error {
	n, err := resolveBytes(envName, stateValue, *dst)
	if err != nil {
		return fmt.Errorf("%s: %w", key, err)
	}
	*dst = n
	return nil
}

// resolveCountTunables fills image_pull_attempts and the byte-size fields
// on t. Bytes are kept together because they share an identical resolve
// helper and ceiling check.
func resolveCountTunables(t *Tunables, s State) error {
	attempts, err := resolveInt(EnvImagePullAttempts, s.ImagePullAttempts, t.ImagePullAttempts, 1, MaxImagePullAttempts)
	if err != nil {
		return fmt.Errorf("image_pull_attempts: %w", err)
	}
	t.ImagePullAttempts = attempts

	if err := resolveBytesField("max_api_response_bytes", EnvMaxAPIResponseBytes, s.MaxAPIResponseBytes, &t.MaxAPIResponseBytes); err != nil {
		return err
	}
	if err := resolveBytesField("max_binary_bytes", EnvMaxBinaryBytes, s.MaxBinaryBytes, &t.MaxBinaryBytes); err != nil {
		return err
	}
	return resolveBytesField("max_archive_entry_bytes", EnvMaxArchiveEntryBytes, s.MaxArchiveEntryBytes, &t.MaxArchiveEntryBytes)
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
	numPart, unit := splitBytesInput(s)
	n, err := strconv.ParseFloat(numPart, 64)
	if err != nil {
		return 0, fmt.Errorf("parse number %q: %w", numPart, err)
	}
	if n <= 0 {
		// Tunables that feed io.LimitReader / HTTP response-size caps
		// would disable the protection entirely at zero; the contract
		// is "strictly positive".
		return 0, fmt.Errorf("non-positive size %v", n)
	}
	mult, err := byteUnitMultiplier(unit)
	if err != nil {
		return 0, err
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

// splitBytesInput separates the leading numeric portion from any trailing
// alphabetic unit suffix. Only digits and a single decimal point are
// accepted in the numeric part; a leading '-' or any other character
// would fall through to strconv.ParseFloat with a clearer error than
// producing a negative number we reject later.
func splitBytesInput(s string) (numPart, unit string) {
	cut := len(s)
	for i, r := range s {
		if (r >= '0' && r <= '9') || r == '.' {
			continue
		}
		cut = i
		break
	}
	return s[:cut], strings.ToLower(strings.TrimSpace(s[cut:]))
}

// byteUnitMultiplier maps a normalised unit suffix to its byte multiplier.
// Empty/"b" is 1. IEC (KiB, MiB, GiB) use 1024 powers; SI (K/KB, M/MB,
// G/GB) use 1000 powers.
func byteUnitMultiplier(unit string) (float64, error) {
	switch unit {
	case "", "b":
		return 1, nil
	case "k", "kb":
		return 1000, nil
	case "ki", "kib":
		return 1024, nil
	case "m", "mb":
		return 1000 * 1000, nil
	case "mi", "mib":
		return 1024 * 1024, nil
	case "g", "gb":
		return 1000 * 1000 * 1000, nil
	case "gi", "gib":
		return 1024 * 1024 * 1024, nil
	default:
		return 0, fmt.Errorf("unknown unit %q", unit)
	}
}
