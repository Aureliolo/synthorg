package health_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/sidecar/internal/allowlist"
	"github.com/Aureliolo/synthorg/sidecar/internal/config"
	"github.com/Aureliolo/synthorg/sidecar/internal/health"
)

const testToken = "test-secret-token-123"

func newTestServer() *health.Server {
	hosts := []config.HostPort{
		{Host: "api.example.com", Port: 443},
	}
	al := allowlist.New(hosts, true, 0)
	return health.NewServer(0, al, testToken, hosts, false, nil)
}

// newServingServer returns a server that has reached the state main marks
// after egress enforcement is installed and privilege is given up.
func newServingServer() *health.Server {
	srv := newTestServer()
	srv.MarkReady()
	return srv
}

func TestHandleHealthz_ok(t *testing.T) {
	t.Parallel()
	srv := newServingServer()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}

	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if body["status"] != "ok" {
		t.Errorf("status = %v, want ok", body["status"])
	}
	if _, ok := body["uptime_seconds"]; !ok {
		t.Error("missing uptime_seconds field")
	}
}

func TestHandleHealthz_no_auth_required(t *testing.T) {
	t.Parallel()
	srv := newServingServer()
	// Health check must work WITHOUT auth (Docker healthcheck).
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("healthz should not require auth, got status %d", w.Code)
	}
}

func TestHandleHealthz_reports_starting_before_enforcement_is_installed(t *testing.T) {
	t.Parallel()
	// The caller joins the sandbox to this network namespace the moment the
	// container reads healthy, and Docker flips to healthy off one successful
	// probe. Answering ok before MarkReady would hand out a namespace whose
	// egress rules are not in yet.
	srv := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}

	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if body["status"] != "starting" {
		t.Errorf("status = %v, want starting", body["status"])
	}
}

func TestServe_before_listen_is_refused(t *testing.T) {
	t.Parallel()
	// Serve spawns the goroutine that answers the sandbox; without a bound
	// socket it must report that rather than appear to have started.
	srv := newTestServer()
	if err := srv.Serve(); err == nil {
		t.Error("expected Serve to fail when Listen has not run")
	}
}

func TestListen_then_serve_answers(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	if err := srv.Listen(); err != nil {
		t.Fatalf("listen: %v", err)
	}
	t.Cleanup(func() { _ = srv.Shutdown(context.Background()) })
	if err := srv.Serve(); err != nil {
		t.Fatalf("serve: %v", err)
	}
}

func TestHandleGetRules_requires_auth(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/rules", nil)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("GET /rules without auth: status = %d, want 401", w.Code)
	}
}

func TestHandleGetRules_with_auth(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/rules", nil)
	req.Header.Set("Authorization", "Bearer "+testToken)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("GET /rules with auth: status = %d, want 200", w.Code)
	}

	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	hosts, ok := body["allowed_hosts"]
	if !ok {
		t.Error("missing allowed_hosts field")
	}
	arr, ok := hosts.([]any)
	if !ok || len(arr) != 1 {
		t.Errorf("allowed_hosts = %v, want 1 entry", hosts)
	}
}

func TestHandleGetRules_wrong_token(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/rules", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("wrong token: status = %d, want 401", w.Code)
	}
}

func TestHandlePutRules_requires_auth(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	body := `{"allowed_hosts":["new.com:80"],"allow_all":false}`
	req := httptest.NewRequest(http.MethodPut, "/rules", strings.NewReader(body))
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("PUT /rules without auth: status = %d, want 401", w.Code)
	}
}

func TestHandlePutRules_updates_allowlist(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	body := `{"allowed_hosts":["new.example.com:80"],"allow_all":false}`
	req := httptest.NewRequest(http.MethodPut, "/rules", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+testToken)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("PUT /rules: status = %d, want 200, body = %s", w.Code, w.Body.String())
	}

	// Verify rules changed via GET.
	req2 := httptest.NewRequest(http.MethodGet, "/rules", nil)
	req2.Header.Set("Authorization", "Bearer "+testToken)
	w2 := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w2, req2)

	var result map[string]any
	if err := json.Unmarshal(w2.Body.Bytes(), &result); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	hosts, ok := result["allowed_hosts"].([]any)
	if !ok {
		t.Fatalf("allowed_hosts missing or not an array: %v", result["allowed_hosts"])
	}
	if len(hosts) != 1 || hosts[0] != "new.example.com:80" {
		t.Errorf("allowed_hosts = %v, want [new.example.com:80]", hosts)
	}
}

func TestHandlePutRules_allow_all(t *testing.T) {
	t.Parallel()
	srv := newTestServer()
	body := `{"allowed_hosts":[],"allow_all":true}`
	req := httptest.NewRequest(http.MethodPut, "/rules", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+testToken)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("PUT /rules allow_all: status = %d, want 200", w.Code)
	}

	// Verify via GET.
	req2 := httptest.NewRequest(http.MethodGet, "/rules", nil)
	req2.Header.Set("Authorization", "Bearer "+testToken)
	w2 := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w2, req2)

	var result map[string]any
	if err := json.Unmarshal(w2.Body.Bytes(), &result); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}
	if result["allow_all"] != true {
		t.Errorf("allow_all = %v, want true", result["allow_all"])
	}
}
