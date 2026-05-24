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

	if !trustTransferred(p) {
		autofillDHIPins(p)
	}
}

// trustTransferred reports whether ANY identity-bearing field differs
// from the compiled-in default. The trust path (SAN regex + pinned digest
// map) is bound to the entire default deployment, so a single override
// (including RegistryHost or ImageRepoPrefix, which don't feed the DHI
// keys directly) transfers trust to the operator and invalidates the
// pin. DisableDefaultDHIPins is set by ParamsFromState when
// tun.CustomRegistry, so a caller that builds Params by hand and sets
// only RegistryHost cannot accidentally inherit the pinned DHI refs.
func trustTransferred(p *Params) bool {
	return p.DisableDefaultDHIPins ||
		p.RegistryHost != config.DefaultRegistryHost ||
		p.ImageRepoPrefix != config.DefaultImageRepoPrefix ||
		p.DHIRegistry != config.DefaultDHIRegistry ||
		p.PostgresImageTag != config.DefaultPostgresImageTag ||
		p.NATSImageTag != config.DefaultNATSImageTag
}

// autofillDHIPins fills empty Postgres / NATS digest fields from the
// verify package's pinned-index map. Only called when trust has NOT been
// transferred; a transferred-trust deployment leaves these blank by
// design so verification stays disabled.
func autofillDHIPins(p *Params) {
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

// validateParams checks all template parameters for safe values.
// Per-section validators live in validate.go.
func validateParams(p Params) error {
	checks := []func(Params) error{
		validateImageRefs,
		validateRuntimeBasics,
		validateBackendChoices,
		validateDistributed,
		validatePostgresParams,
		validateSecrets,
		validateDigestPins,
	}
	for _, check := range checks {
		if err := check(p); err != nil {
			return err
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
