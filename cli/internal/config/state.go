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
	NATSClientPort     int               `json:"nats_client_port,omitempty"`
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
	HealthWaitTimeout      string `json:"health_wait_timeout,omitempty"`
	SelfUpdateHTTPTimeout  string `json:"self_update_http_timeout,omitempty"`
	SelfUpdateAPITimeout   string `json:"self_update_api_timeout,omitempty"`
	TUFFetchTimeout        string `json:"tuf_fetch_timeout,omitempty"`
	AttestationHTTPTimeout string `json:"attestation_http_timeout,omitempty"`
	ImageVerifyTimeout     string `json:"image_verify_timeout,omitempty"`
	ImagePullRetryDelay    string `json:"image_pull_retry_delay,omitempty"`
	HealthPollInterval     string `json:"health_poll_interval,omitempty"`
	HealthInitialDelay     string `json:"health_initial_delay,omitempty"`
	DHIVerifyTimeout       string `json:"dhi_verify_timeout,omitempty"`
	UpdateHealthTimeout    string `json:"update_health_timeout,omitempty"`
	CompletionProbeTimeout string `json:"completion_probe_timeout,omitempty"`
	DiagnosticsDialTimeout string `json:"diagnostics_dial_timeout,omitempty"`
	StatusDockerTimeout    string `json:"status_docker_timeout,omitempty"`

	// Integer strings parsed by strconv.Atoi. Empty = use compiled-in default.
	ImagePullAttempts string `json:"image_pull_attempts,omitempty"`

	// Download size ceilings in bytes. Zero = use compiled-in default.
	MaxAPIResponseBytes  int64 `json:"max_api_response_bytes,omitempty"`
	MaxBinaryBytes       int64 `json:"max_binary_bytes,omitempty"`
	MaxArchiveEntryBytes int64 `json:"max_archive_entry_bytes,omitempty"`

	// Coerced records enum fields whose persisted value this binary did
	// not recognise and replaced at load time (see Coerce). Deliberately
	// NOT persisted: it describes one load, not configuration, and the
	// operator's original value must survive on disk until something
	// deliberately rewrites the file.
	Coerced []Coercion `json:"-"`
}

// Compiled-in default values for the tunables. Exposed so Tunables can detect
// customisation (CustomRegistry = any registry/tag field differs from default).
const (
	DefaultRegistryHost    = "ghcr.io"
	DefaultImageRepoPrefix = "aureliolo/synthorg-"
	DefaultDHIRegistry     = "dhi.io"
	// The pgvector variant of the hardened Postgres image: agent memory
	// stores embeddings in this database, so the vector extension must
	// ship with it. Same DHI family, so the hardened posture is kept.
	DefaultPostgresImageName = "pgvector"
	// renovate: datasource=docker depName=dhi.io/pgvector
	DefaultPostgresImageTag    = "0.8-pg18-debian13"
	DefaultPostgresImageDigest = "sha256:374f7b2b39fd75d559013f44dd24781d187686c6ea708dc1c8f54c7fae05f958"
	// renovate: datasource=docker depName=dhi.io/nats
	DefaultNATSImageTag    = "2.14-debian13"
	DefaultNATSImageDigest = "sha256:c3ea257c0fb9b96d3693c65c364c2a226f03e805dede8a914eb893ed2d6c2ea9"

	DefaultNATSURLValue          = "nats://nats:4222"
	DefaultNATSStreamPrefixValue = "SYNTHORG"

	DefaultBackupCreateTimeout    = 60 * time.Second
	DefaultBackupRestoreTimeout   = 30 * time.Second
	DefaultBackupListTimeout      = 10 * time.Second
	DefaultHealthCheckTimeout     = 5 * time.Second
	DefaultHealthWaitTimeout      = 90 * time.Second
	DefaultSelfUpdateHTTPTimeout  = 5 * time.Minute
	DefaultSelfUpdateAPITimeout   = 30 * time.Second
	DefaultTUFFetchTimeout        = 30 * time.Second
	DefaultAttestationHTTPTimeout = 30 * time.Second
	DefaultImageVerifyTimeout     = 120 * time.Second
	DefaultImagePullRetryDelay    = 2 * time.Second
	// Health-readiness poll cadence shared by the start paths:
	// HealthPollInterval trades responsiveness against backend load (the
	// /readyz endpoint is cheap, faster polling only shaves sub-second
	// latency); HealthInitialDelay skips the first few seconds where a
	// cold compose-up has not bound /readyz yet.
	DefaultHealthPollInterval = 2 * time.Second
	DefaultHealthInitialDelay = 5 * time.Second
	// DHIVerifyTimeout caps DHI cosign + SLSA verification per batch; a
	// stall past two minutes signals a network / transparency-log outage
	// rather than a slow CDN.
	DefaultDHIVerifyTimeout = 120 * time.Second
	// UpdateHealthTimeout bounds the Docker API calls the update flow makes
	// to check the current install; kept short so an unresponsive daemon
	// does not block the update.
	DefaultUpdateHealthTimeout = 15 * time.Second
	// CompletionProbeTimeout bounds the one-shot shell-profile probe run by
	// `synthorg completion install`.
	DefaultCompletionProbeTimeout = 5 * time.Second
	// DiagnosticsDialTimeout bounds each per-port TCP dial in the doctor
	// port-reachability check.
	DefaultDiagnosticsDialTimeout = 1 * time.Second
	// StatusDockerTimeout bounds the Docker API calls `synthorg status`
	// makes for the resource-usage and Postgres-volume sections; kept
	// short so an unresponsive daemon does not hang the status command,
	// mirroring UpdateHealthTimeout's rationale for the same class of
	// local Docker query.
	DefaultStatusDockerTimeout = 15 * time.Second
	DefaultImagePullAttempts   = 3

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

	// MinBinaryBytes / MinArchiveEntryBytes are hard floors on the
	// self-update download caps. They are a floor, not an expected value:
	// the real synthorg binary (and the single binary entry inside its
	// release archive) is tens of MiB, so an over-tight operator override
	// below 1 MiB could only ever truncate a legitimate download mid-stream
	// and fail the update open. 1 MiB rejects the pathological override
	// while leaving operators ample room to tighten within reason.
	MinBinaryBytes       int64 = 1 * 1024 * 1024
	MinArchiveEntryBytes int64 = 1 * 1024 * 1024

	// DefaultHealthResponseLimit caps how many bytes are read from a
	// /readyz health response (and the equivalent diagnostic probe). The
	// payload is a small JSON verdict; 64 KiB is generous headroom while
	// still bounding a misbehaving or hostile endpoint. Shared by the
	// health, diagnostics, and status read paths so the cap is defined
	// once rather than duplicated as a bare 64*1024 literal at each site.
	DefaultHealthResponseLimit int64 = 64 * 1024

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
		MemoryBackend:      "sqlvector",
		BusBackend:         "internal",
		NATSClientPort:     3003,
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
//
// Enum values this binary no longer recognises are coerced to their
// defaults (see Coerce) BEFORE Validate runs, and reported on
// State.Coerced for the caller to warn about. Without that, dropping a
// value from an allowlist would make every command refuse to load the
// config -- including the ones that exist to repair it.
func Load(dataDir string) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	s, readErr := readState(safeDir)
	if readErr != nil {
		if errors.Is(readErr, os.ErrNotExist) {
			defaults := DefaultState()
			defaults.DataDir = safeDir
			// Conservative fallback: sandbox requires explicit user confirmation
			// via `synthorg init`, so disable it when no config file exists.
			defaults.Sandbox = false
			return defaults, nil
		}
		return State{}, readErr
	}
	s, coerced := Coerce(s)
	if err := s.Validate(); err != nil {
		// Report the coercions alongside the failure. Dropping them here
		// would leave an operator whose config has BOTH a stale enum and
		// an unrelated invariant breach with no hint that a second field
		// was also rewritten during the same load.
		if len(coerced) != 0 {
			return State{}, fmt.Errorf(
				"config %s: %w (also replaced unrecognised values: %s)",
				StatePath(safeDir), err, joinCoercions(coerced),
			)
		}
		return State{}, fmt.Errorf("config %s: %w", StatePath(safeDir), err)
	}
	s.Coerced = coerced
	return canonicaliseDataDir(s, safeDir)
}

// joinCoercions renders a coercion list for a single-line error message.
func joinCoercions(coerced []Coercion) string {
	rendered := make([]string, 0, len(coerced))
	for _, c := range coerced {
		rendered = append(rendered, c.String())
	}
	return strings.Join(rendered, "; ")
}

// LoadTolerant reads State for the commands that must stay usable on a
// config the strict loader refuses: `doctor`, which exists to diagnose the
// breakage, and the `config` inspection subcommands, which are how an
// operator repairs it by hand.
//
// It NEVER runs Validate, for the same reason LoadForTeardown does not: a
// command whose whole purpose is to report or repair a broken file cannot
// be gated on that file being valid. Coercion still runs, so the returned
// State is safe to render, and State.Coerced tells the caller what was
// substituted. The returned error is ADVISORY: it reports why the config
// could not be fully resolved, and callers MUST proceed regardless.
//
// Note this is NOT a way to run the stack on an invalid config. Only
// read-only inspection paths use it; `start` keeps the strict Load so a
// config that would bring up the wrong stack still fails closed.
func LoadTolerant(dataDir string) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	seeded := DefaultState()
	seeded.DataDir = safeDir
	s, readErr := readState(safeDir)
	if readErr != nil {
		if errors.Is(readErr, os.ErrNotExist) {
			seeded.Sandbox = false
			return seeded, nil
		}
		return seeded, readErr
	}
	s, coerced := Coerce(s)
	s.Coerced = coerced
	// Surface the validation failure as advisory so `doctor` can report
	// it, without letting it stop the command.
	advisory := s.Validate()
	resolved, dirErr := canonicaliseDataDir(s, safeDir)
	if dirErr != nil {
		// An unusable persisted data_dir must not stop a diagnostic
		// either: fall back to the caller-supplied dir and report it.
		s.DataDir = safeDir
		return s, dirErr
	}
	return resolved, advisory
}

// LoadForReinit reads State for the `synthorg init` re-init path. Like
// LoadForTeardown it NEVER runs Validate, for the same reason: re-init is
// about to overwrite the config wholesale, so validating the value it is
// replacing can only refuse the repair the command exists to perform. The
// only thing re-init takes from the old file is the secrets it must carry
// forward, and those parse fine whatever else is wrong.
//
// It differs from LoadForTeardown in exactly one way: a missing,
// unreadable, or unparseable file IS a hard error here. Teardown can
// delete an install it could not parse, but re-init cannot silently
// proceed without master_key / settings_key / cursor_secret /
// postgres_password -- doing so would orphan every stored ciphertext and
// lock the CLI out of an existing Postgres volume.
//
// A persisted data_dir that fails SecurePath is NOT fatal, matching
// teardown: init is about to overwrite that field with the caller's
// --data-dir anyway, so refusing over it would block the repair while the
// secrets sat readable on disk.
func LoadForReinit(dataDir string) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	s, err := readState(safeDir)
	if err != nil {
		return State{}, err
	}
	// Deliberately NO Validate and NO Coerce: every field is about to be
	// replaced by the answers this init run collected, so the only thing
	// that matters is that the secrets came through.
	resolved, dirErr := canonicaliseDataDir(s, safeDir)
	if dirErr != nil {
		s.DataDir = safeDir
		return s, nil
	}
	return resolved, nil
}

// LoadForTeardown reads State on a best-effort basis for destroy paths
// (`synthorg wipe` / `synthorg uninstall`). Unlike Load it NEVER runs
// Validate and never refuses on a missing, unreadable, or invalid config:
// a teardown command exists to delete everything, so it must parse what it
// can, ignore what it cannot, and still tear down the rest.
//
// The returned State is ALWAYS safe to drive teardown with: DataDir is
// canonicalised to the SecurePath form so safeStateDir can resolve a
// destination. The returned error is ADVISORY ONLY -- it signals that the
// on-disk config could not be read or parsed so the caller can warn, but
// callers MUST proceed with teardown regardless of it.
func LoadForTeardown(dataDir string) (State, error) {
	safeDir, err := SecurePath(dataDir)
	if err != nil {
		return State{}, err
	}
	// Seed with the resolved dir so a corrupt / absent config still leaves
	// safeStateDir somewhere to operate.
	seeded := State{DataDir: safeDir}
	path := StatePath(safeDir)
	data, readErr := os.ReadFile(path) //nolint:gosec // G304: path is the state file under the SecurePath-cleaned data dir
	if readErr != nil {
		if errors.Is(readErr, os.ErrNotExist) {
			// Uninitialised / orphan data dir: nothing persisted to read.
			return seeded, nil
		}
		return seeded, fmt.Errorf("%w %s: %w", ErrReading, path, readErr)
	}
	s := DefaultState()
	// DefaultState seeds DataDir with the platform default, but for teardown
	// targeting the persisted value must win and an OMITTED data_dir must fall
	// back to the caller-supplied dir, not the platform default. Clearing it
	// before unmarshal lets the absent-field branch below pick safeDir.
	s.DataDir = ""
	if err := json.Unmarshal(data, &s); err != nil {
		return seeded, fmt.Errorf("%w %s: %w", ErrParsing, path, err)
	}
	// Deliberately NO Validate: an out-of-range port, a missing master_key,
	// or any other invariant breach must not stop a destroy command.
	if s.DataDir != "" {
		safeLoaded, secErr := SecurePath(s.DataDir)
		if secErr != nil {
			rejectedDir := s.DataDir
			// A persisted data_dir we cannot secure (e.g. traversal) is
			// dropped in favour of the CLI-supplied dir so teardown still
			// has a target, but surface it as an advisory so the caller can
			// warn that the on-disk path was rejected.
			s.DataDir = safeDir
			return s, fmt.Errorf("persisted data_dir %q rejected (%w); using %s", rejectedDir, secErr, safeDir)
		}
		s.DataDir = safeLoaded
	} else {
		s.DataDir = safeDir
	}
	return s, nil
}

// readState reads and decodes the state file under safeDir, unmarshalling
// onto DefaultState so an omitted field keeps its default. A missing file
// surfaces as an ErrReading error wrapping os.ErrNotExist, so callers that
// treat "absent" as a valid bootstrap state can branch on errors.Is while
// callers that require the file get a hard failure.
func readState(safeDir string) (State, error) {
	path := StatePath(safeDir)
	data, err := os.ReadFile(path) //nolint:gosec // G304: path is the state file under the SecurePath-cleaned data dir
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			// A missing config is the normal pre-init state, not a
			// failure, and every caller re-inspects this with errors.Is
			// before deciding what it means. Returned unwrapped because
			// formatting a message here would allocate on the path taken
			// by every command run before `synthorg init`, for a string
			// nothing ever reads.
			return State{}, err
		}
		return State{}, fmt.Errorf("%w %s: %w", ErrReading, path, err)
	}
	s := DefaultState()
	if err := json.Unmarshal(data, &s); err != nil {
		return State{}, fmt.Errorf("%w %s: %w", ErrParsing, path, err)
	}
	return s, nil
}

// canonicaliseDataDir resolves the persisted data_dir to its SecurePath
// form, falling back to the directory the state was loaded from when the
// config omits the field.
func canonicaliseDataDir(s State, safeDir string) (State, error) {
	if s.DataDir == "" {
		s.DataDir = safeDir
		return s, nil
	}
	safeLoaded, err := SecurePath(s.DataDir)
	if err != nil {
		return State{}, fmt.Errorf("data_dir: %w", err)
	}
	s.DataDir = safeLoaded
	return s, nil
}

var validPersistenceBackends = map[string]bool{"sqlite": true, "postgres": true}
var validMemoryBackends = map[string]bool{
	"sqlvector": true,
	"composite": true,
	// Ephemeral keyword-only store: loses everything on restart and cannot
	// retrieve by meaning. Reachable as a deliberate operator opt-in only.
	"inmemory": true,
}
var validBusBackends = map[string]bool{"internal": true, "nats": true}
var validChannels = map[string]bool{"stable": true, "dev": true}
var validLogLevels = map[string]bool{"debug": true, "info": true, "warn": true, "error": true}
var validColorModes = map[string]bool{"always": true, "auto": true, "never": true}
var validOutputModes = map[string]bool{"text": true, "json": true}
var validTimestampModes = map[string]bool{"relative": true, "iso8601": true}
var validHintsModes = map[string]bool{"always": true, "auto": true, "never": true}
var validChangelogViews = map[string]bool{"highlights": true, "commits": true}
var validFineTuneVariants = map[string]bool{FineTuneVariantGPU: true, FineTuneVariantCPU: true}

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
	fineTuneVariantNamesCache    = sortedKeys(validFineTuneVariants)
)

// isValidFineTuneVariant reports whether name is a known fine-tune image
// variant. Unexported because the flag layer takes its variant from the
// TUI index rather than a free-form string; the coercion table is the
// only consumer.
func isValidFineTuneVariant(name string) bool { return validFineTuneVariants[name] }

// FineTuneVariantNames returns the allowed fine-tune variant names.
func FineTuneVariantNames() string { return fineTuneVariantNamesCache }

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
// invoked by Validate. Package-level so the slice header is allocated
// once at init rather than on every Validate call (Load is a hot path).
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
	// safeDir is the SecurePath-cleaned, absolute form of the user's own
	// data dir (--data-dir / SYNTHORG_DATA_DIR / config). A path-injection
	// flag here is accepted by design: this is a local single-user CLI with
	// no privilege boundary -- the only actor who can influence the path is
	// the user writing to their own install directory -- so no filesystem
	// containment is enforced.
	if err := os.WriteFile(StatePath(safeDir), data, 0o600); err != nil {
		return fmt.Errorf("writing config file: %w", err)
	}
	return nil
}
