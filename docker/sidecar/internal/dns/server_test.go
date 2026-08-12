package dns_test

import (
	"encoding/binary"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/sidecar/internal/allowlist"
	"github.com/Aureliolo/synthorg/sidecar/internal/config"
	"github.com/Aureliolo/synthorg/sidecar/internal/dns"
)

// buildTestQuery creates a minimal DNS query for the given hostname.
func buildTestQuery(hostname string) []byte {
	// DNS header (12 bytes).
	header := make([]byte, 12)
	// Transaction ID.
	binary.BigEndian.PutUint16(header[0:], 0x1234)
	// Flags: standard query, recursion desired.
	header[2] = 0x01 // RD=1
	header[3] = 0x00
	// QDCOUNT=1.
	binary.BigEndian.PutUint16(header[4:], 1)

	// Question section: encode hostname.
	var question []byte
	for _, label := range splitHostname(hostname) {
		question = append(question, byte(len(label)))
		question = append(question, []byte(label)...)
	}
	question = append(question, 0) // null terminator

	// QTYPE=A (1), QCLASS=IN (1).
	question = append(question, 0, 1, 0, 1)

	return append(header, question...)
}

func splitHostname(h string) []string {
	var parts []string
	start := 0
	for i := range len(h) {
		if h[i] == '.' {
			parts = append(parts, h[start:i])
			start = i + 1
		}
	}
	parts = append(parts, h[start:])
	return parts
}

func TestExtractQueryHostname(t *testing.T) {
	query := buildTestQuery("api.example.com")
	hostname := dns.ExtractQueryHostname(query)
	if hostname != "api.example.com" {
		t.Errorf("hostname = %q, want %q", hostname, "api.example.com")
	}
}

func TestExtractQueryHostnameSingleLabel(t *testing.T) {
	query := buildTestQuery("localhost")
	hostname := dns.ExtractQueryHostname(query)
	if hostname != "localhost" {
		t.Errorf("hostname = %q, want %q", hostname, "localhost")
	}
}

func TestExtractQueryHostnameEmpty(t *testing.T) {
	hostname := dns.ExtractQueryHostname(nil)
	if hostname != "" {
		t.Errorf("hostname = %q, want empty", hostname)
	}
}

func TestExtractQueryHostnameTooShort(t *testing.T) {
	hostname := dns.ExtractQueryHostname(make([]byte, 5))
	if hostname != "" {
		t.Errorf("hostname = %q, want empty for short query", hostname)
	}
}

func TestBuildNXDOMAIN(t *testing.T) {
	query := buildTestQuery("evil.com")
	resp := dns.BuildNXDOMAIN(query)

	if resp == nil {
		t.Fatal("expected non-nil response")
	}
	if len(resp) < 12 {
		t.Fatal("response too short")
	}

	// Check QR bit (response).
	if resp[2]&0x80 == 0 {
		t.Error("QR bit not set (should be response)")
	}
	// Check RCODE=3 (NXDOMAIN).
	if resp[3]&0x0F != 3 {
		t.Errorf("RCODE = %d, want 3 (NXDOMAIN)", resp[3]&0x0F)
	}
	// Transaction ID preserved.
	if resp[0] != query[0] || resp[1] != query[1] {
		t.Error("transaction ID not preserved")
	}
}

func TestBuildNXDOMAINShortQuery(t *testing.T) {
	resp := dns.BuildNXDOMAIN(make([]byte, 5))
	if resp != nil {
		t.Error("expected nil response for short query")
	}
}

// newServer builds a server, skipping when the host has no upstream to name.
func newServer(t *testing.T) *dns.Server {
	t.Helper()
	al := allowlist.New([]config.HostPort{
		{Host: "api.example.com", Port: 443},
	}, false, 0)
	srv, err := dns.NewServer(al, true, nil)
	if err != nil {
		if strings.Contains(err.Error(), "no upstream DNS found") {
			t.Skipf("no upstream DNS available: %v", err)
		}
		t.Fatalf("failed to create DNS server: %v", err)
	}
	return srv
}

func TestNewServer(t *testing.T) {
	if srv := newServer(t); srv == nil {
		t.Fatal("expected non-nil server")
	}
}

func TestServeBeforeListenIsRefused(t *testing.T) {
	// Port 53 is bound while the process still holds NET_ADMIN and only
	// answered after it drops. Serving without that bind would mean the split
	// silently did nothing, so it reports rather than starting on nothing.
	srv := newServer(t)
	if err := srv.Serve(); err == nil {
		t.Error("expected Serve to fail when Listen has not run")
	}
}
