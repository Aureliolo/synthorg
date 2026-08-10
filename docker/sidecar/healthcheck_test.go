package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
)

// configuredToken satisfies the loader's minimum admin-token length.
const configuredToken = "0123456789abcdef0123456789abcdef0123"

// servePort starts a health endpoint on a free port and returns that port.
func servePort(t *testing.T, status int) int {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := httptest.NewUnstartedServer(http.HandlerFunc(
		func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(status) },
	))
	srv.Listener.Close()
	srv.Listener = ln
	srv.Start()
	t.Cleanup(srv.Close)
	return ln.Addr().(*net.TCPAddr).Port
}

func TestHealthcheckPassesWhenTheEndpointAnswers(t *testing.T) {
	port := servePort(t, http.StatusOK)
	t.Setenv("SIDECAR_ADMIN_TOKEN", configuredToken)
	t.Setenv("SIDECAR_HEALTH_PORT", strconv.Itoa(port))

	if code := runHealthcheck(); code != 0 {
		t.Errorf("exit code = %d, want 0", code)
	}
}

func TestHealthcheckFollowsTheConfiguredPort(t *testing.T) {
	// A probe pinned to the default port would report a sidecar healthy (or
	// dead) on evidence from a listener it does not run.
	port := servePort(t, http.StatusOK)
	t.Setenv("SIDECAR_ADMIN_TOKEN", configuredToken)
	t.Setenv("SIDECAR_HEALTH_PORT", strconv.Itoa(port+1))
	t.Setenv("SIDECAR_PROXY_PORT", strconv.Itoa(port+2))

	if code := runHealthcheck(); code == 0 {
		t.Error("expected failure: nothing is listening on the configured port")
	}
}

func TestHealthcheckFailsOnANonOKStatus(t *testing.T) {
	port := servePort(t, http.StatusServiceUnavailable)
	t.Setenv("SIDECAR_ADMIN_TOKEN", configuredToken)
	t.Setenv("SIDECAR_HEALTH_PORT", strconv.Itoa(port))

	if code := runHealthcheck(); code == 0 {
		t.Error("expected a non-OK status to fail the probe")
	}
}

func TestHealthcheckFailsOnUnusableConfiguration(t *testing.T) {
	// Reporting healthy while the configuration the server needs is rejected
	// would mask the reason the container is not serving.
	t.Setenv("SIDECAR_ADMIN_TOKEN", "too-short")

	if code := runHealthcheck(); code == 0 {
		t.Error("expected an unusable configuration to fail the probe")
	}
}
