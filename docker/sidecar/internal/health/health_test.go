package health_test

import (
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

func TestHealthzOK(t *testing.T) {
	srv := newTestServer()
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

func TestHealthzNoAuth(t *testing.T) {
	srv := newTestServer()
	// Health check must work WITHOUT auth (Docker healthcheck).
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("healthz should not require auth, got status %d", w.Code)
	}
}

func TestGetRulesRequiresAuth(t *testing.T) {
	srv := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/rules", nil)
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("GET /rules without auth: status = %d, want 401", w.Code)
	}
}

func TestGetRulesWithAuth(t *testing.T) {
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

func TestGetRulesWrongToken(t *testing.T) {
	srv := newTestServer()
	req := httptest.NewRequest(http.MethodGet, "/rules", nil)
	req.Header.Set("Authorization", "Bearer wrong-token")
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("wrong token: status = %d, want 401", w.Code)
	}
}

func TestPutRulesRequiresAuth(t *testing.T) {
	srv := newTestServer()
	body := `{"allowed_hosts":["new.com:80"],"allow_all":false}`
	req := httptest.NewRequest(http.MethodPut, "/rules", strings.NewReader(body))
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("PUT /rules without auth: status = %d, want 401", w.Code)
	}
}

func TestPutRulesUpdatesAllowlist(t *testing.T) {
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
	hosts := result["allowed_hosts"].([]any)
	if len(hosts) != 1 || hosts[0] != "new.example.com:80" {
		t.Errorf("allowed_hosts = %v, want [new.example.com:80]", hosts)
	}
}

func TestPutRulesAllowAll(t *testing.T) {
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
