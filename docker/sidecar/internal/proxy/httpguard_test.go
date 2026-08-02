package proxy

import (
	"bufio"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
)

// recorder collects strings written by handler / relay goroutines and read by
// the test goroutine. The lock is the happens-before edge: without it these
// are plain cross-goroutine slice appends, which `go test -race` reports.
type recorder struct {
	mu    sync.Mutex
	items []string
}

func (r *recorder) add(s string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items = append(r.items, s)
}

func (r *recorder) snapshot() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.items...)
}

func (r *recorder) reset() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items = nil
}

func TestPathAllowedMatchesOnSegmentBoundary(t *testing.T) {
	t.Parallel()

	prefixes := []string{"/api/v1/gateway/v1", "/api/v1/mcp-gateway"}

	cases := []struct {
		path string
		want bool
	}{
		{"/api/v1/gateway/v1", true},
		{"/api/v1/gateway/v1/chat/completions", true},
		{"/api/v1/mcp-gateway/mcp", true},
		// The whole point of the narrowing: routes that share the port.
		{"/api/v1/auth/login", false},
		{"/api/v1/auth/setup", false},
		{"/metrics", false},
		{"/healthz", false},
		{"/", false},
		// A sibling route whose name merely starts with an allowed prefix
		// must not inherit it.
		{"/api/v1/gateway/v1x", false},
		{"/api/v1/mcp-gateway-admin", false},
		// Traversal: these all satisfy a naive prefix test, and the upstream
		// would normalise each one back to a denied route. The request is
		// forwarded verbatim, so a non-canonical path is refused outright.
		{"/api/v1/gateway/v1/../auth/login", false},
		{"/api/v1/gateway/v1/../../auth/setup", false},
		{"/api/v1/gateway/v1/./../metrics", false},
		{"/api/v1/gateway/v1/subpath/../../auth/login", false},
		{"/api/v1/mcp-gateway/..", false},
		// A trailing slash is canonical enough to keep working.
		{"/api/v1/gateway/v1/", true},
	}

	for _, tc := range cases {
		if got := pathAllowed(tc.path, prefixes); got != tc.want {
			t.Errorf("pathAllowed(%q) = %v, want %v", tc.path, got, tc.want)
		}
	}
}

func TestPathAllowedRejectsEverythingWithNoPrefixes(t *testing.T) {
	t.Parallel()

	if pathAllowed("/anything", nil) {
		t.Error("no configured prefix must not admit a path")
	}
}

func TestLooksLikeTLSDetectsClientHello(t *testing.T) {
	t.Parallel()

	hello := bufio.NewReader(strings.NewReader("\x16\x03\x01\x00\x2f"))
	if !looksLikeTLS(hello) {
		t.Error("a TLS ClientHello must be detected")
	}

	plain := bufio.NewReader(strings.NewReader("GET /api/v1 HTTP/1.1\r\n\r\n"))
	if looksLikeTLS(plain) {
		t.Error("a plaintext HTTP request must not look like TLS")
	}
}

// TestRelayHTTPGuardedForwardsAllowedAndRefusesRest drives the real relay
// against a real upstream over real sockets: an allowed path reaches the
// upstream, and a request outside the prefixes is refused with 403 and never
// forwarded. Keep-alive is exercised so a second request on the same
// connection cannot slip past a check made only on the first.
func TestRelayHTTPGuardedForwardsAllowedAndRefusesRest(t *testing.T) {
	t.Parallel()

	reachedUpstream := &recorder{}
	upstreamSrv := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			reachedUpstream.add(r.URL.Path)
			w.WriteHeader(http.StatusOK)
			_, _ = fmt.Fprint(w, "ok")
		}),
		ReadHeaderTimeout: 5 * time.Second,
	}
	upstreamLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen upstream: %v", err)
	}
	defer upstreamLn.Close()
	go func() { _ = upstreamSrv.Serve(upstreamLn) }()
	defer func() { _ = upstreamSrv.Close() }()

	cases := []struct {
		name       string
		path       string
		wantStatus int
		wantReach  bool
	}{
		{name: "allowed", path: "/api/v1/gateway/v1/chat", wantStatus: 200, wantReach: true},
		{name: "blocked_auth", path: "/api/v1/auth/login", wantStatus: 403, wantReach: false},
		{name: "blocked_metrics", path: "/metrics", wantStatus: 403, wantReach: false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			reachedUpstream.reset()

			clientConn, guardConn := net.Pipe()
			upstreamConn, err := net.Dial("tcp", upstreamLn.Addr().String())
			if err != nil {
				t.Fatalf("dial upstream: %v", err)
			}
			defer upstreamConn.Close()

			blockedPaths := &recorder{}
			go func() {
				defer guardConn.Close()
				relayHTTPGuarded(
					bufio.NewReader(guardConn), guardConn, upstreamConn,
					[]string{"/api/v1/gateway/v1", "/api/v1/mcp-gateway"},
					func(_, path string) { blockedPaths.add(path) },
				)
			}()

			_ = clientConn.SetDeadline(time.Now().Add(10 * time.Second))
			req := fmt.Sprintf(
				"GET %s HTTP/1.1\r\nHost: backend\r\n\r\n", tc.path,
			)
			if _, err := clientConn.Write([]byte(req)); err != nil {
				t.Fatalf("write request: %v", err)
			}

			resp, err := http.ReadResponse(bufio.NewReader(clientConn), nil)
			if err != nil {
				t.Fatalf("read response: %v", err)
			}
			defer resp.Body.Close()
			_ = clientConn.Close()

			if resp.StatusCode != tc.wantStatus {
				t.Errorf("status = %d, want %d", resp.StatusCode, tc.wantStatus)
			}
			seen := reachedUpstream.snapshot()
			reached := len(seen) > 0
			if reached != tc.wantReach {
				t.Errorf(
					"upstream reached = %v (%v), want %v",
					reached, seen, tc.wantReach,
				)
			}
			if !tc.wantReach && len(blockedPaths.snapshot()) == 0 {
				t.Error("a refused request must be reported to the blocked hook")
			}
		})
	}
}

// TestRelayHTTPGuardedChecksEveryKeepAliveRequest is the smuggling case: the
// first request is allowed, so a check performed only on connection open
// would let the second through.
func TestRelayHTTPGuardedChecksEveryKeepAliveRequest(t *testing.T) {
	t.Parallel()

	reached := &recorder{}
	upstreamSrv := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			reached.add(r.URL.Path)
			w.WriteHeader(http.StatusOK)
		}),
		ReadHeaderTimeout: 5 * time.Second,
	}
	upstreamLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen upstream: %v", err)
	}
	defer upstreamLn.Close()
	go func() { _ = upstreamSrv.Serve(upstreamLn) }()
	defer func() { _ = upstreamSrv.Close() }()

	upstreamConn, err := net.Dial("tcp", upstreamLn.Addr().String())
	if err != nil {
		t.Fatalf("dial upstream: %v", err)
	}
	defer upstreamConn.Close()

	clientConn, guardConn := net.Pipe()
	go func() {
		defer guardConn.Close()
		relayHTTPGuarded(
			bufio.NewReader(guardConn), guardConn, upstreamConn,
			[]string{"/api/v1/gateway/v1"},
			func(_, _ string) {},
		)
	}()

	_ = clientConn.SetDeadline(time.Now().Add(10 * time.Second))
	reader := bufio.NewReader(clientConn)

	first := "GET /api/v1/gateway/v1/chat HTTP/1.1\r\nHost: b\r\n\r\n"
	if _, err := clientConn.Write([]byte(first)); err != nil {
		t.Fatalf("write first: %v", err)
	}
	resp1, err := http.ReadResponse(reader, nil)
	if err != nil {
		t.Fatalf("read first response: %v", err)
	}
	_ = resp1.Body.Close()
	if resp1.StatusCode != http.StatusOK {
		t.Fatalf("first request status = %d, want 200", resp1.StatusCode)
	}

	second := "GET /api/v1/auth/login HTTP/1.1\r\nHost: b\r\n\r\n"
	if _, err := clientConn.Write([]byte(second)); err != nil {
		t.Fatalf("write second: %v", err)
	}
	resp2, err := http.ReadResponse(reader, nil)
	if err != nil {
		t.Fatalf("read second response: %v", err)
	}
	_ = resp2.Body.Close()
	_ = clientConn.Close()

	if resp2.StatusCode != http.StatusForbidden {
		t.Errorf("second request status = %d, want 403", resp2.StatusCode)
	}
	for _, p := range reached.snapshot() {
		if strings.HasPrefix(p, "/api/v1/auth") {
			t.Errorf("a blocked path reached upstream: %q", p)
		}
	}
}
