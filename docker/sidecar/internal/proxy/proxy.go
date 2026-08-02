package proxy

import (
	"context"
	"fmt"
	"io"
	"net"
	"sync"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/allowlist"
)

const dialTimeout = 5 * time.Second

// Logger is the minimal logging interface used by the proxy.
type Logger interface {
	Info(msg string, kvs ...any)
	Warn(msg string, kvs ...any)
	Error(msg string, kvs ...any)
}

// Proxy is a transparent TCP proxy that enforces an allowlist.
// Allow-all state is owned by the Allowlist (updated atomically via
// the admin API) -- the proxy does not keep a separate copy.
type Proxy struct {
	port   uint16
	al     *allowlist.Allowlist
	dnat   *DNATManager
	logger Logger

	listener net.Listener
	wg       sync.WaitGroup
	done     chan struct{}
}

// New creates a transparent TCP proxy. The allow-all state is read
// from the Allowlist at connection time so admin API updates take
// effect immediately.
func New(port uint16, al *allowlist.Allowlist, dnat *DNATManager, logger Logger) *Proxy {
	return &Proxy{
		port:   port,
		al:     al,
		dnat:   dnat,
		logger: logger,
		done:   make(chan struct{}),
	}
}

// Start begins accepting TCP connections.
func (p *Proxy) Start() error {
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", p.port))
	if err != nil {
		return fmt.Errorf("proxy listen: %w", err)
	}
	p.listener = ln
	go p.acceptLoop()
	return nil
}

// Shutdown gracefully shuts down the proxy, draining active connections.
func (p *Proxy) Shutdown(ctx context.Context) error {
	close(p.done)
	if p.listener != nil {
		_ = p.listener.Close()
	}

	waitCh := make(chan struct{})
	go func() {
		p.wg.Wait()
		close(waitCh)
	}()

	select {
	case <-waitCh:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (p *Proxy) acceptLoop() {
	for {
		conn, err := p.listener.Accept()
		if err != nil {
			select {
			case <-p.done:
				return
			default:
				if p.logger != nil {
					p.logger.Error("proxy.accept.error", "error", err.Error())
				}
				continue
			}
		}
		p.wg.Add(1)
		go func() {
			defer p.wg.Done()
			p.handleConn(conn)
		}()
	}
}

// admit reports whether the layer-4 allowlist permits this destination,
// resetting the connection rather than closing it gracefully when it does
// not, so the sandbox sees "connection refused" instead of a timeout.
func (p *Proxy) admit(conn net.Conn, destIP string, destPort uint16) bool {
	if !p.al.IsAllowedIP(destIP, destPort) {
		if p.logger != nil {
			p.logger.Info("proxy.connection.blocked",
				"dst_ip", destIP, "dst_port", destPort,
				"reason", "not in allowlist",
			)
		}
		if tc, ok := conn.(*net.TCPConn); ok {
			_ = tc.SetLinger(0)
		}
		return false
	}
	if p.logger != nil {
		if p.al.IsAllowAll() {
			p.logger.Warn("proxy.connection.allow_all",
				"dst_ip", destIP, "dst_port", destPort,
			)
		} else {
			p.logger.Info("proxy.connection.allowed",
				"dst_ip", destIP, "dst_port", destPort,
			)
		}
	}
	return true
}

func (p *Proxy) handleConn(conn net.Conn) {
	defer conn.Close()

	destIP, destPort, err := GetOriginalDst(conn)
	if err != nil {
		if p.logger != nil {
			p.logger.Error("proxy.original_dst.failed", "error", err.Error())
		}
		return
	}

	if !p.admit(conn, destIP, destPort) {
		return
	}

	// Dial upstream.
	upstream, err := net.DialTimeout("tcp",
		fmt.Sprintf("%s:%d", destIP, destPort), dialTimeout)
	if err != nil {
		if p.logger != nil {
			p.logger.Error("proxy.dial.failed",
				"dst_ip", destIP, "dst_port", destPort,
				"error", err.Error(),
			)
		}
		return
	}
	defer upstream.Close()

	// A destination narrowed to path prefixes is relayed at HTTP level, so
	// sharing one port with unrelated routes does not grant them.
	if prefixes, narrowed := p.al.PathPrefixes(destIP, destPort); narrowed {
		p.relayGuarded(conn, upstream, destIP, destPort, prefixes)
		return
	}

	p.relayRaw(conn, upstream)
}

// relayRaw copies bytes in both directions until either side closes. Used for
// a destination with no path narrowing, where the sidecar has no reason to
// understand the protocol being carried.
func (p *Proxy) relayRaw(conn net.Conn, upstream net.Conn) {
	var copyWg sync.WaitGroup
	copyWg.Add(1)
	go func() {
		defer copyWg.Done()
		if _, err := io.Copy(upstream, conn); err != nil && p.logger != nil {
			p.logger.Warn("proxy.copy.upstream.error", "error", err.Error())
		}
		// Signal the other direction to stop.
		if tc, ok := upstream.(*net.TCPConn); ok {
			_ = tc.CloseWrite()
		}
	}()
	if _, err := io.Copy(conn, upstream); err != nil && p.logger != nil {
		p.logger.Warn("proxy.copy.downstream.error", "error", err.Error())
	}
	if tc, ok := conn.(*net.TCPConn); ok {
		_ = tc.CloseWrite()
	}
	copyWg.Wait()
}
