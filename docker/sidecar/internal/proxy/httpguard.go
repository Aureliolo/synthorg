package proxy

import (
	"bufio"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
)

// relayHTTPGuarded relays a plaintext HTTP connection to upstream, refusing
// any request whose path is outside the destination's allowed prefixes.
//
// A layer-4 allowlist says "reach this address"; when several routes share
// one process that is strictly weaker than what the caller asked for. This
// parses each request off the wire, checks its path, and only then forwards
// it, so keep-alive cannot smuggle a second request past a check performed
// once on the first.
//
// The connection must be plaintext HTTP. TLS is refused outright rather than
// tunnelled: the path is unreadable inside a TLS record, so forwarding it
// would silently restore the layer-4-only behaviour this exists to replace.
func relayHTTPGuarded(
	clientReader *bufio.Reader,
	client net.Conn,
	upstream net.Conn,
	prefixes []string,
	onBlocked func(reason, path string),
) {
	upstreamReader := bufio.NewReader(upstream)

	for {
		req, err := http.ReadRequest(clientReader)
		if err != nil {
			if !errors.Is(err, io.EOF) && !isClosedConn(err) {
				onBlocked("unparseable request", "")
			}
			return
		}

		if !pathAllowed(req.URL.Path, prefixes) {
			onBlocked("path outside allowed prefixes", req.URL.Path)
			writeForbidden(client)
			_ = req.Body.Close()
			return
		}

		if !forwardOne(req, client, upstream, upstreamReader) {
			return
		}
	}
}

// forwardOne relays one already-approved request and its response.
//
// Request and response are read and written in lockstep, one apiece, so a
// response body can never be mistaken for the next request on the wire.
//
// Returns:
//
//	whether the connection may carry a further request.
func forwardOne(
	req *http.Request,
	client net.Conn,
	upstream net.Conn,
	upstreamReader *bufio.Reader,
) bool {
	writeErr := req.Write(upstream)
	_ = req.Body.Close()
	if writeErr != nil {
		return false
	}

	resp, err := http.ReadResponse(upstreamReader, req)
	if err != nil {
		return false
	}
	relayErr := resp.Write(client)
	_ = resp.Body.Close()
	return relayErr == nil && !req.Close && !resp.Close
}

// relayGuarded runs the HTTP-level relay for a narrowed destination,
// refusing a TLS connection outright because its paths are unreadable.
func (p *Proxy) relayGuarded(
	conn net.Conn,
	upstream net.Conn,
	destIP string,
	destPort uint16,
	prefixes []string,
) {
	onBlocked := func(reason, path string) {
		if p.logger != nil {
			p.logger.Info("proxy.request.blocked",
				"dst_ip", destIP, "dst_port", destPort,
				"path", path, "reason", reason,
			)
		}
	}
	clientReader := bufio.NewReader(conn)
	if looksLikeTLS(clientReader) {
		onBlocked("TLS to a path-narrowed destination", "")
		return
	}
	relayHTTPGuarded(clientReader, conn, upstream, prefixes, onBlocked)
}

// pathAllowed reports whether path sits under one of the allowed prefixes.
// Matching is prefix-on-a-segment-boundary, so "/api/v1/gateway" never
// admits "/api/v1/gateway-admin".
func pathAllowed(path string, prefixes []string) bool {
	for _, prefix := range prefixes {
		if path == prefix {
			return true
		}
		trimmed := strings.TrimSuffix(prefix, "/")
		if strings.HasPrefix(path, trimmed+"/") {
			return true
		}
	}
	return false
}

// looksLikeTLS reports whether the buffered bytes open a TLS handshake.
// A TLS ClientHello starts with the handshake content type (0x16) followed
// by a major version of 0x03.
func looksLikeTLS(r *bufio.Reader) bool {
	head, err := r.Peek(2)
	if err != nil || len(head) < 2 {
		return false
	}
	return head[0] == 0x16 && head[1] == 0x03
}

func writeForbidden(w io.Writer) {
	_, _ = io.WriteString(w,
		"HTTP/1.1 403 Forbidden\r\n"+
			"Content-Length: 0\r\n"+
			"Connection: close\r\n\r\n")
}

func isClosedConn(err error) bool {
	return errors.Is(err, net.ErrClosed) ||
		strings.Contains(err.Error(), "use of closed network connection") ||
		strings.Contains(err.Error(), "connection reset by peer")
}
