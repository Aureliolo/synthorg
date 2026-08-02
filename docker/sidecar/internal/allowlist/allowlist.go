// Package allowlist maintains a resolved IP:port allowlist with background
// re-resolution of hostnames.
package allowlist

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/config"
)

// Allowlist is a thread-safe allowlist that resolves hostnames to IPs.
type Allowlist struct {
	mu        sync.RWMutex
	resolved  map[string]bool     // "ip:port" -> true
	hostnames map[string]bool     // "hostname" -> true (any port)
	paths     map[string][]string // "ip:port" -> allowed URL path prefixes
	raw       []config.HostPort
	rawPaths  []config.PathRule
	loopback  bool
	allowAll  atomic.Bool

	resolveMu       sync.Mutex // serializes concurrent resolve() calls
	resolveInterval time.Duration
	stopCh          chan struct{}
	startOnce       sync.Once
}

// New creates an Allowlist from the given host:port entries.
// IP entries are added directly; hostnames are resolved on first call.
// Set resolveInterval to 0 to disable background re-resolution.
func New(hosts []config.HostPort, loopback bool, resolveIntervalSec int, allowAll ...bool) *Allowlist {
	return NewWithPaths(hosts, nil, loopback, resolveIntervalSec, allowAll...)
}

// NewWithPaths creates an Allowlist that additionally narrows the listed
// destinations to a set of URL path prefixes, enforced per HTTP request.
func NewWithPaths(
	hosts []config.HostPort,
	paths []config.PathRule,
	loopback bool,
	resolveIntervalSec int,
	allowAll ...bool,
) *Allowlist {
	al := &Allowlist{
		raw:      hosts,
		rawPaths: paths,
		loopback: loopback,
		stopCh:   make(chan struct{}),
	}
	if len(allowAll) > 0 {
		al.allowAll.Store(allowAll[0])
	}
	if resolveIntervalSec > 0 {
		al.resolveInterval = time.Duration(resolveIntervalSec) * time.Second
	}
	al.resolve()
	return al
}

// Start begins background re-resolution if an interval was configured.
// Idempotent -- safe to call multiple times.
func (a *Allowlist) Start() {
	if a.resolveInterval <= 0 {
		return
	}
	a.startOnce.Do(func() {
		go a.backgroundResolve()
	})
}

// Stop terminates the background re-resolution goroutine.
func (a *Allowlist) Stop() {
	select {
	case <-a.stopCh:
	default:
		close(a.stopCh)
	}
}

// IsAllowAll reports whether allow-all mode is currently active.
func (a *Allowlist) IsAllowAll() bool {
	return a.allowAll.Load()
}

// IsAllowedIP checks whether the given IP:port is permitted.
func (a *Allowlist) IsAllowedIP(ip string, port uint16) bool {
	if a.allowAll.Load() {
		return true
	}
	if isLoopback(ip) {
		return a.loopback
	}
	key := fmt.Sprintf("%s:%d", ip, port)
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.resolved[key]
}

// IsAllowedHostname checks whether the given hostname has any allowed entry.
func (a *Allowlist) IsAllowedHostname(hostname string) bool {
	if a.allowAll.Load() {
		return true
	}
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.hostnames[strings.ToLower(hostname)]
}

// UpdateRules atomically replaces the allowlist with new rules.
// DNS resolution runs on the new hosts before swapping so
// IsAllowedIP/IsAllowedHostname never see a stale-allow window.
// Acquires resolveMu to prevent backgroundResolve from overwriting
// the fresh maps with stale ones.
func (a *Allowlist) UpdateRules(hosts []config.HostPort, allowAll bool) {
	a.resolveMu.Lock()
	a.mu.RLock()
	rawPaths := a.rawPaths
	a.mu.RUnlock()

	resolved, hostnames, answers := resolveHosts(hosts)
	// Path rules key off the same DNS answers the host rules just used, so a
	// rewritten allowlist cannot leave a destination L4-allowed while its L7
	// narrowing points at a different address.
	paths := resolvePaths(rawPaths, answers)

	a.mu.Lock()
	a.raw = hosts
	a.resolved = resolved
	a.hostnames = hostnames
	a.paths = paths
	a.mu.Unlock()
	a.resolveMu.Unlock()
	a.allowAll.Store(allowAll)
}

// PathPrefixes returns the URL path prefixes this destination is narrowed
// to, and whether any narrowing applies at all.
func (a *Allowlist) PathPrefixes(ip string, port uint16) ([]string, bool) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	prefixes, ok := a.paths[fmt.Sprintf("%s:%d", ip, port)]
	return prefixes, ok
}

// HasPathRules reports whether any L7 narrowing is configured at all.
func (a *Allowlist) HasPathRules() bool {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return len(a.rawPaths) > 0
}

func (a *Allowlist) resolve() {
	// Serialize concurrent resolve() calls to avoid redundant DNS lookups.
	a.resolveMu.Lock()
	defer a.resolveMu.Unlock()

	a.mu.RLock()
	raw := make([]config.HostPort, len(a.raw))
	copy(raw, a.raw)
	rawPaths := make([]config.PathRule, len(a.rawPaths))
	copy(rawPaths, a.rawPaths)
	a.mu.RUnlock()

	resolved, hostnames, answers := resolveHosts(raw)
	paths := resolvePaths(rawPaths, answers)

	a.mu.Lock()
	a.resolved = resolved
	a.hostnames = hostnames
	a.paths = paths
	a.mu.Unlock()
}

// resolvePaths maps each path rule's host:port onto the resolved IP:port keys
// the proxy sees after DNAT, so the L7 check keys off the same value the L4
// check already matched.
//
// It takes the answers the host resolution just used rather than looking up
// again. A second lookup can return a different address for the same name
// (round-robin, a record that changed between calls), which would leave an
// address L4-allowed with no path rule attached to it: the narrowing would
// silently not apply to exactly the destination the sandbox reaches.
func resolvePaths(rules []config.PathRule, answers map[string][]string) map[string][]string {
	paths := make(map[string][]string)
	for _, rule := range rules {
		host := strings.ToLower(rule.Host)
		if ip := net.ParseIP(rule.Host); ip != nil {
			key := fmt.Sprintf("%s:%d", rule.Host, rule.Port)
			paths[key] = append(paths[key], rule.Prefix)
			continue
		}
		for _, ip := range answers[host] {
			key := fmt.Sprintf("%s:%d", ip, rule.Port)
			paths[key] = append(paths[key], rule.Prefix)
		}
	}
	return paths
}

// lookupHost resolves a hostname to its addresses, empty on failure.
func lookupHost(host string) []string {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ips, err := net.DefaultResolver.LookupHost(ctx, host)
	if err != nil {
		return nil
	}
	return ips
}

// resolveHosts builds resolved IP:port and hostname maps from the given host
// entries. DNS lookups run outside any lock.
//
// The third return value is the per-hostname answer set these maps were built
// from, so path rules can be keyed off the same addresses instead of asking
// DNS again and possibly getting different ones.
func resolveHosts(
	hosts []config.HostPort,
) (map[string]bool, map[string]bool, map[string][]string) {
	resolved := make(map[string]bool)
	hostnames := make(map[string]bool)
	answers := make(map[string][]string)

	for _, hp := range hosts {
		host := strings.ToLower(hp.Host)
		hostnames[host] = true

		// If already an IP, add directly.
		if ip := net.ParseIP(hp.Host); ip != nil {
			resolved[fmt.Sprintf("%s:%d", hp.Host, hp.Port)] = true
			continue
		}

		if _, seen := answers[host]; !seen {
			answers[host] = lookupHost(hp.Host)
		}
		for _, ip := range answers[host] {
			resolved[fmt.Sprintf("%s:%d", ip, hp.Port)] = true
		}
	}
	return resolved, hostnames, answers
}

func (a *Allowlist) backgroundResolve() {
	ticker := time.NewTicker(a.resolveInterval)
	defer ticker.Stop()
	for {
		select {
		case <-a.stopCh:
			return
		case <-ticker.C:
			a.resolve()
		}
	}
}

func isLoopback(ip string) bool {
	parsed := net.ParseIP(ip)
	return parsed != nil && parsed.IsLoopback()
}
