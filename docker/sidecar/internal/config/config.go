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

// PathRule narrows an allowed host:port to a set of URL path prefixes.
//
// A host:port allowlist is the whole story only when the destination serves
// exactly one thing. It is not when several routes share one process: the
// backend publishes the LLM gateway and the credentialed-MCP endpoint on the
// same port as its authentication, metrics and webhook routes, so allowing
// that host:port at layer 4 allows all of them. A rule here says "on this
// destination, only these path prefixes", enforced per request.
type PathRule struct {
	Host   string
	Port   uint16
	Prefix string
}

// Config holds the parsed sidecar configuration.
type Config struct {
	AllowedHosts    []HostPort
	AllowedPaths    []PathRule
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

	token, err := loadAdminToken()
	if err != nil {
		return Config{}, err
	}
	cfg.AdminToken = token

	if v := os.Getenv("SIDECAR_ALLOWED_HOSTS"); v != "" {
		cfg.AllowedHosts = parseAllowedHosts(v)
	}

	if v := os.Getenv("SIDECAR_ALLOWED_PATHS"); v != "" {
		rules, err := parseAllowedPaths(v)
		if err != nil {
			return Config{}, fmt.Errorf("SIDECAR_ALLOWED_PATHS: %w", err)
		}
		cfg.AllowedPaths = rules
	}

	cfg.DNSAllowed = parseBool(os.Getenv("SIDECAR_DNS_ALLOWED"), true)
	cfg.LoopbackAllowed = parseBool(os.Getenv("SIDECAR_LOOPBACK_ALLOWED"), true)
	cfg.AllowAll = parseBool(os.Getenv("SIDECAR_ALLOW_ALL"), false)

	if err := loadPorts(&cfg); err != nil {
		return Config{}, err
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

// loadAdminToken reads and validates the rule-mutation guard token.
func loadAdminToken() (string, error) {
	token := strings.TrimSpace(os.Getenv("SIDECAR_ADMIN_TOKEN"))
	if token == "" {
		return "", fmt.Errorf("SIDECAR_ADMIN_TOKEN is required")
	}
	if len(token) < minAdminTokenLength {
		return "", fmt.Errorf(
			"SIDECAR_ADMIN_TOKEN must be at least %d characters", minAdminTokenLength)
	}
	return token, nil
}

// loadPorts resolves the health and proxy ports, which must differ: they are
// separate listeners, and sharing one would leave whichever bound second dead.
func loadPorts(cfg *Config) error {
	if v := os.Getenv("SIDECAR_HEALTH_PORT"); v != "" {
		p, err := parsePort(v)
		if err != nil {
			return fmt.Errorf("SIDECAR_HEALTH_PORT: %w", err)
		}
		cfg.HealthPort = p
	}
	if v := os.Getenv("SIDECAR_PROXY_PORT"); v != "" {
		p, err := parsePort(v)
		if err != nil {
			return fmt.Errorf("SIDECAR_PROXY_PORT: %w", err)
		}
		cfg.ProxyPort = p
	}
	if cfg.HealthPort == cfg.ProxyPort {
		return fmt.Errorf(
			"SIDECAR_HEALTH_PORT (%d) and SIDECAR_PROXY_PORT (%d) must differ",
			cfg.HealthPort, cfg.ProxyPort,
		)
	}
	return nil
}

// parseAllowedPaths parses "host:port=/prefix" entries, comma separated.
// Repeat the host:port to grant it more than one prefix. A malformed entry
// is an error rather than a skip: silently dropping one would widen the
// destination back to every route it serves, which is the opposite of what
// the caller asked for.
func parseAllowedPaths(raw string) ([]PathRule, error) {
	var rules []PathRule
	for _, entry := range strings.Split(raw, ",") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		hostPart, prefix, found := strings.Cut(entry, "=")
		if !found {
			return nil, fmt.Errorf("entry %q is not host:port=/prefix", entry)
		}
		prefix = strings.TrimSpace(prefix)
		if !strings.HasPrefix(prefix, "/") {
			return nil, fmt.Errorf("prefix %q must start with '/'", prefix)
		}
		hosts := parseAllowedHosts(strings.TrimSpace(hostPart))
		if len(hosts) != 1 {
			return nil, fmt.Errorf("entry %q has no valid host:port", entry)
		}
		rules = append(rules, PathRule{
			Host: hosts[0].Host, Port: hosts[0].Port, Prefix: prefix,
		})
	}
	return rules, nil
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
