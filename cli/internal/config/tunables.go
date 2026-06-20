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
//
// The helpers take and return Tunables BY VALUE (not via *Tunables). Taking
// &t across the helper boundaries defeated escape analysis on the ~208-byte
// Tunables struct, forcing a heap allocation per ResolveTunables call (one
// of the regressions CLI Bench Regression caught). Pass-by-value keeps the
// struct on the stack and turns the per-call cost into a small stack memcpy
// instead, which is essentially free at this size.
func ResolveTunables(s State) (Tunables, error) {
	t := DefaultTunables()
	var err error
	if t, err = resolveRegistryTunables(t, s); err != nil {
		return Tunables{}, err
	}
	if t, err = resolveDurationTunables(t, s); err != nil {
		return Tunables{}, err
	}
	if t, err = resolveCountTunables(t, s); err != nil {
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
//
// Per-field validation is unrolled (rather than table-driven) to keep
// the resolveRegistryTunables hot path zero-alloc. A previous
// `[]struct{name, value, valid}` literal escaped to the heap once per
// call (~208 B/op) because the slice header survived the range loop,
// which tripped CLI Bench Regression on ResolveTunables.
func resolveRegistryTunables(t Tunables, s State) (Tunables, error) {
	t.RegistryHost = firstNonEmpty(os.Getenv(EnvRegistryHost), s.RegistryHost, t.RegistryHost)
	t.ImageRepoPrefix = firstNonEmpty(os.Getenv(EnvImageRepoPrefix), s.ImageRepoPrefix, t.ImageRepoPrefix)
	t.DHIRegistry = firstNonEmpty(os.Getenv(EnvDHIRegistry), s.DHIRegistry, t.DHIRegistry)
	t.PostgresImageTag = firstNonEmpty(os.Getenv(EnvPostgresImageTag), s.PostgresImageTag, t.PostgresImageTag)
	t.NATSImageTag = firstNonEmpty(os.Getenv(EnvNATSImageTag), s.NATSImageTag, t.NATSImageTag)
	t.DefaultNATSStreamPrefix = firstNonEmpty(os.Getenv(EnvDefaultNATSStreamPfx), s.DefaultNATSStreamPrefix, t.DefaultNATSStreamPrefix)
	return t, validateResolvedRegistryFields(t)
}

// validateResolvedRegistryFields runs the per-field format predicates
// on the registry / image-tag fields after resolution. Extracted so
// resolveRegistryTunables stays under the cyclomatic-complexity
// ceiling (6 ifs plus the 6 firstNonEmpty assignments would push it
// over) without re-introducing a per-call slice. Takes Tunables by
// value (read-only); the caller already owns the updated copy.
func validateResolvedRegistryFields(t Tunables) error {
	if !IsValidRegistryHost(t.RegistryHost) {
		return fmt.Errorf("invalid registry_host %q", t.RegistryHost)
	}
	if !IsValidRegistryHost(t.DHIRegistry) {
		return fmt.Errorf("invalid dhi_registry %q", t.DHIRegistry)
	}
	if !IsValidImageRepoPrefix(t.ImageRepoPrefix) {
		return fmt.Errorf("invalid image_repo_prefix %q", t.ImageRepoPrefix)
	}
	if !IsValidImageTag(t.PostgresImageTag) {
		return fmt.Errorf("invalid postgres_image_tag %q", t.PostgresImageTag)
	}
	if !IsValidImageTag(t.NATSImageTag) {
		return fmt.Errorf("invalid nats_image_tag %q", t.NATSImageTag)
	}
	if !IsValidStreamPrefix(t.DefaultNATSStreamPrefix) {
		return fmt.Errorf("invalid default_nats_stream_prefix %q", t.DefaultNATSStreamPrefix)
	}
	return nil
}

// resolveDurationField returns the resolved duration for one field
// without taking the address of any caller-owned storage. Pointer-based
// dst was tried in earlier rounds and forced Tunables to the heap via
// escape analysis on the caller's bindings table; returning the value
// keeps everything on stack and matches main's pattern.
func resolveDurationField(key, envName, stateValue string, def time.Duration) (time.Duration, error) {
	d, err := resolveDuration(envName, stateValue, def)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", key, err)
	}
	return d, nil
}

// resolveDurationTunables fills every duration field on t, plus the
// image-verify floor. Direct assignment per field (no bindings table
// holding &t.X pointers) so Tunables never has its address taken in
// this function, which previously caused the ~208-byte struct to
// heap-allocate per ResolveTunables call.
func resolveDurationTunables(t Tunables, s State) (Tunables, error) {
	var err error
	if t.BackupCreateTimeout, err = resolveDurationField("backup_create_timeout", EnvBackupCreateTimeout, s.BackupCreateTimeout, t.BackupCreateTimeout); err != nil {
		return t, err
	}
	if t.BackupRestoreTimeout, err = resolveDurationField("backup_restore_timeout", EnvBackupRestoreTimeout, s.BackupRestoreTimeout, t.BackupRestoreTimeout); err != nil {
		return t, err
	}
	if t.HealthCheckTimeout, err = resolveDurationField("health_check_timeout", EnvHealthCheckTimeout, s.HealthCheckTimeout, t.HealthCheckTimeout); err != nil {
		return t, err
	}
	t, err = resolveSelfUpdateAndTUFTimeouts(t, s)
	if err != nil {
		return t, err
	}
	t, err = resolveImageTimeouts(t, s)
	if err != nil {
		return t, err
	}
	if t.ImageVerifyTimeout < MinImageVerifyTimeout {
		return t, fmt.Errorf(
			"image_verify_timeout: %v is below the %v minimum floor; a shorter timeout would bypass cosign/SLSA verification by silently timing out",
			t.ImageVerifyTimeout, MinImageVerifyTimeout,
		)
	}
	return t, nil
}

// resolveSelfUpdateAndTUFTimeouts resolves the three timeouts that
// gate the self-update + TUF fetch + attestation paths. Split out of
// resolveDurationTunables so neither function blows the per-function
// cyclomatic-complexity ceiling without re-introducing a bindings
// table (which would heap-allocate Tunables).
func resolveSelfUpdateAndTUFTimeouts(t Tunables, s State) (Tunables, error) {
	var err error
	if t.SelfUpdateHTTPTimeout, err = resolveDurationField("self_update_http_timeout", EnvSelfUpdateHTTPTimeout, s.SelfUpdateHTTPTimeout, t.SelfUpdateHTTPTimeout); err != nil {
		return t, err
	}
	if t.SelfUpdateAPITimeout, err = resolveDurationField("self_update_api_timeout", EnvSelfUpdateAPITimeout, s.SelfUpdateAPITimeout, t.SelfUpdateAPITimeout); err != nil {
		return t, err
	}
	if t.TUFFetchTimeout, err = resolveDurationField("tuf_fetch_timeout", EnvTUFFetchTimeout, s.TUFFetchTimeout, t.TUFFetchTimeout); err != nil {
		return t, err
	}
	t.AttestationHTTPTimeout, err = resolveDurationField("attestation_http_timeout", EnvAttestationHTTPTimeout, s.AttestationHTTPTimeout, t.AttestationHTTPTimeout)
	return t, err
}

// resolveImageTimeouts resolves the image-verify / pull-retry pair.
// Floor check on image_verify_timeout lives in resolveDurationTunables
// because it needs to see the final resolved value.
func resolveImageTimeouts(t Tunables, s State) (Tunables, error) {
	var err error
	if t.ImageVerifyTimeout, err = resolveDurationField("image_verify_timeout", EnvImageVerifyTimeout, s.ImageVerifyTimeout, t.ImageVerifyTimeout); err != nil {
		return t, err
	}
	t.ImagePullRetryDelay, err = resolveDurationField("image_pull_retry_delay", EnvImagePullRetryDelay, s.ImagePullRetryDelay, t.ImagePullRetryDelay)
	return t, err
}

// resolveBytesField returns the resolved byte count without taking
// the address of any caller-owned storage. Mirrors resolveDurationField's
// zero-alloc value-return shape.
func resolveBytesField(key, envName string, stateValue, def int64) (int64, error) {
	n, err := resolveBytes(envName, stateValue, def)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", key, err)
	}
	return n, nil
}

// resolveCountTunables fills image_pull_attempts and the byte-size fields
// on t. Bytes are kept together because they share an identical resolve
// helper and ceiling check. Same value-pass pattern as the other
// resolve helpers to keep Tunables on stack.
func resolveCountTunables(t Tunables, s State) (Tunables, error) {
	attempts, err := resolveInt(EnvImagePullAttempts, s.ImagePullAttempts, t.ImagePullAttempts, 1, MaxImagePullAttempts)
	if err != nil {
		return t, fmt.Errorf("image_pull_attempts: %w", err)
	}
	t.ImagePullAttempts = attempts
	if t.MaxAPIResponseBytes, err = resolveBytesField("max_api_response_bytes", EnvMaxAPIResponseBytes, s.MaxAPIResponseBytes, t.MaxAPIResponseBytes); err != nil {
		return t, err
	}
	if t.MaxBinaryBytes, err = resolveBytesField("max_binary_bytes", EnvMaxBinaryBytes, s.MaxBinaryBytes, t.MaxBinaryBytes); err != nil {
		return t, err
	}
	if t.MaxBinaryBytes < MinBinaryBytes {
		return t, fmt.Errorf(
			"max_binary_bytes: %d is below the %d minimum floor; a smaller cap would silently truncate the self-update binary download",
			t.MaxBinaryBytes, MinBinaryBytes,
		)
	}
	if t.MaxArchiveEntryBytes, err = resolveBytesField("max_archive_entry_bytes", EnvMaxArchiveEntryBytes, s.MaxArchiveEntryBytes, t.MaxArchiveEntryBytes); err != nil {
		return t, err
	}
	if t.MaxArchiveEntryBytes < MinArchiveEntryBytes {
		return t, fmt.Errorf(
			"max_archive_entry_bytes: %d is below the %d minimum floor; a smaller cap would silently truncate the self-update archive entry",
			t.MaxArchiveEntryBytes, MinArchiveEntryBytes,
		)
	}
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
