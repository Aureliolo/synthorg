package main

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/config"
	"github.com/Aureliolo/synthorg/sidecar/internal/privdrop"
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

// shedProbePrivilege gives up root when the probe was started as it.
//
// Docker execs a HEALTHCHECK as the image's configured user, which here is
// uid 0 because that is the only way the entrypoint can receive CAP_NET_ADMIN
// (see internal/privdrop). Nothing about probing an HTTP endpoint needs that,
// so the probe becomes the serving account immediately rather than spending
// its life as a root process respawned every two seconds.
//
// Returns:
//
//	An error when the process is root and cannot stop being it. Reporting
//	healthy from an image whose account database cannot supply the serving
//	account would certify a container the entrypoint could not have started.
func shedProbePrivilege() error {
	if os.Geteuid() != 0 {
		return nil
	}
	account, err := privdrop.Lookup(servingAccount)
	if err != nil {
		return err
	}
	return privdrop.Drop(account)
}

// runHealthcheck probes the local health endpoint.
//
// Returns:
//
//	The process exit code: 0 when the endpoint answers 200.
func runHealthcheck() int {
	if err := shedProbePrivilege(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}

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
