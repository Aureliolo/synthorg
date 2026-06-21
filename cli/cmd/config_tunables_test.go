package cmd

import (
	"slices"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// tunableKeys is the set of keys introduced by the tunables feature. Every
// entry MUST be gettable + settable + unsettable, round-trip to config.State,
// and map to a SYNTHORG_* env var.
var tunableKeys = []struct {
	Key   string
	Value string // a value accepted by applyConfigValue
}{
	{"registry_host", "my.registry.example"},
	{"image_repo_prefix", "myorg/service-"},
	{"dhi_registry", "private.docker.example"},
	{"postgres_image_tag", "17-debian13"},
	{"nats_image_tag", "2.11-debian13"},
	{"default_nats_stream_prefix", "CUSTOM"},
	{"backup_create_timeout", "90s"},
	{"backup_restore_timeout", "45s"},
	{"health_check_timeout", "2s"},
	{"health_wait_timeout", "120s"},
	{"self_update_http_timeout", "10m"},
	{"self_update_api_timeout", "20s"},
	{"tuf_fetch_timeout", "15s"},
	{"attestation_http_timeout", "20s"},
	{"max_api_response_bytes", "2097152"},
	{"max_binary_bytes", "512MiB"},
	{"max_archive_entry_bytes", "64MiB"},
}

func TestTunableKeys_AllRegistered(t *testing.T) {
	for _, tk := range tunableKeys {
		t.Run(tk.Key, func(t *testing.T) {
			if !slices.Contains(gettableConfigKeys, tk.Key) {
				t.Errorf("%s missing from gettableConfigKeys", tk.Key)
			}
			if !slices.Contains(supportedConfigKeys, tk.Key) {
				t.Errorf("%s missing from supportedConfigKeys", tk.Key)
			}
			if envVarForKey(tk.Key) == "" {
				t.Errorf("%s has no env var mapping", tk.Key)
			}
		})
	}
}

func TestTunableKeys_SetUnsetRoundTrip(t *testing.T) {
	for _, tk := range tunableKeys {
		t.Run(tk.Key, func(t *testing.T) {
			state := config.DefaultState()
			state.EncryptSecrets = false

			if err := applyConfigValue(&state, tk.Key, tk.Value); err != nil {
				t.Fatalf("applyConfigValue(%s, %q): %v", tk.Key, tk.Value, err)
			}
			got := configGetValue(state, tk.Key)
			if got == "" {
				t.Errorf("configGetValue after set returned empty string")
			}

			if err := resetConfigValue(&state, tk.Key); err != nil {
				t.Fatalf("resetConfigValue(%s): %v", tk.Key, err)
			}
			// After reset, get returns the compiled-in default, not the
			// user-supplied value. Every entry in tunableKeys deliberately
			// uses a test value that differs from the compiled-in default
			// (verified in the tunableKeys fixture), so equality here can
			// only mean `resetConfigValue` failed to clear state. No
			// exclusions: if a future test value drifts to equal the
			// default, this will flag the fixture rather than silently
			// lose coverage.
			after := configGetValue(state, tk.Key)
			if after == "" {
				t.Errorf("configGetValue after unset returned empty string; expected compiled-in default")
			}
			if after == got {
				t.Errorf("configGetValue after unset (%q) matches post-set value (%q); reset did not clear state (or test value equals default)", after, got)
			}
		})
	}
}

func TestTunableKeys_InvalidValues(t *testing.T) {
	cases := map[string]string{
		"registry_host":              "has spaces",
		"image_repo_prefix":          "UPPERCASE",
		"dhi_registry":               "invalid!host",
		"postgres_image_tag":         "-leading-dash",
		"nats_image_tag":             "with space",
		"default_nats_stream_prefix": "lowercase",
		"backup_create_timeout":      "not-a-duration",
		"health_check_timeout":       "-5s",
		"max_binary_bytes":           "abc",
	}
	for key, bad := range cases {
		t.Run(key, func(t *testing.T) {
			state := config.DefaultState()
			state.EncryptSecrets = false
			err := applyConfigValue(&state, key, bad)
			if err == nil {
				t.Errorf("applyConfigValue(%s, %q) = nil, want error", key, bad)
			}
		})
	}
}

func TestTunableKeys_ComposeAffectingSet(t *testing.T) {
	want := []string{
		"registry_host", "image_repo_prefix", "dhi_registry",
		"postgres_image_tag", "nats_image_tag",
		"default_nats_stream_prefix",
	}
	for _, k := range want {
		if !composeAffectingKeys[k] {
			t.Errorf("%s should be in composeAffectingKeys", k)
		}
	}
	// SYNTHORG_NATS_URL is env-only since the parallel CLI tunable
	// layer was removed; it must not creep back in as a tunable.
	if composeAffectingKeys["default_nats_url"] {
		t.Errorf("default_nats_url should NOT be in composeAffectingKeys")
	}
}

// TestRemovedTunable_DefaultNATSURLRejected guards against re-introducing
// "default_nats_url" as a config-set tunable. SYNTHORG_NATS_URL is the
// single env-only source of truth shared with the backend; if a future
// PR mistakenly re-adds the parallel CLI tunable, this test fails by
// enumerating every entry point (key list + apply + reset).
func TestRemovedTunable_DefaultNATSURLRejected(t *testing.T) {
	const removedKey = "default_nats_url"
	state := config.DefaultState()
	state.EncryptSecrets = false

	if slices.Contains(supportedConfigKeys, removedKey) {
		t.Errorf("%s should NOT be present in supportedConfigKeys", removedKey)
	}
	if slices.Contains(gettableConfigKeys, removedKey) {
		t.Errorf("%s should NOT be present in gettableConfigKeys", removedKey)
	}
	if err := applyConfigValue(&state, removedKey, "nats://example:4222"); err == nil {
		t.Errorf("applyConfigValue should reject removed key %q", removedKey)
	}
	if err := resetConfigValue(&state, removedKey); err == nil {
		t.Errorf("resetConfigValue should reject removed key %q", removedKey)
	}
	// envVarForKey backs the env-var plumbing the live tunables use;
	// ensuring it returns "" prevents the removed key from lingering
	// as a back-channel even after the apply/reset paths reject it.
	if env := envVarForKey(removedKey); env != "" {
		t.Errorf("envVarForKey(%q) = %q, want \"\" (no env-var mapping)", removedKey, env)
	}
}
