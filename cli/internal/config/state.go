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
	"sort"
	"strconv"
	"strings"
	"time"
)

const stateFileName = "config.json"

// Sentinel errors for Load failure modes, classified so callers can
// branch on shape (errors.Is) rather than on error.Error() prefix. The
// shapes are mutually exclusive: at most one wraps any given Load
// error.
var (
	// ErrReading is wrapped when the persisted config file exists but
	// cannot be read (filesystem permissions, I/O error, etc.).
	ErrReading = errors.New("reading config")
	// ErrParsing is wrapped when the config file is present and
	// readable but its bytes do not decode as valid JSON.
	ErrParsing = errors.New("parsing config")
)

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
	// VerifiedImageTag records the ImageTag value the SynthOrg pins in
	// VerifiedDigests were verified against. hasSynthOrgDigests treats the
	// SynthOrg cache as stale whenever this does not match the current
	// ImageTag, mirroring the strictness of the DHI pin-comparison check
	// (see hasDHIDigests in cli/cmd/start.go). DHI pins are validated
	// independently against the binary-baked Renovate map and do not use
	// this field.
	VerifiedImageTag string `json:"verified_image_tag,omitempty"`

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
	// An empty value resolves to "gpu" at read time so a config that omits
	// the field (older on-disk state or a hand-edit) still loads, but the
	// init flow always writes an explicit variant. The backend reads
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

	// Default value for the `synthorg worker start --stream-prefix` flag.
	// The NATS URL is no longer persisted as CLI config -- the worker
	// reads ``SYNTHORG_NATS_URL`` directly so the CLI and the backend's
	// ``communication.nats_url`` setting share a single env var.
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
	DefaultPostgresImageTag    = "18-debian13"
	DefaultPostgresImageDigest = "sha256:0e7e99976a6fe74dacc6669f8102cc79c0ca075549ee0df5add51d6c4bf9578d"
	// renovate: datasource=docker depName=dhi.io/nats
	DefaultNATSImageTag    = "2.14-debian13"
	DefaultNATSImageDigest = "sha256:081f7895b874bd1306e61bf631631ac47e227a1ea9c60bfd2551b9be2dec8370"

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

// ColorOrDefault returns the persisted color mode, or "auto" when empty.
// "auto" matches the runtime auto-detect (TTY + NO_COLOR + CLICOLOR
// inspection in GlobalOpts) that fires when Color is unset.
func (s State) ColorOrDefault() string {
	if s.Color == "" {
		return "auto"
	}
	return s.Color
}

// HintsOrDefault returns the persisted hints mode, or "auto" when empty.
// "auto" matches the runtime default (once-per-session for HintTip,
// suppressed for HintGuidance) applied when Hints is unset.
func (s State) HintsOrDefault() string {
	if s.Hints == "" {
		return "auto"
	}
	return s.Hints
}

// OutputOrDefault returns the persisted output mode, or "text" when empty.
func (s State) OutputOrDefault() string {
	if s.Output == "" {
		return "text"
	}
	return s.Output
}

// TimestampsOrDefault returns the persisted timestamp mode, or "relative"
// when empty (the canonical default rendered by the logs command when
// the operator has not opted into iso8601).
func (s State) TimestampsOrDefault() string {
	if s.Timestamps == "" {
		return "relative"
	}
	return s.Timestamps
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
	return loadWith(dataDir, State.Validate)
}

// LoadAllowMissingMasterKey is Load but runs ValidateAllowMissingMasterKey
// instead of Validate, so a legacy persisted config can be read even
// when EncryptSecrets is true and MasterKey is empty. Used by the init
// reinit flow to recover such installs; callers MUST regenerate or
// hand-provide a master_key before persisting the returned state back
// (the strict Validate runs again on the next normal Load).
func LoadAllowMissingMasterKey(dataDir string) (State, error) {
	return loadWith(dataDir, State.ValidateAllowMissingMasterKey)
}

// loadWith is the shared body of Load and LoadAllowMissingMasterKey.
// validate is the per-state validator the caller wants applied to the
// unmarshalled State; both wrappers pass a method value so the dispatch
// cost is a single function call rather than a per-call branch.
func loadWith(dataDir string, validate func(State) error) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	path := StatePath(safeDir)
	data, err := os.ReadFile(path) //nolint:gosec // G304: path is the state file under the SecurePath-cleaned data dir
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			defaults := DefaultState()
			defaults.DataDir = safeDir
			// Conservative fallback: sandbox requires explicit user confirmation
			// via `synthorg init`, so disable it when no config file exists.
			defaults.Sandbox = false
			return defaults, nil
		}
		return State{}, fmt.Errorf("%w %s: %w", ErrReading, path, err)
	}
	// Unmarshal onto defaults so missing fields retain default values.
	s := DefaultState()
	if err := json.Unmarshal(data, &s); err != nil {
		return State{}, fmt.Errorf("%w %s: %w", ErrParsing, path, err)
	}
	if err := validate(s); err != nil {
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

// Cached sortedKeys outputs for each enum map. sortedKeys allocates a
// keys slice + the joined string, so callers that hit it per Validate
// (e.g. the error-message lookups in validateBackends /
// validateDisplayModes) pay those allocs eagerly even on the happy
// path. The maps are package-level constants; their sorted-string form
// is too, so memoise once at init and serve every accessor from the
// cache. Restores LoadExisting to its pre-refactor alloc budget.
var (
	persistenceBackendNamesCache = sortedKeys(validPersistenceBackends)
	memoryBackendNamesCache      = sortedKeys(validMemoryBackends)
	busBackendNamesCache         = sortedKeys(validBusBackends)
	channelNamesCache            = sortedKeys(validChannels)
	logLevelNamesCache           = sortedKeys(validLogLevels)
	colorModeNamesCache          = sortedKeys(validColorModes)
	outputModeNamesCache         = sortedKeys(validOutputModes)
	timestampModeNamesCache      = sortedKeys(validTimestampModes)
	hintsModeNamesCache          = sortedKeys(validHintsModes)
	changelogViewNamesCache      = sortedKeys(validChangelogViews)
)

// IsValidChannel reports whether name is a known update channel.
func IsValidChannel(name string) bool {
	return validChannels[name]
}

// ChannelNames returns the allowed channel names.
func ChannelNames() string { return channelNamesCache }

// IsValidChangelogView reports whether name is a known changelog view mode
// for the `synthorg update` walk.
func IsValidChangelogView(name string) bool { return validChangelogViews[name] }

// ChangelogViewNames returns the allowed changelog view names.
func ChangelogViewNames() string { return changelogViewNamesCache }

// IsValidLogLevel reports whether name is a known log level.
func IsValidLogLevel(name string) bool {
	return validLogLevels[name]
}

// LogLevelNames returns the allowed log level names.
func LogLevelNames() string { return logLevelNamesCache }

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
func PersistenceBackendNames() string { return persistenceBackendNamesCache }

// MemoryBackendNames returns the allowed memory backend names.
func MemoryBackendNames() string { return memoryBackendNamesCache }

// BusBackendNames returns the allowed bus backend names.
func BusBackendNames() string { return busBackendNamesCache }

// IsValidColorMode reports whether name is a known color mode.
func IsValidColorMode(name string) bool { return validColorModes[name] }

// ColorModeNames returns the allowed color mode names.
func ColorModeNames() string { return colorModeNamesCache }

// IsValidOutputMode reports whether name is a known output mode.
func IsValidOutputMode(name string) bool { return validOutputModes[name] }

// OutputModeNames returns the allowed output mode names.
func OutputModeNames() string { return outputModeNamesCache }

// IsValidTimestampMode reports whether name is a known timestamp mode.
func IsValidTimestampMode(name string) bool { return validTimestampModes[name] }

// TimestampModeNames returns the allowed timestamp mode names.
func TimestampModeNames() string { return timestampModeNamesCache }

// IsValidHintsMode reports whether name is a known hints mode.
func IsValidHintsMode(name string) bool { return validHintsModes[name] }

// HintsModeNames returns the allowed hints mode names.
func HintsModeNames() string { return hintsModeNamesCache }

// stateValidations is the ordered list of per-section State validators
// invoked by both Validate and ValidateAllowMissingMasterKey.
// validateMasterKey is NOT in this slice; both wrappers call it (or
// skip it) separately so the migration-recovery path does not need
// pointer comparison or per-iteration skip logic to omit it.
// Package-level so the slice header is allocated once at init rather
// than on every Validate call (LoadExisting is a hot path).
var stateValidations = []func(State) error{
	validatePorts,
	validateBackends,
	validateDisplayModes,
	validatePostgres,
	validateFineTuning,
	validateVerifiedDigests,
}

// Validate runs State invariants (cross-field constraints such as
// fine_tuning requires sandbox, variant must be gpu|cpu, valid JWT /
// master-key formats) and returns the first failure. Callers that mutate
// State outside of Load (e.g. `synthorg config set` when toggling a
// previously-off feature) should invoke this so inconsistent combinations
// fail at `config set` time rather than at the next `start`. Load also
// runs Validate on every read.
func (s State) Validate() error {
	for _, check := range stateValidations {
		if err := check(s); err != nil {
			return err
		}
	}
	if err := validateMasterKey(s); err != nil {
		return err
	}
	return s.validateTunables()
}

// ValidateAllowMissingMasterKey is Validate but tolerates ONE specific
// failure -- ErrMissingMasterKey. Every other validateMasterKey error
// (e.g. a non-empty MasterKey that fails the Fernet format check) is
// still surfaced so a malformed key cannot leak through the recovery
// path. Used by LoadAllowMissingMasterKey (and ultimately by the init
// reinit flow) so a legacy persisted config can be read into memory
// even though it fails the strict invariant; the caller MUST
// regenerate or hand-provide a master_key before persisting the
// returned state back.
func (s State) ValidateAllowMissingMasterKey() error {
	for _, check := range stateValidations {
		if err := check(s); err != nil {
			return err
		}
	}
	if err := validateMasterKey(s); err != nil && !errors.Is(err, ErrMissingMasterKey) {
		return err
	}
	return s.validateTunables()
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

// tunablesValidations is the ordered list of per-section tunables
// validators. Package-level for the same reason as stateValidations:
// avoid a per-call slice header allocation on the LoadExisting hot path.
var tunablesValidations = []func(State) error{
	validateRegistryFields,
	validateDurationFields,
	validateIntegerFields,
	validateByteFields,
}

// validateTunables checks that the optional registry/tunable fields
// parse and fall within sane ranges. Empty fields are treated as "use
// default" and skipped. Per-section validators live in validate.go.
func (s State) validateTunables() error {
	for _, check := range tunablesValidations {
		if err := check(s); err != nil {
			return err
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
	data, err := json.MarshalIndent(s, "", "  ") //nolint:gosec // G117: State is the CLI's own config store; secret-bearing fields are intentionally persisted to the 0600 state file under the user's data dir
	if err != nil {
		return fmt.Errorf("marshaling config: %w", err)
	}
	return os.WriteFile(StatePath(safeDir), data, 0o600)
}
