package compose

import (
	"fmt"
	"sort"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
)

// Per-section validators called by validateParams. Each returns the first
// failure for its slice of the Params surface; validateParams runs them
// in order so the user sees the first problem in field order.

// validateImageRefs runs the image-identity validators. Third-party tags
// flow from Tunables (env/state) straight into the Postgres / NATS image
// references in compose.yml. ResolveTunables validates them at load
// time, but validateParams is the last gate before string interpolation
// so we re-check here for defense-in-depth: a caller who bypassed
// ResolveTunables (e.g. a test building Params by hand) must not be
// able to inject colons or semicolons into the generated YAML.
func validateImageRefs(p Params) error {
	if err := validateImageTags(p); err != nil {
		return err
	}
	if err := validateImageDigests(p); err != nil {
		return err
	}
	return validateRegistryRefs(p)
}

func validateImageTags(p Params) error {
	tags := []struct{ name, value string }{
		{"image", p.ImageTag},
		{"postgres image", p.PostgresImageTag},
		{"nats image", p.NATSImageTag},
	}
	for _, t := range tags {
		if !config.IsValidImageTag(t.value) {
			return fmt.Errorf("invalid %s tag %q", t.name, t.value)
		}
	}
	return nil
}

// validateImageDigests rejects malformed digest pins. Blank digests are
// the legitimate unpinned mode (custom registry / trust transfer) so
// only non-empty values are checked.
func validateImageDigests(p Params) error {
	digests := []struct{ name, value string }{
		{"postgres", p.PostgresDigest},
		{"nats", p.NATSDigest},
	}
	for _, d := range digests {
		if d.value != "" && !verify.IsValidDigest(d.value) {
			return fmt.Errorf("invalid %s digest %q: must be a sha256 digest", d.name, d.value)
		}
	}
	return nil
}

func validateRegistryRefs(p Params) error {
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
	return nil
}

func validateRuntimeBasics(p Params) error {
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
	return validateSandbox(p)
}

func validateSandbox(p Params) error {
	if !p.Sandbox {
		return nil
	}
	if p.DockerSock == "" {
		return fmt.Errorf("docker socket path must be set when sandbox is enabled")
	}
	if strings.ContainsAny(p.DockerSock, "\"'`$\n\r{}[]") {
		return fmt.Errorf("docker socket path %q contains unsafe characters", p.DockerSock)
	}
	// docker_sock_gid is a Linux GID: -1 disables the override, the upper
	// bound is uint32 max. Widen to int64 first so the untyped 4294967295
	// constant is representable where int is 32-bit.
	if gid := int64(p.DockerSockGID); gid < -1 || gid > 4294967295 {
		return fmt.Errorf("invalid docker socket gid %d: must be -1 to 4294967295", p.DockerSockGID)
	}
	return nil
}

func validateBackendChoices(p Params) error {
	if !config.IsValidPersistenceBackend(p.PersistenceBackend) {
		return fmt.Errorf("invalid persistence backend %q: must be one of %s", p.PersistenceBackend, config.PersistenceBackendNames())
	}
	if !config.IsValidMemoryBackend(p.MemoryBackend) {
		return fmt.Errorf("invalid memory backend %q: must be one of %s", p.MemoryBackend, config.MemoryBackendNames())
	}
	if p.BusBackend != "" && !config.IsValidBusBackend(p.BusBackend) {
		return fmt.Errorf("invalid bus backend %q: must be one of %s", p.BusBackend, config.BusBackendNames())
	}
	return nil
}

func validateDistributed(p Params) error {
	if !p.DistributedEnabled() {
		return nil
	}
	if p.NatsClientPort < 1 || p.NatsClientPort > 65535 {
		return fmt.Errorf("invalid nats client port %d: must be 1-65535", p.NatsClientPort)
	}
	if p.NatsClientPort == p.BackendPort || p.NatsClientPort == p.WebPort {
		return fmt.Errorf("nats client port %d collides with another service port", p.NatsClientPort)
	}
	return nil
}

func validatePostgresParams(p Params) error {
	if !p.PostgresEnabled() {
		return nil
	}
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
	return nil
}

// validateSecrets cross-validates the three secret fields across the
// permitted shapes:
//   - all empty: valid for development / testing (template omits every
//     secret env var and the backend stays unwired);
//   - all three set: the standard production layout that init.go generates
//     and the backend boot guard expects;
//   - cursor-only: valid when the operator wants the unconditional
//     pagination cursor secret (synthorg.api.app create_app refuses to
//     start without one) but has not yet wired JWT auth or encrypted
//     settings storage.
//
// What is NOT valid is a partially-configured production layout: JWT
// without SettingsKey, SettingsKey without JWT, or JWT/SettingsKey without
// a CursorSecret. Emitting that compose.yml would produce a boot loop on
// `synthorg start`. Generate trims these fields before calling
// validateParams, so truthiness here is equivalent to "operator supplied
// a non-blank value".
func validateSecrets(p Params) error {
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
	return nil
}

func validateDigestPins(p Params) error {
	// Sort keys so the returned error is deterministic when more than
	// one pin is malformed (range over a map is randomised in Go).
	keys := make([]string, 0, len(p.DigestPins))
	for name := range p.DigestPins {
		keys = append(keys, name)
	}
	sort.Strings(keys)
	for _, name := range keys {
		if !verify.IsValidDigest(p.DigestPins[name]) {
			return fmt.Errorf("invalid digest pin for %q: %q is not a valid sha256 digest", name, p.DigestPins[name])
		}
	}
	return nil
}
