// Package compose generates Docker Compose YAML from an embedded template.
package compose

import (
	"bytes"
	_ "embed"
	"fmt"
	"net/url"
	"os"
	"strings"
	"text/template"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

//go:embed compose.yml.tmpl
var composeTmpl string

// Image tag and digest validation delegate to config.IsValidImageTag
// and verify.IsValidDigest so the rules (including the 128-char Docker
// limit) stay in a single place and cannot drift between the config
// load path and the compose render path.

// envSynthorgNATSURL is the shared env var that the CLI and the
// backend's “communication.nats_url“ setting both read. Centralised
// here so callers (resolveNATSURL, future generators) reference a
// single source of truth instead of repeating the string literal.
const envSynthorgNATSURL = "SYNTHORG_NATS_URL"

// allowedLogLevels restricts log level values to a known safe set.
var allowedLogLevels = map[string]bool{
	"debug": true,
	"info":  true,
	"warn":  true,
	"error": true,
}

// Params are the template parameters for compose generation.
type Params struct {
	CLIVersion         string
	ImageTag           string
	BackendPort        int
	WebPort            int
	NatsClientPort     int
	LogLevel           string
	JWTSecret          string
	SettingsKey        string
	CursorSecret       string // HMAC key for opaque pagination cursor tokens (>= 16 bytes)
	MasterKey          string // Fernet key for encrypted secret backend
	EncryptSecrets     bool   // whether to wire SYNTHORG_MASTER_KEY into backend
	Sandbox            bool
	DockerSock         string
	DockerSockGID      int // host GID owning DockerSock; -1 skips group_add
	PersistenceBackend string
	MemoryBackend      string
	BusBackend         string
	TelemetryOptIn     bool
	PostgresPort       int
	PostgresPassword   string
	DigestPins         map[string]string // image name suffix → digest (e.g. "backend" → "sha256:abc...")
	FineTuning         bool
	FineTuningVariant  string // "gpu" (default) or "cpu"; selects which fine-tune image the compose file references

	// Registry and image tag tunables resolved at generation time.
	// RegistryHost + ImageRepoPrefix form the prefix for the backend/web
	// images; DHIRegistry + Postgres/NATS tags name the third-party
	// services. PostgresDigest / NATSDigest are the pinned multi-arch
	// index digests when the default (trusted) DHI images are in use;
	// empty when custom registry/tags are in play (no known digest, so
	// the compose file renders repo:tag without a pin).
	RegistryHost     string
	ImageRepoPrefix  string
	DHIRegistry      string
	PostgresImageTag string
	NATSImageTag     string
	PostgresDigest   string
	NATSDigest       string
	NATSURL          string

	// DisableDefaultDHIPins, when true, tells applyComposeDefaults not
	// to autofill PostgresDigest / NATSDigest from the pinned-digest map
	// even if the DHI registry and tags still match the compiled-in
	// defaults. Required for the trust-transfer contract: any custom
	// registry/repo override invalidates the pin set, since the
	// verification path (SAN regex + DHI digest map) is bound to the
	// default targets. ParamsFromState sets this to tun.CustomRegistry.
	DisableDefaultDHIPins bool
}

// ParamsFromState creates Params from a persisted State. Tunable
// registry/tag fields are resolved via config.ResolveTunables so the
// compose output reflects both persisted state and env overrides. The
// pinned DHI digests are looked up only when the user stayed on default
// registry/tags (CustomRegistry=false); a custom deployment produces a
// digest-free reference and relies on SkipVerify instead.
//
// Returns an error when ResolveTunables rejects the input (invalid env
// or persisted state) so compose generation fails deterministically
// rather than silently emitting a compose.yml built from compiled-in
// defaults that masks the user's broken override.
func ParamsFromState(s config.State) (Params, error) {
	// The compose template only emits SYNTHORG_FINE_TUNE_IMAGE behind
	// `and .Sandbox .FineTuning`, so fine-tuning without sandbox renders a
	// half-configured backend (flag set, image env missing). State.Validate
	// enforces the coupling at load time; repeat the check here so callers
	// that construct State directly (tests, in-memory mutation) fail fast
	// instead of producing a broken compose.yml.
	if s.FineTuning && !s.Sandbox {
		return Params{}, fmt.Errorf("fine_tuning requires sandbox to be enabled")
	}

	busBackend := s.BusBackend
	if busBackend == "" {
		busBackend = "internal"
	}
	natsPort := s.NatsClientPort
	if natsPort == 0 {
		natsPort = 3003
	}

	tun, err := config.ResolveTunables(s)
	if err != nil {
		return Params{}, fmt.Errorf("resolving tunables: %w", err)
	}

	// Only honour cached pins when we are still on the canonical default
	// deployment. For a custom registry/repo/tag, the trust path (SAN
	// regex + pinned digest map + verified_digests cache) is bound to
	// the defaults, so any cached pin refers to a DIFFERENT image than
	// the one we are about to render -- emitting it would produce
	// `newregistry/newprefix-backend@sha256:OLD_DEFAULT_DIGEST`, which
	// either 404s at pull time or pulls a mismatched image. Null out
	// both the SynthOrg (DigestPins) and DHI (PostgresDigest/
	// NATSDigest) pins in that case and let Generate render repo:tag
	// references under SkipVerify.
	var pgDigest, natsDigest string
	var digestPins map[string]string
	if !tun.CustomRegistry {
		pgKey := tun.DHIRegistry + "/postgres:" + tun.PostgresImageTag
		natsKey := tun.DHIRegistry + "/nats:" + tun.NATSImageTag
		if d, ok := verify.DHIPinnedIndexDigest(pgKey); ok {
			pgDigest = d
		}
		if d, ok := verify.DHIPinnedIndexDigest(natsKey); ok {
			natsDigest = d
		}
		digestPins = s.VerifiedDigests
	}

	return Params{
		CLIVersion:            version.Version,
		ImageTag:              s.ImageTag,
		BackendPort:           s.BackendPort,
		WebPort:               s.WebPort,
		NatsClientPort:        natsPort,
		LogLevel:              s.LogLevel,
		JWTSecret:             s.JWTSecret,
		SettingsKey:           s.SettingsKey,
		CursorSecret:          s.CursorSecret,
		MasterKey:             s.MasterKey,
		EncryptSecrets:        s.EncryptSecrets,
		Sandbox:               s.Sandbox,
		DockerSock:            s.DockerSock,
		DockerSockGID:         s.DockerSockGID,
		PersistenceBackend:    s.PersistenceBackend,
		MemoryBackend:         s.MemoryBackend,
		BusBackend:            busBackend,
		TelemetryOptIn:        s.TelemetryOptIn,
		PostgresPort:          s.PostgresPort,
		PostgresPassword:      s.PostgresPassword,
		FineTuning:            s.FineTuning,
		FineTuningVariant:     s.FineTuneVariantOrDefault(),
		RegistryHost:          tun.RegistryHost,
		ImageRepoPrefix:       tun.ImageRepoPrefix,
		DHIRegistry:           tun.DHIRegistry,
		PostgresImageTag:      tun.PostgresImageTag,
		NATSImageTag:          tun.NATSImageTag,
		PostgresDigest:        pgDigest,
		NATSDigest:            natsDigest,
		NATSURL:               resolveNATSURL(),
		DisableDefaultDHIPins: tun.CustomRegistry,
		DigestPins:            digestPins,
	}, nil
}

// resolveNATSURL returns the NATS URL embedded into the generated
// compose.yml backend env block. Reads “envSynthorgNATSURL“
// directly so the CLI and the backend's “communication.nats_url“
// setting share a single env var, falling back to the compiled-in
// default when the env var is unset or whitespace-only.
func resolveNATSURL() string {
	if v := strings.TrimSpace(os.Getenv(envSynthorgNATSURL)); v != "" {
		return v
	}
	return config.DefaultNATSURLValue
}

// PostgresEnabled reports whether the Postgres persistence backend is active.
func (p Params) PostgresEnabled() bool {
	return p.PersistenceBackend == "postgres"
}

// DistributedEnabled reports whether the distributed runtime profile is
// active (currently: bus_backend is anything other than "internal").
func (p Params) DistributedEnabled() bool {
	return p.BusBackend != "" && p.BusBackend != "internal"
}

// Generate renders the compose template with the given parameters.
// It validates all string parameters before rendering to prevent YAML injection.
//
// Params fields added for registry/tag configurability are populated
// with compiled-in defaults when the caller supplied empty strings, so
// existing callers that build a Params literal continue to produce the
// canonical SynthOrg compose output without having to name every new
// field.
func Generate(p Params) ([]byte, error) {
	applyComposeDefaults(&p)
	// Trim secret fields in place so a whitespace-padded operator value
	// (e.g. accidentally captured trailing newline) cannot pass the
	// boolean truthiness checks below and then leak into the rendered
	// env block as a value with embedded whitespace -- the backend would
	// either reject it or, worse, accept a subtly-different secret.
	p.JWTSecret = strings.TrimSpace(p.JWTSecret)
	p.SettingsKey = strings.TrimSpace(p.SettingsKey)
	p.CursorSecret = strings.TrimSpace(p.CursorSecret)
	p.MasterKey = strings.TrimSpace(p.MasterKey)
	if err := validateParams(p); err != nil {
		return nil, fmt.Errorf("validating params: %w", err)
	}

	funcMap := template.FuncMap{
		"yamlStr":            yamlStr,
		"digestPin":          digestPin(p.DigestPins),
		"sandboxImageRef":    sandboxImageRef(p.DigestPins),
		"sidecarImageRef":    sidecarImageRef(p.DigestPins),
		"fineTuneImageRef":   fineTuneImageRef(p.DigestPins, p.FineTuningVariant),
		"distributedEnabled": p.DistributedEnabled,
		"postgresEnabled":    p.PostgresEnabled,
		"pgDSN":              func() string { return pgDSN(p) },
	}

	tmpl, err := template.New("compose").Funcs(funcMap).Parse(composeTmpl)
	if err != nil {
		return nil, fmt.Errorf("parsing template: %w", err)
	}
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, p); err != nil {
		return nil, fmt.Errorf("executing template: %w", err)
	}
	return buf.Bytes(), nil
}

// applyComposeDefaults populates empty tunable fields with their
// compiled-in defaults and fills in the pinned DHI digests when the
// caller is running on the default registry/tags. The goal is to keep
// direct Params literals simple while still allowing callers (CLI
// commands building Params via ParamsFromState) to override any field.
func applyComposeDefaults(p *Params) {
	if p.RegistryHost == "" {
		p.RegistryHost = config.DefaultRegistryHost
	}
	if p.ImageRepoPrefix == "" {
		p.ImageRepoPrefix = config.DefaultImageRepoPrefix
	}
	if p.DHIRegistry == "" {
		p.DHIRegistry = config.DefaultDHIRegistry
	}
	if p.PostgresImageTag == "" {
		p.PostgresImageTag = config.DefaultPostgresImageTag
	}
	if p.NATSImageTag == "" {
		p.NATSImageTag = config.DefaultNATSImageTag
	}
	if p.NATSURL == "" {
		// Honour SYNTHORG_NATS_URL on the direct-Params build path so
		// callers that construct Params themselves (and never go
		// through ParamsFromState) see the same env override the
		// state-driven path does.
		p.NATSURL = resolveNATSURL()
	}

	// Autofill pinned digests ONLY when every registry/repo/tag field
	// still matches the compiled-in default. The trust path (SAN regex
	// + pinned digest map) is bound to the ENTIRE default deployment,
	// so any single overridden field -- including RegistryHost or
	// ImageRepoPrefix that don't even feed the DHI keys -- transfers
	// trust to the operator and invalidates the pin. We check all five
	// identity-bearing fields AND the explicit DisableDefaultDHIPins
	// flag (set by ParamsFromState when tun.CustomRegistry) so a caller
	// that builds Params by hand and sets only RegistryHost cannot
	// accidentally inherit the pinned DHI refs.
	trustTransferred := p.DisableDefaultDHIPins ||
		p.RegistryHost != config.DefaultRegistryHost ||
		p.ImageRepoPrefix != config.DefaultImageRepoPrefix ||
		p.DHIRegistry != config.DefaultDHIRegistry ||
		p.PostgresImageTag != config.DefaultPostgresImageTag ||
		p.NATSImageTag != config.DefaultNATSImageTag
	if !trustTransferred {
		if p.PostgresDigest == "" {
			pgKey := p.DHIRegistry + "/postgres:" + p.PostgresImageTag
			if d, ok := verify.DHIPinnedIndexDigest(pgKey); ok {
				p.PostgresDigest = d
			}
		}
		if p.NATSDigest == "" {
			natsKey := p.DHIRegistry + "/nats:" + p.NATSImageTag
			if d, ok := verify.DHIPinnedIndexDigest(natsKey); ok {
				p.NATSDigest = d
			}
		}
	}
}

// validateParams checks all template parameters for safe values.
func validateParams(p Params) error {
	if !config.IsValidImageTag(p.ImageTag) {
		return fmt.Errorf("invalid image tag %q", p.ImageTag)
	}
	// Third-party tags flow from Tunables (env/state) straight into the
	// Postgres/NATS image references in compose.yml. ResolveTunables
	// already validates them at load time, but validateParams is the
	// last gate before string interpolation so we re-check here for
	// defense-in-depth -- a caller who bypassed ResolveTunables (e.g. a
	// test that builds Params by hand) must not be able to inject
	// colons or semicolons into the generated YAML. Use the shared
	// config.IsValidImageTag which enforces the 128-char Docker tag
	// limit as well as the character class.
	if !config.IsValidImageTag(p.PostgresImageTag) {
		return fmt.Errorf("invalid postgres image tag %q", p.PostgresImageTag)
	}
	if !config.IsValidImageTag(p.NATSImageTag) {
		return fmt.Errorf("invalid nats image tag %q", p.NATSImageTag)
	}
	// Digest pins flow straight into @sha256:... in the rendered YAML.
	// Only validate when present -- a blank digest is the legitimate
	// unpinned mode (custom registry / trust transfer).
	if p.PostgresDigest != "" && !verify.IsValidDigest(p.PostgresDigest) {
		return fmt.Errorf("invalid postgres digest %q: must be a sha256 digest", p.PostgresDigest)
	}
	if p.NATSDigest != "" && !verify.IsValidDigest(p.NATSDigest) {
		return fmt.Errorf("invalid nats digest %q: must be a sha256 digest", p.NATSDigest)
	}
	// Registry hosts flow into the generated image reference prefix. A
	// malformed host (spaces, shell metacharacters) would produce a YAML
	// line that docker-compose rejects; reject early with a clearer error.
	if !config.IsValidRegistryHost(p.RegistryHost) {
		return fmt.Errorf("invalid registry host %q", p.RegistryHost)
	}
	if !config.IsValidRegistryHost(p.DHIRegistry) {
		return fmt.Errorf("invalid dhi registry %q", p.DHIRegistry)
	}
	if !config.IsValidImageRepoPrefix(p.ImageRepoPrefix) {
		return fmt.Errorf("invalid image repo prefix %q", p.ImageRepoPrefix)
	}
	if err := config.ValidateNATSURL(p.NATSURL); err != nil {
		return fmt.Errorf("invalid NATS URL %q: %w", p.NATSURL, err)
	}
	if p.LogLevel != "" && !allowedLogLevels[p.LogLevel] {
		return fmt.Errorf("invalid log level %q: must be one of debug, info, warn, error", p.LogLevel)
	}
	if p.BackendPort < 1 || p.BackendPort > 65535 {
		return fmt.Errorf("invalid backend port %d: must be 1-65535", p.BackendPort)
	}
	if p.WebPort < 1 || p.WebPort > 65535 {
		return fmt.Errorf("invalid web port %d: must be 1-65535", p.WebPort)
	}
	if p.BackendPort == p.WebPort {
		return fmt.Errorf("backend and web ports must be different (both set to %d)", p.BackendPort)
	}
	if p.Sandbox {
		if p.DockerSock == "" {
			return fmt.Errorf("docker socket path must be set when sandbox is enabled")
		}
		if strings.ContainsAny(p.DockerSock, "\"'`$\n\r{}[]") {
			return fmt.Errorf("docker socket path %q contains unsafe characters", p.DockerSock)
		}
		if p.DockerSockGID < -1 || p.DockerSockGID > 4294967295 {
			return fmt.Errorf("invalid docker socket gid %d: must be -1 to 4294967295", p.DockerSockGID)
		}
	}
	if !config.IsValidPersistenceBackend(p.PersistenceBackend) {
		return fmt.Errorf("invalid persistence backend %q: must be one of %s", p.PersistenceBackend, config.PersistenceBackendNames())
	}
	if !config.IsValidMemoryBackend(p.MemoryBackend) {
		return fmt.Errorf("invalid memory backend %q: must be one of %s", p.MemoryBackend, config.MemoryBackendNames())
	}
	if p.BusBackend != "" && !config.IsValidBusBackend(p.BusBackend) {
		return fmt.Errorf("invalid bus backend %q: must be one of %s", p.BusBackend, config.BusBackendNames())
	}
	if p.DistributedEnabled() {
		if p.NatsClientPort < 1 || p.NatsClientPort > 65535 {
			return fmt.Errorf("invalid nats client port %d: must be 1-65535", p.NatsClientPort)
		}
		if p.NatsClientPort == p.BackendPort || p.NatsClientPort == p.WebPort {
			return fmt.Errorf("nats client port %d collides with another service port", p.NatsClientPort)
		}
	}
	if p.PostgresEnabled() {
		if p.PostgresPort < 1 || p.PostgresPort > 65535 {
			return fmt.Errorf("invalid postgres port %d: must be 1-65535", p.PostgresPort)
		}
		if p.PostgresPort == p.BackendPort || p.PostgresPort == p.WebPort {
			return fmt.Errorf("postgres port %d collides with another service port", p.PostgresPort)
		}
		if p.DistributedEnabled() && p.PostgresPort == p.NatsClientPort {
			return fmt.Errorf("postgres port %d collides with nats client port %d", p.PostgresPort, p.NatsClientPort)
		}
		if strings.TrimSpace(p.PostgresPassword) == "" {
			return fmt.Errorf("postgres password is required when persistence backend is postgres")
		}
		if len(p.PostgresPassword) < 32 {
			return fmt.Errorf("postgres password must be >= 32 characters, got %d", len(p.PostgresPassword))
		}
	}
	// Cross-validate secrets across three permitted shapes:
	//   - all-empty: valid for development / testing (template omits every
	//     secret env var and the backend stays unwired);
	//   - all three set: the standard production layout that init.go
	//     generates and the backend boot guard expects;
	//   - cursor-only: valid when the operator wants the unconditional
	//     pagination cursor secret (synthorg.api.app create_app refuses
	//     to start without one) but has not yet wired JWT auth or
	//     encrypted settings storage.
	// What is NOT valid is a partially-configured production layout: JWT
	// without SettingsKey, SettingsKey without JWT, or JWT/SettingsKey
	// without a CursorSecret -- emitting that compose.yml would produce a
	// boot loop on ``synthorg start``.
	// Generate trims these fields before calling validateParams, so
	// truthiness here is equivalent to "operator supplied a non-blank
	// value" -- no second TrimSpace pass needed.
	hasJWT := p.JWTSecret != ""
	hasKey := p.SettingsKey != ""
	hasCursor := p.CursorSecret != ""
	if hasJWT && !hasKey {
		return fmt.Errorf("SYNTHORG_SETTINGS_KEY is required when JWT secret is set")
	}
	if hasKey && !hasJWT {
		return fmt.Errorf("JWT secret is required when SYNTHORG_SETTINGS_KEY is set")
	}
	if (hasJWT || hasKey) && !hasCursor {
		return fmt.Errorf("SYNTHORG_PAGINATION_CURSOR_SECRET is required when JWT/SettingsKey are set: backend refuses to start without it")
	}
	if hasCursor && len(p.CursorSecret) < 16 {
		return fmt.Errorf("SYNTHORG_PAGINATION_CURSOR_SECRET must be >= 16 bytes, got %d", len(p.CursorSecret))
	}
	for name, d := range p.DigestPins {
		if !verify.IsValidDigest(d) {
			return fmt.Errorf("invalid digest pin for %q: %q is not a valid sha256 digest", name, d)
		}
	}
	return nil
}

// pgDSN builds a properly percent-encoded PostgreSQL connection string.
// Uses url.UserPassword for userinfo encoding per RFC 3986 section 3.2.1.
//
// “postgres:5432“ is the docker-compose service DNS name plus the
// container-internal Postgres port -- both container-to-container,
// never exposed to the operator's host. The host-side port is a
// separate “Params.PostgresPort“ tunable (rendered in compose.yml.tmpl).
func pgDSN(p Params) string {
	if !p.PostgresEnabled() || p.PostgresPassword == "" {
		return ""
	}
	u := &url.URL{
		Scheme: "postgresql",
		User:   url.UserPassword("synthorg", p.PostgresPassword),
		Host:   "postgres:5432",
		Path:   "/synthorg",
	}
	return u.String()
}

// digestPin returns a template function that resolves an image name to either
// a digest-pinned reference (repo@digest) or a tag-based reference (repo:tag).
func digestPin(pins map[string]string) func(name, repo, tag string) string {
	return func(name, repo, tag string) string {
		if d, ok := pins[name]; ok && d != "" {
			return repo + "@" + d
		}
		return repo + ":" + tag
	}
}

// sandboxImageRef returns a template function that resolves the sandbox image
// to its digest-pinned or tag-based reference. Wired into the backend's
// SYNTHORG_SANDBOX_IMAGE env var so the backend and CLI stay version-locked
// when the backend spawns ephemeral sandbox containers via aiodocker.
func sandboxImageRef(pins map[string]string) func(tag string) string {
	return func(tag string) string {
		return verify.FormatImageRef("sandbox", tag, pins["sandbox"])
	}
}

// sidecarImageRef returns a template function that resolves the sidecar image
// to its digest-pinned or tag-based reference. Wired into the backend's
// SYNTHORG_SIDECAR_IMAGE env var so the backend creates version-locked
// sidecar proxy containers for sandbox network enforcement.
func sidecarImageRef(pins map[string]string) func(tag string) string {
	return func(tag string) string {
		return verify.FormatImageRef("sidecar", tag, pins["sidecar"])
	}
}

// fineTuneImageRef returns a template function that resolves the fine-tune
// image for the requested variant to its digest-pinned or tag-based
// reference. Wired into the backend's SYNTHORG_FINE_TUNE_IMAGE env var so
// the backend spawns version-locked fine-tuning pipeline containers.
//
// variant must be "gpu", "cpu", or empty (forward-compat shim that
// resolves to "gpu"); any other value produces a template function that
// fails rendering with a clear error instead of silently defaulting.
func fineTuneImageRef(pins map[string]string, variant string) func(tag string) string {
	if variant != "" && variant != config.FineTuneVariantGPU && variant != config.FineTuneVariantCPU {
		// Surface the misconfiguration at template render time. Going
		// through panic keeps the template signature simple (no error
		// return) while ensuring a typo in a hand-built Params fails
		// loudly instead of silently pulling the GPU image.
		return func(string) string {
			panic(fmt.Sprintf("fineTuneImageRef: invalid fine-tuning variant %q: must be %q or %q", variant, config.FineTuneVariantGPU, config.FineTuneVariantCPU))
		}
	}
	service := verify.FineTuneServiceName(variant)
	return func(tag string) string {
		return verify.FormatImageRef(service, tag, pins[service])
	}
}

// yamlStr safely quotes a string value for YAML, escaping special characters.
// Also escapes $ to prevent Docker Compose variable interpolation.
func yamlStr(s string) string {
	// If the string contains YAML-special or Compose-interpolation characters,
	// double-quote and escape.
	if strings.ContainsAny(s, "\x00$:#{}[]|>&*!%@`\"'\\\n\r\t") {
		escaped := strings.ReplaceAll(s, "\x00", "") // YAML cannot represent null bytes
		escaped = strings.ReplaceAll(escaped, `\`, `\\`)
		escaped = strings.ReplaceAll(escaped, `"`, `\"`)
		escaped = strings.ReplaceAll(escaped, "\n", `\n`)
		escaped = strings.ReplaceAll(escaped, "\r", `\r`)
		escaped = strings.ReplaceAll(escaped, "\t", `\t`)
		// Escape $ to prevent Docker Compose variable interpolation.
		escaped = strings.ReplaceAll(escaped, "$", "$$")
		return `"` + escaped + `"`
	}
	return `"` + s + `"`
}
