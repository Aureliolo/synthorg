package config_test

import (
	"testing"

	"github.com/Aureliolo/synthorg/sidecar/internal/config"
)

// validAdminToken is at least minAdminTokenLength (32) characters so it
// passes the loader's token-length floor.
const validAdminToken = "test-admin-token-0123456789abcdef"

func TestLoadDefaults(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.HealthPort != 15000 {
		t.Errorf("HealthPort = %d, want 15000", cfg.HealthPort)
	}
	if cfg.ProxyPort != 15001 {
		t.Errorf("ProxyPort = %d, want 15001", cfg.ProxyPort)
	}
	if cfg.LogLevel != "info" {
		t.Errorf("LogLevel = %q, want %q", cfg.LogLevel, "info")
	}
	if !cfg.DNSAllowed {
		t.Error("DNSAllowed = false, want true")
	}
	if !cfg.LoopbackAllowed {
		t.Error("LoopbackAllowed = false, want true")
	}
	if cfg.AllowAll {
		t.Error("AllowAll = true, want false")
	}
	if cfg.ResolveInterval != 30 {
		t.Errorf("ResolveInterval = %d, want 30", cfg.ResolveInterval)
	}
	if len(cfg.AllowedHosts) != 0 {
		t.Errorf("AllowedHosts = %v, want empty", cfg.AllowedHosts)
	}
	if cfg.AdminToken != validAdminToken {
		t.Errorf("AdminToken = %q, want %q", cfg.AdminToken, validAdminToken)
	}
}

func TestLoadAllowedHosts(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_ALLOWED_HOSTS", "api.example.com:443,db.local:5432")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(cfg.AllowedHosts) != 2 {
		t.Fatalf("AllowedHosts length = %d, want 2", len(cfg.AllowedHosts))
	}
	if cfg.AllowedHosts[0].Host != "api.example.com" || cfg.AllowedHosts[0].Port != 443 {
		t.Errorf("AllowedHosts[0] = %+v, want api.example.com:443", cfg.AllowedHosts[0])
	}
	if cfg.AllowedHosts[1].Host != "db.local" || cfg.AllowedHosts[1].Port != 5432 {
		t.Errorf("AllowedHosts[1] = %+v, want db.local:5432", cfg.AllowedHosts[1])
	}
}

func TestLoadIPBasedHosts(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_ALLOWED_HOSTS", "93.184.216.34:443")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(cfg.AllowedHosts) != 1 {
		t.Fatalf("AllowedHosts length = %d, want 1", len(cfg.AllowedHosts))
	}
	if cfg.AllowedHosts[0].Host != "93.184.216.34" || cfg.AllowedHosts[0].Port != 443 {
		t.Errorf("AllowedHosts[0] = %+v, want 93.184.216.34:443", cfg.AllowedHosts[0])
	}
}

func TestLoadInvalidPortSkipped(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_ALLOWED_HOSTS", "good.com:443,bad.com:notaport,also-good.com:80")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(cfg.AllowedHosts) != 2 {
		t.Fatalf("AllowedHosts length = %d, want 2 (invalid entry skipped)", len(cfg.AllowedHosts))
	}
}

func TestLoadPortOutOfRange(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_ALLOWED_HOSTS", "host.com:0,host.com:70000")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(cfg.AllowedHosts) != 0 {
		t.Errorf("AllowedHosts length = %d, want 0 (out of range ports skipped)", len(cfg.AllowedHosts))
	}
}

func TestLoadBoolFlags(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_DNS_ALLOWED", "0")
	t.Setenv("SIDECAR_LOOPBACK_ALLOWED", "0")
	t.Setenv("SIDECAR_ALLOW_ALL", "1")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.DNSAllowed {
		t.Error("DNSAllowed = true, want false")
	}
	if cfg.LoopbackAllowed {
		t.Error("LoopbackAllowed = true, want false")
	}
	if !cfg.AllowAll {
		t.Error("AllowAll = false, want true")
	}
}

func TestLoadCustomPorts(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_HEALTH_PORT", "9000")
	t.Setenv("SIDECAR_PROXY_PORT", "9001")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.HealthPort != 9000 {
		t.Errorf("HealthPort = %d, want 9000", cfg.HealthPort)
	}
	if cfg.ProxyPort != 9001 {
		t.Errorf("ProxyPort = %d, want 9001", cfg.ProxyPort)
	}
}

func TestLoadPortConflict(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_HEALTH_PORT", "8080")
	t.Setenv("SIDECAR_PROXY_PORT", "8080")

	_, err := config.Load()
	if err == nil {
		t.Fatal("expected error for port conflict, got nil")
	}
}

func TestLoadMissingAdminToken(t *testing.T) {
	// No SIDECAR_ADMIN_TOKEN set.
	_, err := config.Load()
	if err == nil {
		t.Fatal("expected error for missing admin token, got nil")
	}
}

func TestLoadRejectsShortAdminToken(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", "short-token")
	_, err := config.Load()
	if err == nil {
		t.Fatal("expected error for a too-short admin token, got nil")
	}
}

func TestHostnameNormalizedToLower(t *testing.T) {
	t.Setenv("SIDECAR_ADMIN_TOKEN", validAdminToken)
	t.Setenv("SIDECAR_ALLOWED_HOSTS", "API.Example.COM:443")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.AllowedHosts[0].Host != "api.example.com" {
		t.Errorf("Host = %q, want lowercase", cfg.AllowedHosts[0].Host)
	}
}
