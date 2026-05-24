package cmd

import (
	"fmt"
	"strconv"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// This file hosts the 17 tunable config keys (registry hosts / image
// tags / NATS defaults / timeouts / byte limits) and their setter,
// getter, reset, and env-var mappings. Kept separate from config.go to
// keep either file under the 800-line soft limit -- config.go carries
// the cobra command definitions and the original key set, while this
// file carries the extensions added with the tunables feature.

// applyTunableConfigValue is the delegation target called from
// applyConfigValue for tunable keys. Returns (true, err) if the key was
// handled (regardless of success), (false, nil) if the key is not a
// tunable so the caller falls through to the default-case. Per-key
// specs live in tunableSpecs (config_tunables_dispatch.go).
func applyTunableConfigValue(state *config.State, key, value string) (bool, error) {
	spec, ok := tunableSpecs[key]
	if !ok {
		return false, nil
	}
	return true, spec.set(state, value)
}

// resetTunableConfigValue resets a tunable key to its zero value (empty
// string for durations and strings, 0 for byte sizes) so configGetValue
// falls back to the compiled-in default. Returns true when handled.
func resetTunableConfigValue(state *config.State, key string) bool {
	spec, ok := tunableSpecs[key]
	if !ok {
		return false
	}
	spec.reset(state)
	return true
}

// tunableConfigGetValue returns the display value for a tunable key,
// falling back to the compiled-in default when the state field is
// empty/zero. Returns (value, true) when handled.
func tunableConfigGetValue(state config.State, key string) (string, bool) {
	spec, ok := tunableSpecs[key]
	if !ok {
		return "", false
	}
	return spec.get(state), true
}

// tunableEnvVarForKey maps a tunable config key to its SYNTHORG_* env
// var name. Returns "" for non-tunable keys so the caller falls through.
func tunableEnvVarForKey(key string) string {
	if spec, ok := tunableSpecs[key]; ok {
		return spec.envVar
	}
	return ""
}

// setRegistryHost validates a DNS hostname (optionally with :port) and
// writes it into target. Empty values are rejected; use `config unset`
// to restore the default.
func setRegistryHost(value, key string, target *string) error {
	if !config.IsValidRegistryHost(value) {
		return fmt.Errorf("invalid %s %q: must be a DNS hostname (optionally with :port)", key, value)
	}
	*target = value
	return nil
}

// setImageRepoPrefix validates a repo path prefix and writes it into target.
func setImageRepoPrefix(value string, target *string) error {
	if !config.IsValidImageRepoPrefix(value) {
		return fmt.Errorf("invalid image_repo_prefix %q: must match [a-z0-9][a-z0-9._/-]*", value)
	}
	*target = value
	return nil
}

// setTag validates a Docker image tag and writes it into target.
func setTag(value, key string, target *string) error {
	if !config.IsValidImageTag(value) {
		return fmt.Errorf("invalid %s %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", key, value)
	}
	*target = value
	return nil
}

// setStreamPrefix validates a NATS JetStream stream prefix.
func setStreamPrefix(value string, target *string) error {
	if !config.IsValidStreamPrefix(value) {
		return fmt.Errorf("invalid default_nats_stream_prefix %q: must match [A-Z0-9][A-Z0-9_-]*", value)
	}
	*target = value
	return nil
}

// setDuration validates a time.ParseDuration string and writes it into
// target. The stored form is the normalized string (e.g. "30s") so
// config.json stays human-readable.
func setDuration(value, key string, target *string) error {
	d, err := time.ParseDuration(value)
	if err != nil {
		return fmt.Errorf("invalid %s %q: %w", key, value, err)
	}
	if d <= 0 {
		return fmt.Errorf("invalid %s %q: must be > 0", key, value)
	}
	*target = d.String()
	return nil
}

// setIntInRange parses value as a decimal integer, validates it lies
// in [minValue, maxValue], and writes the normalized decimal string
// into target. Stored as a string so config.json stays empty when the
// operator never sets the key -- matching the convention used for
// durations and byte sizes.
func setIntInRange(value, key string, minValue, maxValue int, target *string) error {
	n, err := strconv.Atoi(value)
	if err != nil {
		return fmt.Errorf("invalid %s %q: %w", key, value, err)
	}
	if n < minValue || n > maxValue {
		return fmt.Errorf("invalid %s %q: must be in [%d, %d]", key, value, minValue, maxValue)
	}
	*target = strconv.Itoa(n)
	return nil
}

// setByteSize parses a human-readable byte size (accepts IEC and SI
// suffixes) and writes the int64 result into target. Rejects zero,
// negative, and values exceeding the 1 GiB ceiling. ParseBytes already
// enforces the ceiling; the redundant check here is defence-in-depth
// against a future ParseBytes relaxation.
func setByteSize(value, key string, target *int64) error {
	n, err := config.ParseBytes(value)
	if err != nil {
		return fmt.Errorf("invalid %s %q: %w", key, value, err)
	}
	if n <= 0 {
		return fmt.Errorf("invalid %s %q: must be > 0", key, value)
	}
	if n > config.MaxBytesCeiling {
		return fmt.Errorf("invalid %s %q: exceeds 1 GiB ceiling", key, value)
	}
	*target = n
	return nil
}

// displayOrFallback returns value when non-empty, otherwise the fallback.
// Used by the tunable getters to print compiled-in defaults for unset
// string fields so `config get` never prints an empty line.
func displayOrFallback(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

// int64OrDefault returns value when positive, otherwise the fallback as
// a decimal string.
func int64OrDefault(value, fallback int64) string {
	if value <= 0 {
		return strconv.FormatInt(fallback, 10)
	}
	return strconv.FormatInt(value, 10)
}
