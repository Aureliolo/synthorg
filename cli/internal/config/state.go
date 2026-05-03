package config

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
)

const stateFileName = "config.json"

// Fine-tune variant identifiers persisted in State.FineTuningVariant and
// used to construct image service names (e.g. "synthorg-fine-tune-gpu").
const (
	FineTuneVariantGPU = "gpu"
	FineTuneVariantCPU = "cpu"
)

// State is the persisted CLI configuration written by `synthorg init`.
type State struct {
	DataDir       string `json:"data_dir"`
	ImageTag      string `json:"image_tag"`
	Channel       string `json:"channel"`
	BackendPort   int    `json:"backend_port"`
	WebPort       int    `json:"web_port"`
	Sandbox       bool   `json:"sandbox"`
	DockerSock    string `json:"docker_sock,omitempty"`
	DockerSockGID int    `json:"docker_sock_gid"`
	LogLevel      string `json:"log_level"`
	JWTSecret     string `json:"jwt_secret,omitempty"`
	SettingsKey   string `json:"settings_key,omitempty"`
	// CursorSecret is the HMAC signing key for opaque pagination cursor
	// tokens (>= 16 bytes URL-safe base64). Generated at init time and
	// preserved across re-init: rotating it would invalidate every
	// outstanding pagination cursor on every restart, which the backend
	// refuses to start without. Wired into the backend container as
	// SYNTHORG_PAGINATION_CURSOR_SECRET unconditionally -- the boot
	// guard is the same in dev, pre-release, and prod.
	CursorSecret string `json:"cursor_secret,omitempty"`
	// MasterKey is a Fernet-compatible URL-safe base64 of 32 bytes used
	// to encrypt connection secrets at rest. Generated at init time and
	// preserved across re-init (regenerating would orphan every stored
	// secret). Wired into the backend container as SYNTHORG_MASTER_KEY
	// only when EncryptSecrets is true.
	MasterKey          string            `json:"master_key,omitempty"`
	EncryptSecrets     bool              `json:"encrypt_secrets"`
	PersistenceBackend string            `json:"persistence_backend"`
	MemoryBackend      string            `json:"memory_backend"`
	BusBackend         string            `json:"bus_backend"`
	NatsClientPort     int               `json:"nats_client_port,omitempty"`
	PostgresPort       int               `json:"postgres_port,omitempty"`
	PostgresPassword   string            `json:"postgres_password,omitempty"`
	AutoCleanup        bool              `json:"auto_cleanup"`
	VerifiedDigests    map[string]string `json:"verified_digests,omitempty"`

	// Display preferences (empty = use default).
	Color         string `json:"color,omitempty"`          // always/auto/never
	Output        string `json:"output,omitempty"`         // text/json
	Timestamps    string `json:"timestamps,omitempty"`     // relative/iso8601
	Hints         string `json:"hints,omitempty"`          // always/auto/never
	ChangelogView string `json:"changelog_view,omitempty"` // highlights/commits

	// Auto-behavior keys (false = prompt interactively).
	AutoUpdateCLI      bool `json:"auto_update_cli"`
	AutoPull           bool `json:"auto_pull"`
	AutoRestart        bool `json:"auto_restart"`
	AutoApplyCompose   bool `json:"auto_apply_compose"`
	AutoStartAfterWipe bool `json:"auto_start_after_wipe"`

	// Telemetry (opt-in anonymous product telemetry, default false).
	TelemetryOptIn bool `json:"telemetry_opt_in"`

	// Fine-tuning (requires sandbox/Docker for container execution).
	//
	// When FineTuning is true, FineTuningVariant selects which image to pull:
	//   - "gpu" (default): bundled CUDA torch, ~4 GB, runs on NVIDIA hosts
	//   - "cpu": CPU-only torch, ~1.7 GB, runs anywhere
	// An empty value is treated as "gpu" at read time for backward
	// compatibility with pre-split configs, but the init flow always writes
	// an explicit variant. The backend reads
	// ``ghcr.io/aureliolo/synthorg-fine-tune-{variant}`` via
	// SYNTHORG_FINE_TUNE_IMAGE.
	FineTuning        bool   `json:"fine_tuning"`
	FineTuningVariant string `json:"fine_tuning_variant,omitempty"`

	// Registry + image tag overrides. Overriding any of these disables
	// signature and provenance verification because the pinned identity
	// policy (SAN regex) and DHI digest map are bound to the defaults.
	// Empty values mean "use the compiled-in default".
	RegistryHost     string `json:"registry_host,omitempty"`
	ImageRepoPrefix  string `json:"image_repo_prefix,omitempty"`
	DHIRegistry      string `json:"dhi_registry,omitempty"`
	PostgresImageTag string `json:"postgres_image_tag,omitempty"`
	NATSImageTag     string `json:"nats_image_tag,omitempty"`

	// Default values for the `synthorg worker start` flags.
	DefaultNATSURL          string `json:"default_nats_url,omitempty"`
	DefaultNATSStreamPrefix string `json:"default_nats_stream_prefix,omitempty"`

	// Timeout strings parsed by time.ParseDuration (e.g. "30s", "5m").
	// Empty = use compiled-in default.
	BackupCreateTimeout    string `json:"backup_create_timeout,omitempty"`
	BackupRestoreTimeout   string `json:"backup_restore_timeout,omitempty"`
	HealthCheckTimeout     string `json:"health_check_timeout,omitempty"`
	SelfUpdateHTTPTimeout  string `json:"self_update_http_timeout,omitempty"`
	SelfUpdateAPITimeout   string `json:"self_update_api_timeout,omitempty"`
	TUFFetchTimeout        string `json:"tuf_fetch_timeout,omitempty"`
	AttestationHTTPTimeout string `json:"attestation_http_timeout,omitempty"`
	ImageVerifyTimeout     string `json:"image_verify_timeout,omitempty"`
	ImagePullRetryDelay    string `json:"image_pull_retry_delay,omitempty"`

	// Integer strings parsed by strconv.Atoi. Empty = use compiled-in default.
	ImagePullAttempts string `json:"image_pull_attempts,omitempty"`

	// Download size ceilings in bytes. Zero = use compiled-in default.
	MaxAPIResponseBytes  int64 `json:"max_api_response_bytes,omitempty"`
	MaxBinaryBytes       int64 `json:"max_binary_bytes,omitempty"`
	MaxArchiveEntryBytes int64 `json:"max_archive_entry_bytes,omitempty"`
}

// Compiled-in default values for the tunables. Exposed so Tunables can detect
// customisation (CustomRegistry = any registry/tag field differs from default).
const (
	DefaultRegistryHost    = "ghcr.io"
	DefaultImageRepoPrefix = "aureliolo/synthorg-"
	DefaultDHIRegistry     = "dhi.io"
	// renovate: datasource=docker depName=dhi.io/postgres
	DefaultPostgresImageTag = "18-debian13"
	// renovate: datasource=docker depName=dhi.io/nats
	DefaultNATSImageTag = "2.12-debian13"

	DefaultNATSURLValue          = "nats://nats:4222"
	DefaultNATSStreamPrefixValue = "SYNTHORG"

	DefaultBackupCreateTimeout    = 60 * time.Second
	DefaultBackupRestoreTimeout   = 30 * time.Second
	DefaultHealthCheckTimeout     = 5 * time.Second
	DefaultSelfUpdateHTTPTimeout  = 5 * time.Minute
	DefaultSelfUpdateAPITimeout   = 30 * time.Second
	DefaultTUFFetchTimeout        = 30 * time.Second
	DefaultAttestationHTTPTimeout = 30 * time.Second
	DefaultImageVerifyTimeout     = 120 * time.Second
	DefaultImagePullRetryDelay    = 2 * time.Second
	DefaultImagePullAttempts      = 3

	// MinImageVerifyTimeout is the lower bound operators may set for
	// image-signature verification.  Anything shorter would almost
	// certainly time out before cosign / SLSA / TUF can complete the
	// required network I/O, effectively bypassing verification by
	// silently failing open.  One second is a hard floor, not an
	// expected value -- typical verification takes 10-60s.
	MinImageVerifyTimeout = 1 * time.Second

	// MaxImagePullAttempts caps the user-provided retry count so an
	// operator cannot accidentally wedge ``synthorg start`` behind a
	// thousand sequential retries.  Kept well above the sensible
	// operational range (3-5) but finite.
	MaxImagePullAttempts = 100

	// DefaultMaxAPIResponseBytes is the per-call cap on JSON responses from
	// the GitHub API. Sized for the list-commits walk used by `synthorg
	// update`: each commit object inlines the full PGP signature plus the
	// signed payload (a duplicate of the message) plus 20+ author/committer
	// URL fields, averaging ~15 KiB per commit; at per_page=25 a typical
	// page is ~400 KiB. 4 MiB gives 10x headroom for outlier release-PR-
	// heavy pages without inviting runaway allocations.
	DefaultMaxAPIResponseBytes  int64 = 4 * 1024 * 1024
	DefaultMaxBinaryBytes       int64 = 256 * 1024 * 1024
	DefaultMaxArchiveEntryBytes int64 = 128 * 1024 * 1024

	// MaxBytesCeiling caps any user-provided size limit to prevent runaway
	// allocations if someone sets a ridiculous value.
	MaxBytesCeiling int64 = 1 * 1024 * 1024 * 1024
)

// DefaultState returns a State with sensible defaults for the interactive init
// wizard. Note: Load applies a more conservative fallback (sandbox disabled)
// when no config file exists.
//
// Host port layout (contiguous with existing services):
//
//	3000 web / 3001 backend / 3002 postgres / 3003 NATS client.
//
// Tunable fields (registry, timeouts, size limits) are intentionally left
// empty here; an empty value means "resolve to the compiled-in default at
// read time" so users who never touched these fields do not accumulate
// noise in their config.json.
func DefaultState() State {
	return State{
		DataDir:            DataDir(),
		ImageTag:           "latest",
		Channel:            "stable",
		BackendPort:        3001,
		WebPort:            3000,
		Sandbox:            true,
		DockerSockGID:      -1,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
		NatsClientPort:     3003,
		PostgresPort:       3002,
		EncryptSecrets:     true,
	}
}

// DisplayChannel returns the channel for display, defaulting to "stable" when empty.
func (s State) DisplayChannel() string {
	if s.Channel == "" {
		return "stable"
	}
	return s.Channel
}

// ChangelogViewOrDefault returns the configured changelog view for the
// `synthorg update` walk, defaulting to "highlights" when empty or unknown.
// "highlights" -> AI summary block (per stable release); "commits" -> the
// commit-based changelog generated by Release Please.
func (s State) ChangelogViewOrDefault() string {
	if s.ChangelogView == "commits" {
		return "commits"
	}
	return "highlights"
}

// StatePath returns the path to the config file inside the data directory.
func StatePath(dataDir string) string {
	return filepath.Join(dataDir, stateFileName)
}

// Load reads State from disk. Returns a default state with the given dataDir
// if the file does not exist (so --data-dir is respected on bootstrap).
func Load(dataDir string) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	path := StatePath(safeDir)
	data, err := os.ReadFile(path) //nolint:gosec // path validated by SecurePath
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			defaults := DefaultState()
			defaults.DataDir = safeDir
			// Conservative fallback: sandbox requires explicit user confirmation
			// via `synthorg init`, so disable it when no config file exists.
			defaults.Sandbox = false
			return defaults, nil
		}
		return State{}, fmt.Errorf("reading config %s: %w", path, err)
	}
	// Unmarshal onto defaults so missing fields retain default values.
	s := DefaultState()
	if err := json.Unmarshal(data, &s); err != nil {
		return State{}, fmt.Errorf("parsing config %s: %w", path, err)
	}
	if err := s.validate(); err != nil {
		return State{}, fmt.Errorf("config %s: %w", path, err)
	}
	// Canonicalize and validate DataDir.
	if s.DataDir != "" {
		safeLoaded, err := SecurePath(s.DataDir)
		if err != nil {
			return State{}, fmt.Errorf("data_dir: %w", err)
		}
		s.DataDir = safeLoaded
	} else {
		// Config file omitted data_dir; fall back to the directory we loaded from.
		s.DataDir = safeDir
	}
	return s, nil
}

var validPersistenceBackends = map[string]bool{"sqlite": true, "postgres": true}
var validMemoryBackends = map[string]bool{"mem0": true}
var validBusBackends = map[string]bool{"internal": true, "nats": true}
var validChannels = map[string]bool{"stable": true, "dev": true}
var validLogLevels = map[string]bool{"debug": true, "info": true, "warn": true, "error": true}
var validColorModes = map[string]bool{"always": true, "auto": true, "never": true}
var validOutputModes = map[string]bool{"text": true, "json": true}
var validTimestampModes = map[string]bool{"relative": true, "iso8601": true}
var validHintsModes = map[string]bool{"always": true, "auto": true, "never": true}
var validChangelogViews = map[string]bool{"highlights": true, "commits": true}

// IsValidChannel reports whether name is a known update channel.
func IsValidChannel(name string) bool {
	return validChannels[name]
}

// ChannelNames returns the allowed channel names.
func ChannelNames() string { return sortedKeys(validChannels) }

// IsValidChangelogView reports whether name is a known changelog view mode
// for the `synthorg update` walk.
func IsValidChangelogView(name string) bool { return validChangelogViews[name] }

// ChangelogViewNames returns the allowed changelog view names.
func ChangelogViewNames() string { return sortedKeys(validChangelogViews) }

// IsValidLogLevel reports whether name is a known log level.
func IsValidLogLevel(name string) bool {
	return validLogLevels[name]
}

// LogLevelNames returns the allowed log level names.
func LogLevelNames() string { return sortedKeys(validLogLevels) }

// sortedKeys returns a comma-separated sorted list of map keys.
func sortedKeys(m map[string]bool) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return strings.Join(keys, ", ")
}

// IsValidBool reports whether value is a strict boolean string ("true" or "false").
func IsValidBool(value string) bool {
	return value == "true" || value == "false"
}

// BoolNames returns the allowed boolean values.
func BoolNames() string { return "true, false" }

// IsValidPersistenceBackend reports whether name is a known persistence backend.
func IsValidPersistenceBackend(name string) bool {
	return validPersistenceBackends[name]
}

// IsValidMemoryBackend reports whether name is a known memory backend.
func IsValidMemoryBackend(name string) bool {
	return validMemoryBackends[name]
}

// IsValidBusBackend reports whether name is a known message bus backend.
func IsValidBusBackend(name string) bool {
	return validBusBackends[name]
}

// PersistenceBackendNames returns the allowed persistence backend names.
func PersistenceBackendNames() string { return sortedKeys(validPersistenceBackends) }

// MemoryBackendNames returns the allowed memory backend names.
func MemoryBackendNames() string { return sortedKeys(validMemoryBackends) }

// BusBackendNames returns the allowed bus backend names.
func BusBackendNames() string { return sortedKeys(validBusBackends) }

// IsValidColorMode reports whether name is a known color mode.
func IsValidColorMode(name string) bool { return validColorModes[name] }

// ColorModeNames returns the allowed color mode names.
func ColorModeNames() string { return sortedKeys(validColorModes) }

// IsValidOutputMode reports whether name is a known output mode.
func IsValidOutputMode(name string) bool { return validOutputModes[name] }

// OutputModeNames returns the allowed output mode names.
func OutputModeNames() string { return sortedKeys(validOutputModes) }

// IsValidTimestampMode reports whether name is a known timestamp mode.
func IsValidTimestampMode(name string) bool { return validTimestampModes[name] }

// TimestampModeNames returns the allowed timestamp mode names.
func TimestampModeNames() string { return sortedKeys(validTimestampModes) }

// IsValidHintsMode reports whether name is a known hints mode.
func IsValidHintsMode(name string) bool { return validHintsModes[name] }

// HintsModeNames returns the allowed hints mode names.
func HintsModeNames() string { return sortedKeys(validHintsModes) }

// validate checks that loaded config values are within safe ranges.
func (s State) validate() error {
	if s.BackendPort < 1 || s.BackendPort > 65535 {
		return fmt.Errorf("invalid backend_port %d: must be 1-65535", s.BackendPort)
	}
	if s.WebPort < 1 || s.WebPort > 65535 {
		return fmt.Errorf("invalid web_port %d: must be 1-65535", s.WebPort)
	}
	if !IsValidPersistenceBackend(s.PersistenceBackend) {
		return fmt.Errorf("invalid persistence_backend %q: must be one of %s", s.PersistenceBackend, sortedKeys(validPersistenceBackends))
	}
	if !IsValidMemoryBackend(s.MemoryBackend) {
		return fmt.Errorf("invalid memory_backend %q: must be one of %s", s.MemoryBackend, sortedKeys(validMemoryBackends))
	}
	if s.BusBackend != "" && !IsValidBusBackend(s.BusBackend) {
		return fmt.Errorf("invalid bus_backend %q: must be one of %s", s.BusBackend, sortedKeys(validBusBackends))
	}
	if s.NatsClientPort != 0 && (s.NatsClientPort < 1 || s.NatsClientPort > 65535) {
		return fmt.Errorf("invalid nats_client_port %d: must be 1-65535", s.NatsClientPort)
	}
	if s.DockerSockGID < -1 || s.DockerSockGID > 4294967295 {
		return fmt.Errorf("invalid docker_sock_gid %d: must be -1 to 4294967295", s.DockerSockGID)
	}
	if s.Channel != "" && !IsValidChannel(s.Channel) {
		return fmt.Errorf("invalid channel %q: must be one of %s", s.Channel, sortedKeys(validChannels))
	}
	if s.LogLevel != "" && !IsValidLogLevel(s.LogLevel) {
		return fmt.Errorf("invalid log_level %q: must be one of %s", s.LogLevel, sortedKeys(validLogLevels))
	}
	if s.ImageTag != "" && !IsValidImageTag(s.ImageTag) {
		return fmt.Errorf("invalid image_tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", s.ImageTag)
	}
	if s.Color != "" && !IsValidColorMode(s.Color) {
		return fmt.Errorf("invalid color %q: must be one of %s", s.Color, ColorModeNames())
	}
	if s.Output != "" && !IsValidOutputMode(s.Output) {
		return fmt.Errorf("invalid output %q: must be one of %s", s.Output, OutputModeNames())
	}
	if s.Timestamps != "" && !IsValidTimestampMode(s.Timestamps) {
		return fmt.Errorf("invalid timestamps %q: must be one of %s", s.Timestamps, TimestampModeNames())
	}
	if s.Hints != "" && !IsValidHintsMode(s.Hints) {
		return fmt.Errorf("invalid hints %q: must be one of %s", s.Hints, HintsModeNames())
	}
	if s.ChangelogView != "" && !IsValidChangelogView(s.ChangelogView) {
		return fmt.Errorf("invalid changelog_view %q: must be one of %s", s.ChangelogView, ChangelogViewNames())
	}
	if s.PersistenceBackend == "postgres" {
		if s.PostgresPort < 1 || s.PostgresPort > 65535 {
			return fmt.Errorf("invalid postgres_port %d: must be 1-65535", s.PostgresPort)
		}
		if strings.TrimSpace(s.PostgresPassword) == "" {
			return fmt.Errorf("postgres_password is required when persistence_backend is postgres")
		}
		if len(s.PostgresPassword) < 32 {
			return fmt.Errorf("postgres_password must be at least 32 characters, got %d", len(s.PostgresPassword))
		}
		// Reject NUL/CR/LF/TAB. The password is interpolated into the
		// Postgres DSN, written to the compose.yml env block, and
		// forwarded to docker -- a stray newline could split the DSN or
		// produce a YAML value that deserializes to something else.
		if strings.ContainsAny(s.PostgresPassword, "\x00\n\r\t") {
			return fmt.Errorf("postgres_password must not contain control characters (NUL, CR, LF, TAB)")
		}
	}
	if s.EncryptSecrets && strings.TrimSpace(s.MasterKey) != "" {
		if err := validateFernetKey(s.MasterKey); err != nil {
			return fmt.Errorf("invalid master_key: %w", err)
		}
	}
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
		// Empty permitted for forward compat with pre-split configs;
		// resolved to "gpu" at read time via FineTuneVariantOrDefault.
	default:
		return fmt.Errorf("fine_tuning_variant must be %q or %q, got %q", FineTuneVariantGPU, FineTuneVariantCPU, s.FineTuningVariant)
	}
	for name, digest := range s.VerifiedDigests {
		if !isValidDigestFormat(digest) {
			return fmt.Errorf("invalid verified_digests[%q]: %q is not a valid sha256 digest", name, digest)
		}
	}
	if err := s.validateTunables(); err != nil {
		return err
	}
	return nil
}

// Validate runs State invariants (cross-field constraints such as
// fine_tuning requires sandbox, variant must be gpu|cpu, valid JWT /
// master-key formats) and returns the first failure. Callers that mutate
// State outside of Load (e.g. `synthorg config set` when toggling a
// previously-off feature) should invoke this so inconsistent combinations
// fail at `config set` time rather than at the next `start`.
func (s State) Validate() error {
	return s.validate()
}

// FineTuneVariantOrDefault returns the configured fine-tune variant,
// falling back to "gpu" when unset. Callers that need to build image
// refs or service names should always route through this accessor so
// the default is consistent across start / update / diagnostics paths.
func (s State) FineTuneVariantOrDefault() string {
	if s.FineTuningVariant == FineTuneVariantCPU {
		return FineTuneVariantCPU
	}
	return FineTuneVariantGPU
}

// FineTuneVariantFromIndex maps the TUI's integer variant index to the
// string persisted in State.FineTuningVariant. 0 -> "gpu" (default),
// 1 -> "cpu"; any other index falls back to "gpu" rather than writing
// an invalid value.
func FineTuneVariantFromIndex(idx int) string {
	if idx == 1 {
		return FineTuneVariantCPU
	}
	return FineTuneVariantGPU
}

// validateTunables checks that the optional registry/tunable fields parse
// and fall within sane ranges. Empty fields are treated as "use default"
// and skipped.
func (s State) validateTunables() error {
	if s.RegistryHost != "" && !IsValidRegistryHost(s.RegistryHost) {
		return fmt.Errorf("invalid registry_host %q: must be a DNS hostname (optionally with :port)", s.RegistryHost)
	}
	if s.DHIRegistry != "" && !IsValidRegistryHost(s.DHIRegistry) {
		return fmt.Errorf("invalid dhi_registry %q: must be a DNS hostname (optionally with :port)", s.DHIRegistry)
	}
	if s.ImageRepoPrefix != "" && !IsValidImageRepoPrefix(s.ImageRepoPrefix) {
		return fmt.Errorf("invalid image_repo_prefix %q: must match [a-z0-9][a-z0-9._/-]*", s.ImageRepoPrefix)
	}
	if s.PostgresImageTag != "" && !IsValidImageTag(s.PostgresImageTag) {
		return fmt.Errorf("invalid postgres_image_tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", s.PostgresImageTag)
	}
	if s.NATSImageTag != "" && !IsValidImageTag(s.NATSImageTag) {
		return fmt.Errorf("invalid nats_image_tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", s.NATSImageTag)
	}
	if s.DefaultNATSURL != "" {
		if err := ValidateNATSURL(s.DefaultNATSURL); err != nil {
			return fmt.Errorf("invalid default_nats_url: %w", err)
		}
	}
	if s.DefaultNATSStreamPrefix != "" && !IsValidStreamPrefix(s.DefaultNATSStreamPrefix) {
		return fmt.Errorf("invalid default_nats_stream_prefix %q: must match [A-Z0-9][A-Z0-9_-]*", s.DefaultNATSStreamPrefix)
	}
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
		if d.value == "" {
			continue
		}
		parsed, err := time.ParseDuration(d.value)
		if err != nil {
			return fmt.Errorf("invalid %s %q: %w", d.name, d.value, err)
		}
		if parsed <= 0 {
			return fmt.Errorf("invalid %s %q: must be > 0", d.name, d.value)
		}
		// image_verify_timeout has an additional floor: shorter values
		// would bypass cosign/SLSA verification by silently timing out
		// before network I/O completes.  Catch it at state-load time so
		// a persisted config.json fails loudly here rather than deep
		// inside ResolveTunables on the next `start`.
		if d.name == "image_verify_timeout" && parsed < MinImageVerifyTimeout {
			return fmt.Errorf(
				"invalid %s %q: %v is below the %v minimum floor; a shorter timeout would bypass cosign/SLSA verification by silently timing out",
				d.name, d.value, parsed, MinImageVerifyTimeout,
			)
		}
	}
	if s.ImagePullAttempts != "" {
		n, err := strconv.Atoi(s.ImagePullAttempts)
		if err != nil {
			return fmt.Errorf("invalid image_pull_attempts %q: %w", s.ImagePullAttempts, err)
		}
		if n < 1 || n > MaxImagePullAttempts {
			return fmt.Errorf("invalid image_pull_attempts %q: must be in [1, %d]", s.ImagePullAttempts, MaxImagePullAttempts)
		}
	}
	bytes := []struct {
		name  string
		value int64
	}{
		{"max_api_response_bytes", s.MaxAPIResponseBytes},
		{"max_binary_bytes", s.MaxBinaryBytes},
		{"max_archive_entry_bytes", s.MaxArchiveEntryBytes},
	}
	for _, b := range bytes {
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

var (
	// registryHostRegex matches a DNS hostname (letters/digits/dots/hyphens)
	// with an optional port suffix. Intentionally permissive: we rely on the
	// container runtime to reject genuinely malformed refs when it tries to
	// pull. We just want to catch obvious typos at config-set time.
	registryHostRegex = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9.\-]*(:[0-9]+)?$`)

	// imageRepoPrefixRegex matches a repository path prefix such as
	// "aureliolo/synthorg-". Trailing slash or dash is allowed.
	imageRepoPrefixRegex = regexp.MustCompile(`^[a-z0-9][a-z0-9._/\-]*$`)

	// streamPrefixRegex matches NATS JetStream stream name prefixes.
	streamPrefixRegex = regexp.MustCompile(`^[A-Z0-9][A-Z0-9_\-]*$`)
)

// IsValidRegistryHost reports whether host looks like a DNS hostname with
// an optional port. Length is capped at 253 characters (DNS limit).
func IsValidRegistryHost(host string) bool {
	if host == "" || len(host) > 253 {
		return false
	}
	if !registryHostRegex.MatchString(host) {
		return false
	}
	if i := strings.LastIndex(host, ":"); i >= 0 {
		port, err := strconv.Atoi(host[i+1:])
		if err != nil || port < 1 || port > 65535 {
			return false
		}
	}
	return true
}

// IsValidImageRepoPrefix reports whether prefix is a plausible Docker
// repository path prefix (lowercase alphanumerics plus ./-/_ and /).
func IsValidImageRepoPrefix(prefix string) bool {
	if prefix == "" || len(prefix) > 255 {
		return false
	}
	return imageRepoPrefixRegex.MatchString(prefix)
}

// IsValidStreamPrefix reports whether s is a valid NATS JetStream stream
// name prefix (uppercase ASCII + digits + _/-).
func IsValidStreamPrefix(s string) bool {
	if s == "" || len(s) > 64 {
		return false
	}
	return streamPrefixRegex.MatchString(s)
}

// ValidateNATSURL rejects obviously malformed NATS URLs. Mirrors the
// validation in cli/cmd/worker_start.go so the same rules apply whether
// the URL comes from a flag or from persisted config.
func ValidateNATSURL(raw string) error {
	if raw == "" {
		return fmt.Errorf("must not be empty")
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("parse: %w", err)
	}
	switch parsed.Scheme {
	case "nats", "tls", "nats+tls":
	default:
		return fmt.Errorf("scheme %q: must be nats://, tls://, or nats+tls://", parsed.Scheme)
	}
	if parsed.Hostname() == "" {
		return fmt.Errorf("missing host")
	}
	if rawPort := parsed.Port(); rawPort != "" {
		port, err := strconv.Atoi(rawPort)
		if err != nil {
			return fmt.Errorf("non-numeric port %q", rawPort)
		}
		if port < 1 || port > 65535 {
			return fmt.Errorf("port %d out of range (must be 1-65535)", port)
		}
	}
	return nil
}

// IsValidImageTag checks that tag matches [a-zA-Z0-9][a-zA-Z0-9._-]*
// and is at most 128 characters long (Docker tag length limit).
func IsValidImageTag(tag string) bool {
	if len(tag) == 0 || len(tag) > 128 {
		return false
	}
	first := tag[0]
	if !isAlphaNum(first) {
		return false
	}
	for i := 1; i < len(tag); i++ {
		c := tag[i]
		if !isAlphaNum(c) && c != '.' && c != '_' && c != '-' {
			return false
		}
	}
	return true
}

func isAlphaNum(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
}

// validateFernetKey verifies that key is a 44-character URL-safe base64
// string that decodes to exactly 32 bytes. Fernet keys that pass this
// check will round-trip through cryptography.fernet.Fernet without
// raising ValueError; a non-empty invalid key would otherwise sail
// through init, be injected as SYNTHORG_MASTER_KEY, and only fail
// when the backend constructs Fernet -- after the container has been
// restarted enough times to trip the restart-loop detector.
func validateFernetKey(key string) error {
	if len(key) != 44 {
		return fmt.Errorf("must be 44 characters (URL-safe base64 of 32 bytes), got %d", len(key))
	}
	raw, err := base64.URLEncoding.DecodeString(key)
	if err != nil {
		return fmt.Errorf("not valid URL-safe base64: %w", err)
	}
	if len(raw) != 32 {
		return fmt.Errorf("must decode to 32 bytes, got %d", len(raw))
	}
	return nil
}

// isValidDigestFormat checks if d matches sha256:<64-hex-chars>.
// Avoids importing the verify package to prevent circular dependencies.
func isValidDigestFormat(d string) bool {
	if len(d) != 71 || d[:7] != "sha256:" {
		return false
	}
	for _, c := range d[7:] {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

// Save writes State to disk as indented JSON.
// DataDir is normalized to the SecurePath-cleaned form before persisting.
func Save(s State) error {
	safeDir, err := SecurePath(s.DataDir)
	if err != nil {
		return fmt.Errorf("securing data dir: %w", err)
	}
	s.DataDir = safeDir // persist the canonical path
	if err := os.MkdirAll(safeDir, 0o700); err != nil {
		return fmt.Errorf("creating config directory: %w", err)
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return fmt.Errorf("marshaling config: %w", err)
	}
	return os.WriteFile(StatePath(safeDir), data, 0o600) //nolint:gosec // path validated by SecurePath
}
