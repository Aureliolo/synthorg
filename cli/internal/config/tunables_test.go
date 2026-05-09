package config

import (
	"strings"
	"testing"
	"time"
)

func TestDefaultTunables_AllDefaults(t *testing.T) {
	got := DefaultTunables()
	if got.RegistryHost != DefaultRegistryHost {
		t.Errorf("RegistryHost = %q; want %q", got.RegistryHost, DefaultRegistryHost)
	}
	if got.DHIRegistry != DefaultDHIRegistry {
		t.Errorf("DHIRegistry = %q; want %q", got.DHIRegistry, DefaultDHIRegistry)
	}
	if got.BackupCreateTimeout != DefaultBackupCreateTimeout {
		t.Errorf("BackupCreateTimeout = %v; want %v", got.BackupCreateTimeout, DefaultBackupCreateTimeout)
	}
	if got.MaxBinaryBytes != DefaultMaxBinaryBytes {
		t.Errorf("MaxBinaryBytes = %d; want %d", got.MaxBinaryBytes, DefaultMaxBinaryBytes)
	}
	if got.CustomRegistry {
		t.Error("CustomRegistry = true on defaults; want false")
	}
}

func TestResolveTunables_DefaultsOnEmptyState(t *testing.T) {
	t.Setenv(EnvRegistryHost, "")
	t.Setenv(EnvHealthCheckTimeout, "")
	t.Setenv(EnvMaxBinaryBytes, "")
	t.Setenv(EnvImageVerifyTimeout, "")
	t.Setenv(EnvImagePullAttempts, "")
	t.Setenv(EnvImagePullRetryDelay, "")

	tun, err := ResolveTunables(State{})
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.RegistryHost != DefaultRegistryHost {
		t.Errorf("RegistryHost = %q; want %q", tun.RegistryHost, DefaultRegistryHost)
	}
	if tun.HealthCheckTimeout != DefaultHealthCheckTimeout {
		t.Errorf("HealthCheckTimeout = %v; want %v", tun.HealthCheckTimeout, DefaultHealthCheckTimeout)
	}
	if tun.MaxBinaryBytes != DefaultMaxBinaryBytes {
		t.Errorf("MaxBinaryBytes = %d; want %d", tun.MaxBinaryBytes, DefaultMaxBinaryBytes)
	}
	if tun.ImageVerifyTimeout != DefaultImageVerifyTimeout {
		t.Errorf("ImageVerifyTimeout = %v; want %v", tun.ImageVerifyTimeout, DefaultImageVerifyTimeout)
	}
	if tun.ImagePullAttempts != DefaultImagePullAttempts {
		t.Errorf("ImagePullAttempts = %d; want %d", tun.ImagePullAttempts, DefaultImagePullAttempts)
	}
	if tun.ImagePullRetryDelay != DefaultImagePullRetryDelay {
		t.Errorf("ImagePullRetryDelay = %v; want %v", tun.ImagePullRetryDelay, DefaultImagePullRetryDelay)
	}
	if tun.CustomRegistry {
		t.Error("CustomRegistry = true on empty state; want false")
	}
}

func TestResolveTunables_StateOverridesDefault(t *testing.T) {
	s := State{
		HealthCheckTimeout:      "12s",
		MaxBinaryBytes:          100 * 1024 * 1024,
		DefaultNATSStreamPrefix: "CUSTOM",
	}
	tun, err := ResolveTunables(s)
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.HealthCheckTimeout != 12*time.Second {
		t.Errorf("HealthCheckTimeout = %v; want 12s", tun.HealthCheckTimeout)
	}
	if tun.MaxBinaryBytes != 100*1024*1024 {
		t.Errorf("MaxBinaryBytes = %d; want %d", tun.MaxBinaryBytes, 100*1024*1024)
	}
	if tun.DefaultNATSStreamPrefix != "CUSTOM" {
		t.Errorf("DefaultNATSStreamPrefix = %q", tun.DefaultNATSStreamPrefix)
	}
}

func TestResolveTunables_EnvOverridesState(t *testing.T) {
	t.Setenv(EnvHealthCheckTimeout, "7s")
	t.Setenv(EnvMaxBinaryBytes, "64MiB")
	s := State{HealthCheckTimeout: "12s", MaxBinaryBytes: 100 * 1024 * 1024}
	tun, err := ResolveTunables(s)
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.HealthCheckTimeout != 7*time.Second {
		t.Errorf("HealthCheckTimeout = %v; want 7s", tun.HealthCheckTimeout)
	}
	if tun.MaxBinaryBytes != 64*1024*1024 {
		t.Errorf("MaxBinaryBytes = %d; want %d", tun.MaxBinaryBytes, 64*1024*1024)
	}
}

func TestResolveTunables_ImageVerifyTimeoutPrecedence(t *testing.T) {
	// env > state > default for the image-verify timeout.
	t.Setenv(EnvImageVerifyTimeout, "45s")
	s := State{ImageVerifyTimeout: "90s"}
	tun, err := ResolveTunables(s)
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.ImageVerifyTimeout != 45*time.Second {
		t.Errorf("env wins: ImageVerifyTimeout = %v; want 45s", tun.ImageVerifyTimeout)
	}
}

func TestResolveTunables_ImageVerifyTimeoutStateOverridesDefault(t *testing.T) {
	tun, err := ResolveTunables(State{ImageVerifyTimeout: "30s"})
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.ImageVerifyTimeout != 30*time.Second {
		t.Errorf("state wins: ImageVerifyTimeout = %v; want 30s", tun.ImageVerifyTimeout)
	}
}

func TestResolveTunables_ImagePullAttemptsPrecedence(t *testing.T) {
	// env > state > default for the pull attempts counter.
	t.Setenv(EnvImagePullAttempts, "7")
	s := State{ImagePullAttempts: "5"}
	tun, err := ResolveTunables(s)
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.ImagePullAttempts != 7 {
		t.Errorf("env wins: ImagePullAttempts = %d; want 7", tun.ImagePullAttempts)
	}
}

func TestResolveTunables_ImagePullRetryDelayPrecedence(t *testing.T) {
	// env > state > default for the retry base-delay.
	t.Setenv(EnvImagePullRetryDelay, "500ms")
	s := State{ImagePullRetryDelay: "1s"}
	tun, err := ResolveTunables(s)
	if err != nil {
		t.Fatalf("ResolveTunables: %v", err)
	}
	if tun.ImagePullRetryDelay != 500*time.Millisecond {
		t.Errorf("env wins: ImagePullRetryDelay = %v; want 500ms", tun.ImagePullRetryDelay)
	}
}

func TestResolveTunables_CustomRegistryDetected(t *testing.T) {
	cases := []struct {
		name  string
		state State
		env   map[string]string
		want  bool
	}{
		{"all defaults", State{}, nil, false},
		{"state overrides registry", State{RegistryHost: "my.reg"}, nil, true},
		{"state overrides dhi", State{DHIRegistry: "docker.io"}, nil, true},
		{"state overrides postgres tag", State{PostgresImageTag: "17-debian13"}, nil, true},
		{"env overrides registry", State{}, map[string]string{EnvRegistryHost: "my.reg"}, true},
		{"env overrides nats tag", State{}, map[string]string{EnvNATSImageTag: "2.11-debian13"}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			tun, err := ResolveTunables(tc.state)
			if err != nil {
				t.Fatalf("ResolveTunables: %v", err)
			}
			if tun.CustomRegistry != tc.want {
				t.Errorf("CustomRegistry = %v; want %v", tun.CustomRegistry, tc.want)
			}
		})
	}
}

func TestResolveTunables_InvalidValues(t *testing.T) {
	cases := []struct {
		name  string
		state State
		env   map[string]string
		msg   string
	}{
		{"bad duration env", State{}, map[string]string{EnvHealthCheckTimeout: "not-a-duration"}, "health_check_timeout"},
		{"negative duration state", State{HealthCheckTimeout: "-1s"}, nil, "health_check_timeout"},
		{"zero duration state", State{HealthCheckTimeout: "0s"}, nil, "health_check_timeout"},
		{"bad bytes env", State{}, map[string]string{EnvMaxBinaryBytes: "2XiB"}, "max_binary_bytes"},
		{"exceeds bytes ceiling", State{MaxBinaryBytes: 2 * 1024 * 1024 * 1024}, nil, "max_binary_bytes"},
		{"negative bytes state", State{MaxAPIResponseBytes: -5}, nil, "max_api_response_bytes"},
		{"bad registry host", State{RegistryHost: "not valid"}, nil, "registry_host"},
		{"bad stream prefix", State{DefaultNATSStreamPrefix: "lowercase"}, nil, "default_nats_stream_prefix"},
		{"bad image verify timeout env", State{}, map[string]string{EnvImageVerifyTimeout: "not-a-duration"}, "image_verify_timeout"},
		{"bad image pull retry delay state", State{ImagePullRetryDelay: "not-a-duration"}, nil, "image_pull_retry_delay"},
		{"zero image pull attempts env", State{}, map[string]string{EnvImagePullAttempts: "0"}, "image_pull_attempts"},
		{"negative image pull attempts state", State{ImagePullAttempts: "-1"}, nil, "image_pull_attempts"},
		{"image pull attempts exceeds ceiling", State{ImagePullAttempts: "999"}, nil, "image_pull_attempts"},
		{"non-integer image pull attempts env", State{}, map[string]string{EnvImagePullAttempts: "three"}, "image_pull_attempts"},
		{"image verify timeout below floor env", State{}, map[string]string{EnvImageVerifyTimeout: "1ns"}, "image_verify_timeout"},
		{"image verify timeout below floor state", State{ImageVerifyTimeout: "500ms"}, nil, "image_verify_timeout"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			_, err := ResolveTunables(tc.state)
			if err == nil {
				t.Fatal("expected error")
			}
			if !strings.Contains(err.Error(), tc.msg) {
				t.Errorf("error %q does not mention %q", err, tc.msg)
			}
		})
	}
}

func TestParseBytes(t *testing.T) {
	cases := []struct {
		in   string
		want int64
		err  bool
	}{
		{"1024", 1024, false},
		{"1KiB", 1024, false},
		{"1kib", 1024, false},
		{"1kb", 1000, false},
		{"256MiB", 256 * 1024 * 1024, false},
		{"1.5MiB", int64(1.5 * 1024 * 1024), false},
		{"1GiB", 1024 * 1024 * 1024, false},
		{"1GB", 1000 * 1000 * 1000, false},
		{"1B", 1, false},
		{"", 0, true},
		{"-1", 0, true},
		{"0", 0, true},
		{"0B", 0, true},
		{".5B", 0, true},                 // sub-byte truncates to 0
		{".000000000000000001", 0, true}, // extreme fraction truncates to 0
		{"1XB", 0, true},
		{"abc", 0, true},
		// Overflow: value larger than int64 capacity. Must be rejected
		// safely (not silently wrapped to a negative int64).
		{"999999999999GiB", 0, true},
		{"9999999999999999999", 0, true},
		// Ceiling: 2 GiB exceeds the 1 GiB runtime ceiling.
		{"2GiB", 0, true},
		// Regression for the int64-rounding edge case: a numeric input
		// that rounds to float64(math.MaxInt64) (= 2^63) must NOT be
		// silently cast to math.MinInt64 on amd64. It must be rejected
		// by the in-float-space ceiling check well before any cast.
		{"9223372036854775808", 0, true},
	}
	for _, tc := range cases {
		t.Run(tc.in, func(t *testing.T) {
			got, err := ParseBytes(tc.in)
			if tc.err {
				if err == nil {
					t.Errorf("ParseBytes(%q) = %d, want error", tc.in, got)
				}
				return
			}
			if err != nil {
				t.Fatalf("ParseBytes(%q): %v", tc.in, err)
			}
			if got != tc.want {
				t.Errorf("ParseBytes(%q) = %d; want %d", tc.in, got, tc.want)
			}
		})
	}
}

// FuzzParseBytes keeps the overflow, ceiling, and float-rounding
// defenses covered far more thoroughly than a growing table of
// TestParseBytes cases. For every accepted input, the parser MUST
// return a strictly-positive int64 <= MaxBytesCeiling; anything else
// means a defense regressed. The corpus seeds a mix of the existing
// happy-path and failure-path cases so the fuzzer has something to
// mutate from.
func FuzzParseBytes(f *testing.F) {
	seeds := []string{
		"", "0", "1", "1024",
		"1KiB", "1kib", "1kb", "256MiB", "1.5MiB", "1GiB", "1GB", "1B",
		"-1", "1XB", "abc",
		"999999999999GiB", "9999999999999999999",
		"2GiB", "9223372036854775808",
		" ", "1 ", " 1",
	}
	for _, s := range seeds {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, s string) {
		got, err := ParseBytes(s)
		if err != nil {
			return
		}
		if got <= 0 {
			t.Fatalf("ParseBytes(%q) = %d: accepted value must be > 0", s, got)
		}
		if got > MaxBytesCeiling {
			t.Fatalf("ParseBytes(%q) = %d: exceeds MaxBytesCeiling %d", s, got, MaxBytesCeiling)
		}
	})
}

func TestIsValidRegistryHost(t *testing.T) {
	good := []string{"ghcr.io", "dhi.io", "my-registry.example.com", "localhost:5000", "10.0.0.1:5000"}
	bad := []string{"", "ghcr io", "ghcr.io/", "ghcr.io:abc", "ghcr.io:99999", "!invalid"}
	for _, h := range good {
		if !IsValidRegistryHost(h) {
			t.Errorf("IsValidRegistryHost(%q) = false; want true", h)
		}
	}
	for _, h := range bad {
		if IsValidRegistryHost(h) {
			t.Errorf("IsValidRegistryHost(%q) = true; want false", h)
		}
	}
}

func TestIsValidImageRepoPrefix(t *testing.T) {
	good := []string{"aureliolo/synthorg-", "myorg/", "myorg/service-", "single"}
	bad := []string{"", "UPPER", "with space", "!bang"}
	for _, p := range good {
		if !IsValidImageRepoPrefix(p) {
			t.Errorf("IsValidImageRepoPrefix(%q) = false; want true", p)
		}
	}
	for _, p := range bad {
		if IsValidImageRepoPrefix(p) {
			t.Errorf("IsValidImageRepoPrefix(%q) = true; want false", p)
		}
	}
}

func TestIsValidStreamPrefix(t *testing.T) {
	// Regex: ^[A-Z0-9][A-Z0-9_\-]*$. A leading digit is allowed ("1LEADING"),
	// but ANY lowercase letter rejects the whole string even if prefixed by
	// a digit ("1LEADING-ok-actually" has "ok-actually" → invalid).
	good := []string{"SYNTHORG", "MY_PREFIX", "A", "A-B", "1LEADING"}
	bad := []string{"", "lower", "1LEADING-ok-actually", "has space"}
	for _, p := range good {
		if !IsValidStreamPrefix(p) {
			t.Errorf("IsValidStreamPrefix(%q) = false; want true", p)
		}
	}
	for _, p := range bad {
		if IsValidStreamPrefix(p) {
			t.Errorf("IsValidStreamPrefix(%q) = true; want false", p)
		}
	}
}

func TestValidateNATSURL(t *testing.T) {
	good := []string{"nats://nats:4222", "tls://example.com:4443", "nats+tls://host"}
	bad := []string{"", "http://example.com", "nats://", "nats://host:abc", "nats://host:99999"}
	for _, u := range good {
		if err := ValidateNATSURL(u); err != nil {
			t.Errorf("ValidateNATSURL(%q): unexpected error %v", u, err)
		}
	}
	for _, u := range bad {
		if err := ValidateNATSURL(u); err == nil {
			t.Errorf("ValidateNATSURL(%q): expected error, got nil", u)
		}
	}
}
