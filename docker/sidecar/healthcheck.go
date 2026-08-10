package main

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/config"
)

// healthcheckArg is the argument Docker's HEALTHCHECK invokes this binary with.
//
// The probe is the binary rather than a shell one-liner because the image
// carries no HTTP client: its busybox ships no wget applet and there is no
// curl. Reading the port from the same configuration the server bound it from
// also keeps a non-default SIDECAR_HEALTH_PORT probing the listener that
// actually exists.
const healthcheckArg = "-healthcheck"

const healthcheckTimeout = 2 * time.Second

// runHealthcheck probes the local health endpoint.
//
// Returns:
//
//	The process exit code: 0 when the endpoint answers 200.
func runHealthcheck() int {
	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}

	addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(int(cfg.HealthPort)))
	req, err := http.NewRequest(http.MethodGet, "http://"+addr+"/healthz", nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}

	resp, err := (&http.Client{Timeout: healthcheckTimeout}).Do(req)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "healthz returned %d\n", resp.StatusCode)
		return 1
	}
	return 0
}
