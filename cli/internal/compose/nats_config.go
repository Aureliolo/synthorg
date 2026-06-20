package compose

import "fmt"

// NATS server config values. Kept as named constants (a single source of
// truth) rather than bare literals inside the config template below.
//
// The client / monitoring ports are NOT operator-tunable: they are the
// in-container listen ports the generated compose port mappings are bound to
// (host 3003 -> container NATSClientPort), so changing one here without the
// matching compose mapping would silently break connectivity. The payload cap
// is a genuine sizing knob documented in the rationale below.
const (
	// NATSClientPort is the in-container client listen port (compose maps
	// host 3003 to this).
	NATSClientPort = 4222
	// NATSHTTPPort is the in-container monitoring/HTTP port.
	NATSHTTPPort = 8222
	// NATSMaxPayload sizes NATS for SynthOrg's traffic: LLM agent outputs,
	// meeting transcripts, and large tool results routinely exceed NATS's
	// 1MB default. 16MB stays well under the 64MB hard ceiling and gives
	// ample headroom for transcript bundling.
	NATSMaxPayload = "16MB"
)

// natsConfigContent is the canonical NATS server config the CLI writes
// alongside compose.yml when the distributed bus mode is active. The
// rendered compose file references this via `configs.nats-config.file`.
//
// It is an unexported package `var` (not a `const`) only because
// `fmt.Sprintf` is not a constant expression; callers outside the package
// read it through the immutable [NATSConfig] getter so the rendered config
// cannot be reassigned at a distance.
//
// Settings rationale:
//   - `host: 0.0.0.0` so the broker accepts connections from other
//     services on the synthorg-net docker network.
//   - `jetstream.store_dir: /data` matches the synthorg-nats-data volume
//     mount, persisting JetStream state across container restarts.
var natsConfigContent = fmt.Sprintf(`host: 0.0.0.0
port: %d
http_port: %d
jetstream {
  store_dir: /data
}
max_payload: %s
`, NATSClientPort, NATSHTTPPort, NATSMaxPayload)

// NATSConfig returns the canonical NATS server config the CLI writes
// alongside compose.yml when the distributed bus mode is active.
func NATSConfig() string {
	return natsConfigContent
}

// NATSConfigFilename is the on-disk name for the NATS config file the
// CLI writes next to compose.yml. Kept as a package-level constant so
// init/update/start agree on the path without duplicating the string.
const NATSConfigFilename = "nats.conf"
