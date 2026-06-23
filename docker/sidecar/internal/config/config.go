// Package config parses sidecar configuration from environment variables.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// minAdminTokenLength is the floor for SIDECAR_ADMIN_TOKEN. The token
// guards the rule-mutation endpoint with a constant-time compare; a very
// short token provides negligible brute-force resistance, so a
// misconfigured short value is rejected loudly at startup.
const minAdminTokenLength = 32

// HostPort is a validated host:port entry from the allowlist.
type HostPort struct {
	Host string
	Port uint16
}

// Config holds the parsed sidecar configuration.
type Config struct {
	AllowedHosts    []HostPort
	AllowAll        bool
	DNSAllowed      bool
	LoopbackAllowed bool
	HealthPort      uint16
	ProxyPort       uint16
	AdminToken      string
	LogLevel        string
	ResolveInterval int
}

// Load parses all SIDECAR_* environment variables into a Config.
func Load() (Config, error) {
	cfg := Config{
		DNSAllowed:      true,
		LoopbackAllowed: true,
		HealthPort:      15000,
		ProxyPort:       15001,
		LogLevel:        "info",
		ResolveInterval: 30,
	}

	cfg.AdminToken = strings.TrimSpace(os.Getenv("SIDECAR_ADMIN_TOKEN"))
	if cfg.AdminToken == "" {
		return Config{}, fmt.Errorf("SIDECAR_ADMIN_TOKEN is required")
	}
	if len(cfg.AdminToken) < minAdminTokenLength {
		return Config{}, fmt.Errorf(
			"SIDECAR_ADMIN_TOKEN must be at least %d characters", minAdminTokenLength)
	}

	if v := os.Getenv("SIDECAR_ALLOWED_HOSTS"); v != "" {
		cfg.AllowedHosts = parseAllowedHosts(v)
	}

	cfg.DNSAllowed = parseBool(os.Getenv("SIDECAR_DNS_ALLOWED"), true)
	cfg.LoopbackAllowed = parseBool(os.Getenv("SIDECAR_LOOPBACK_ALLOWED"), true)
	cfg.AllowAll = parseBool(os.Getenv("SIDECAR_ALLOW_ALL"), false)

	if v := os.Getenv("SIDECAR_HEALTH_PORT"); v != "" {
		p, err := parsePort(v)
		if err != nil {
			return Config{}, fmt.Errorf("SIDECAR_HEALTH_PORT: %w", err)
		}
		cfg.HealthPort = p
	}

	if v := os.Getenv("SIDECAR_PROXY_PORT"); v != "" {
		p, err := parsePort(v)
		if err != nil {
			return Config{}, fmt.Errorf("SIDECAR_PROXY_PORT: %w", err)
		}
		cfg.ProxyPort = p
	}

	if cfg.HealthPort == cfg.ProxyPort {
		return Config{}, fmt.Errorf(
			"SIDECAR_HEALTH_PORT (%d) and SIDECAR_PROXY_PORT (%d) must differ",
			cfg.HealthPort, cfg.ProxyPort,
		)
	}

	if v := os.Getenv("SIDECAR_LOG_LEVEL"); v != "" {
		level := strings.ToLower(strings.TrimSpace(v))
		switch level {
		case "debug", "info", "warn", "error":
			cfg.LogLevel = level
		default:
			return Config{}, fmt.Errorf("SIDECAR_LOG_LEVEL: invalid level %q", v)
		}
	}

	if v := os.Getenv("SIDECAR_RESOLVE_INTERVAL"); v != "" {
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err != nil || n < 1 {
			return Config{}, fmt.Errorf("SIDECAR_RESOLVE_INTERVAL: must be a positive integer, got %q", v)
		}
		cfg.ResolveInterval = n
	}

	return cfg, nil
}

func parseAllowedHosts(raw string) []HostPort {
	var hosts []HostPort
	for _, entry := range strings.Split(raw, ",") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		idx := strings.LastIndex(entry, ":")
		if idx < 1 {
			continue
		}
		host := strings.ToLower(strings.TrimSpace(entry[:idx]))
		portStr := strings.TrimSpace(entry[idx+1:])
		port, err := strconv.ParseUint(portStr, 10, 16)
		if err != nil || port < 1 || port > 65535 {
			continue
		}
		hosts = append(hosts, HostPort{Host: host, Port: uint16(port)})
	}
	return hosts
}

func parseBool(val string, defaultVal bool) bool {
	val = strings.TrimSpace(val)
	if val == "" {
		return defaultVal
	}
	return val == "1" || strings.EqualFold(val, "true")
}

func parsePort(val string) (uint16, error) {
	n, err := strconv.ParseUint(strings.TrimSpace(val), 10, 16)
	if err != nil {
		return 0, fmt.Errorf("invalid port %q: %w", val, err)
	}
	if n < 1 || n > 65535 {
		return 0, fmt.Errorf("port %d out of range 1-65535", n)
	}
	return uint16(n), nil
}
