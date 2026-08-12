// Package dns provides a DNS server that enforces the allowlist.
// Allowed hostnames are forwarded to upstream DNS; denied ones get NXDOMAIN.
package dns

import (
	"bufio"
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/allowlist"
)

const dnsDialTimeout = 3 * time.Second

// Logger is the minimal logging interface.
type Logger interface {
	Info(msg string, kvs ...any)
	Warn(msg string, kvs ...any)
	Error(msg string, kvs ...any)
}

// Server is a DNS server that filters queries based on the allowlist.
type Server struct {
	al         *allowlist.Allowlist
	dnsAllowed bool
	logger     Logger
	udpConn    *net.UDPConn
	tcpLn      net.Listener
	upstream   string
	done       chan struct{}
	wg         sync.WaitGroup
}

// NewServer creates a DNS server. Returns an error if no upstream
// DNS server can be determined from /etc/resolv.conf -- the sidecar
// must not start without a known upstream.
func NewServer(al *allowlist.Allowlist, dnsAllowed bool, logger Logger) (*Server, error) {
	upstream := findUpstreamDNS()
	if upstream == "" {
		return nil, fmt.Errorf("no upstream DNS found in /etc/resolv.conf -- sidecar cannot enforce DNS filtering")
	}
	return &Server{
		al:         al,
		dnsAllowed: dnsAllowed,
		logger:     logger,
		upstream:   upstream,
		done:       make(chan struct{}),
	}, nil
}

// Listen binds UDP and TCP port 53 without serving on them.
//
// Split from Serve because port 53 is privileged: the bind needs the
// capability the process is about to give up, while the queries this answers
// are raw bytes from the sandbox sharing this network namespace, so nothing
// should parse them while the process can still edit the rules confining it.
//
// Returns:
//
//	An error when either socket cannot be bound.
func (s *Server) Listen() error {
	udpAddr := &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 53}
	udpConn, err := net.ListenUDP("udp", udpAddr)
	if err != nil {
		return fmt.Errorf("dns udp listen: %w", err)
	}

	tcpLn, err := net.Listen("tcp", "127.0.0.1:53")
	if err != nil {
		_ = udpConn.Close()
		return fmt.Errorf("dns tcp listen: %w", err)
	}

	s.udpConn = udpConn
	s.tcpLn = tcpLn
	return nil
}

// Serve begins answering queries on the sockets Listen bound.
//
// Returns:
//
//	An error when Listen has not run.
func (s *Server) Serve() error {
	if s.udpConn == nil || s.tcpLn == nil {
		return fmt.Errorf("dns serve: not listening")
	}
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		s.serveUDP()
	}()
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		s.serveTCP()
	}()
	return nil
}

// Stop shuts down the DNS server.
func (s *Server) Stop() {
	select {
	case <-s.done:
		return
	default:
		close(s.done)
	}
	if s.udpConn != nil {
		_ = s.udpConn.Close()
	}
	if s.tcpLn != nil {
		_ = s.tcpLn.Close()
	}
	s.wg.Wait()
}

func (s *Server) serveUDP() {
	buf := make([]byte, 4096)
	for {
		n, addr, err := s.udpConn.ReadFromUDP(buf)
		if err != nil {
			select {
			case <-s.done:
				return
			default:
				continue
			}
		}
		query := make([]byte, n)
		copy(query, buf[:n])
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			s.handleUDP(query, addr)
		}()
	}
}

func (s *Server) serveTCP() {
	for {
		conn, err := s.tcpLn.Accept()
		if err != nil {
			select {
			case <-s.done:
				return
			default:
				continue
			}
		}
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			s.handleTCP(conn)
		}()
	}
}

func (s *Server) handleUDP(query []byte, addr *net.UDPAddr) {
	resp := s.processQuery(query)
	if resp != nil {
		_, _ = s.udpConn.WriteToUDP(resp, addr)
	}
}

func (s *Server) handleTCP(conn net.Conn) {
	defer conn.Close()
	reader := bufio.NewReader(conn)

	// TCP DNS: 2-byte length prefix. Use io.ReadFull to ensure
	// partial reads don't cause misparsed messages.
	var lenBuf [2]byte
	if _, err := io.ReadFull(reader, lenBuf[:]); err != nil {
		return
	}
	msgLen := binary.BigEndian.Uint16(lenBuf[:])
	query := make([]byte, msgLen)
	if _, err := io.ReadFull(reader, query); err != nil {
		return
	}

	resp := s.processQuery(query)
	if resp == nil {
		return
	}

	// Write response with length prefix -- use full writes. The prefix is a
	// uint16 by RFC 1035, so a response that cannot be described by one is
	// undeliverable rather than silently truncated to its low 16 bits.
	if len(resp) > math.MaxUint16 {
		return
	}
	binary.BigEndian.PutUint16(lenBuf[:], uint16(len(resp))) //nolint:gosec // bounded by the MaxUint16 check above
	if _, err := conn.Write(lenBuf[:]); err != nil {
		return
	}
	if _, err := conn.Write(resp); err != nil {
		return
	}
}

func (s *Server) processQuery(query []byte) []byte {
	hostname := ExtractQueryHostname(query)
	if hostname == "" {
		// Can't parse -- return NXDOMAIN instead of forwarding
		// to prevent unparseable queries from bypassing the allowlist.
		return BuildNXDOMAIN(query)
	}

	// When DNS is disabled (SIDECAR_DNS_ALLOWED=0), deny all queries
	// regardless of allowlist. DNAT DROP rules are the primary gate
	// but this acts as defense-in-depth for loopback queries.
	if !s.dnsAllowed {
		if s.logger != nil {
			s.logger.Info("dns.query.denied", "host", hostname, "reason", "dns disabled")
		}
		return BuildNXDOMAIN(query)
	}

	if s.al.IsAllowedHostname(hostname) {
		if s.logger != nil {
			s.logger.Info("dns.query.allowed", "host", hostname)
		}
		return s.forwardToUpstream(query)
	}

	if s.logger != nil {
		s.logger.Info("dns.query.denied", "host", hostname, "reason", "not in allowlist")
	}
	return BuildNXDOMAIN(query)
}

func (s *Server) forwardToUpstream(query []byte) []byte {
	if s.upstream == "" {
		return BuildNXDOMAIN(query)
	}

	conn, err := net.DialTimeout("udp", s.upstream, dnsDialTimeout)
	if err != nil {
		return BuildNXDOMAIN(query)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(dnsDialTimeout))

	if _, err := conn.Write(query); err != nil {
		return BuildNXDOMAIN(query)
	}

	buf := make([]byte, 4096)
	n, err := conn.Read(buf)
	if err != nil {
		return BuildNXDOMAIN(query)
	}
	resp := make([]byte, n)
	copy(resp, buf[:n])
	return resp
}

// ExtractQueryHostname extracts the queried hostname from a DNS query.
// Returns empty string if the query can't be parsed.
func ExtractQueryHostname(query []byte) string {
	// DNS header is 12 bytes.
	if len(query) < 13 {
		return ""
	}

	// Question section starts at byte 12.
	pos := 12
	var parts []string
	for pos < len(query) {
		length := int(query[pos])
		if length == 0 {
			break
		}
		pos++
		if pos+length > len(query) {
			return ""
		}
		parts = append(parts, string(query[pos:pos+length]))
		pos += length
	}
	if len(parts) == 0 {
		return ""
	}
	return strings.ToLower(strings.Join(parts, "."))
}

// BuildNXDOMAIN creates a minimal NXDOMAIN response for the given query.
func BuildNXDOMAIN(query []byte) []byte {
	if len(query) < 12 {
		return nil
	}
	resp := make([]byte, len(query))
	copy(resp, query)

	// Set QR=1 (response), RCODE=3 (NXDOMAIN).
	resp[2] = 0x81 // QR=1, RD=1
	resp[3] = 0x83 // RA=1, RCODE=3 (NXDOMAIN)

	// Zero answer, authority, additional counts.
	resp[6] = 0
	resp[7] = 0
	resp[8] = 0
	resp[9] = 0
	resp[10] = 0
	resp[11] = 0

	return resp
}

// findUpstreamDNS reads /etc/resolv.conf for the first nameserver.
// Returns empty string if no nameserver is found -- the caller must
// handle this as a startup failure. No fallback: if we can't
// determine the upstream DNS, the sidecar cannot enforce DNS
// filtering and must refuse to start.
func findUpstreamDNS() string {
	f, err := os.Open("/etc/resolv.conf")
	if err != nil {
		return ""
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "nameserver") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				ns := fields[1]
				// Skip IPv6 nameservers -- sidecar is IPv4-only.
				if strings.Contains(ns, ":") {
					continue
				}
				return ns + ":53"
			}
		}
	}
	return ""
}
