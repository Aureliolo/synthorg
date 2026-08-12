// Package health provides the health check and admin API server.
package health

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/allowlist"
	"github.com/Aureliolo/synthorg/sidecar/internal/config"
)

// Logger is the minimal logging interface used by the health server.
type Logger interface {
	Info(msg string, kvs ...any)
	Warn(msg string, kvs ...any)
}

// Server is the health check and admin API HTTP server.
type Server struct {
	port       uint16
	al         *allowlist.Allowlist
	adminToken string
	logger     Logger
	server     *http.Server
	listener   net.Listener
	startTime  time.Time

	// ready reports that egress enforcement is installed and privilege has
	// been given up. Docker flips a container to healthy off one successful
	// probe, and the caller joins the sandbox's network namespace to this one
	// the moment it reads healthy, so answering ok while the DNAT rules are
	// still going in would hand a sandbox an unfiltered namespace.
	ready atomic.Bool

	mu       sync.RWMutex
	rawRules []config.HostPort
	allowAll bool
}

// NewServer creates a new health/admin server.
func NewServer(port uint16, al *allowlist.Allowlist, adminToken string, initialRules []config.HostPort, allowAll bool, logger Logger) *Server {
	return &Server{
		port:       port,
		al:         al,
		adminToken: adminToken,
		logger:     logger,
		startTime:  time.Now(),
		rawRules:   initialRules,
		allowAll:   allowAll,
	}
}

// Handler returns the HTTP handler (useful for testing without starting a listener).
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealthz)
	mux.HandleFunc("GET /rules", s.requireAuth(s.handleGetRules))
	mux.HandleFunc("PUT /rules", s.requireAuth(s.handlePutRules))
	return mux
}

// Listen binds the configured port without serving on it.
//
// Split from Serve for the reason the DNS server is: the admin API is
// reachable from the sandbox sharing this network namespace, so nothing
// should answer on it while the process still holds NET_ADMIN.
//
// Returns:
//
//	An error when the port cannot be bound.
func (s *Server) Listen() error {
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", s.port))
	if err != nil {
		return fmt.Errorf("health listener: %w", err)
	}
	s.listener = listener
	return nil
}

// Serve begins answering on the socket Listen bound.
//
// Returns:
//
//	An error when Listen has not run.
func (s *Server) Serve() error {
	if s.listener == nil {
		return fmt.Errorf("health serve: not listening")
	}
	listener := s.listener
	s.server = &http.Server{
		Handler:           s.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       30 * time.Second,
	}
	go func() {
		if err := s.server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			if s.logger != nil {
				s.logger.Warn("health.serve.error", "error", err.Error())
			}
		}
	}()
	return nil
}

// MarkReady records that egress enforcement is installed and privilege given up.
func (s *Server) MarkReady() {
	s.ready.Store(true)
}

// Shutdown gracefully shuts down the server.
func (s *Server) Shutdown(ctx context.Context) error {
	if s.server != nil {
		return s.server.Shutdown(ctx)
	}
	return nil
}

func (s *Server) handleHealthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if !s.ready.Load() {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{"status": "starting"}) //nolint:errcheck // HTTP response write errors are non-actionable in handlers
		return
	}
	uptime := int64(time.Since(s.startTime).Seconds())
	// "ok" aligns with the main API /healthz liveness body and the
	// /rules response below; Docker's own State.Health stays "healthy".
	resp := map[string]any{
		"status":         "ok",
		"uptime_seconds": uptime,
	}
	json.NewEncoder(w).Encode(resp) //nolint:errcheck // HTTP response write errors are non-actionable in handlers
}

func (s *Server) handleGetRules(w http.ResponseWriter, _ *http.Request) {
	s.mu.RLock()
	rules := s.rawRules
	allowAll := s.allowAll
	s.mu.RUnlock()

	hostStrs := make([]string, len(rules))
	for i, hp := range rules {
		hostStrs[i] = fmt.Sprintf("%s:%d", hp.Host, hp.Port)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{ //nolint:errcheck // HTTP response write errors are non-actionable in handlers
		"allowed_hosts": hostStrs,
		"allow_all":     allowAll,
	})
}

type rulesRequest struct {
	AllowedHosts []string `json:"allowed_hosts"`
	AllowAll     bool     `json:"allow_all"`
}

func (s *Server) handlePutRules(w http.ResponseWriter, r *http.Request) {
	// Bound the request body (defence-in-depth): even an authenticated
	// caller should not be able to stream an unbounded body into memory.
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	var req rulesRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		s.jsonError(w, "invalid JSON", http.StatusBadRequest)
		return
	}

	var hosts []config.HostPort
	for _, entry := range req.AllowedHosts {
		idx := strings.LastIndex(entry, ":")
		if idx < 1 {
			s.jsonError(w, fmt.Sprintf("invalid allowed_hosts entry: %q", entry), http.StatusBadRequest)
			return
		}
		host := strings.ToLower(strings.TrimSpace(entry[:idx]))
		portStr := strings.TrimSpace(entry[idx+1:])
		port, err := strconv.ParseUint(portStr, 10, 16)
		if err != nil || port < 1 || port > 65535 {
			s.jsonError(w, fmt.Sprintf("invalid allowed_hosts entry: %q", entry), http.StatusBadRequest)
			return
		}
		hosts = append(hosts, config.HostPort{Host: host, Port: uint16(port)})
	}

	// Update atomically: hold lock for both rawRules and allowAll,
	// then update the allowlist outside the lock.
	s.mu.Lock()
	s.rawRules = hosts
	s.allowAll = req.AllowAll
	s.mu.Unlock()

	s.al.UpdateRules(hosts, req.AllowAll)

	if s.logger != nil {
		s.logger.Info("rules.updated", "count", len(hosts), "allow_all", req.AllowAll)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"}) //nolint:errcheck // HTTP response write errors are non-actionable in handlers
}

func (s *Server) requireAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		auth := r.Header.Get("Authorization")
		if !strings.HasPrefix(auth, "Bearer ") {
			s.jsonError(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		token := strings.TrimSpace(strings.TrimPrefix(auth, "Bearer "))
		if token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(s.adminToken)) != 1 {
			s.jsonError(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next(w, r)
	}
}

func (*Server) jsonError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": msg}) //nolint:errcheck // HTTP response write errors are non-actionable in handlers
}
