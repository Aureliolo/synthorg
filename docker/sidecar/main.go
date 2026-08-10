// Sidecar network proxy for SynthOrg sandbox containers.
//
// Provides dual-layer network enforcement (DNS + DNAT transparent proxy)
// for fully rootless sandbox containers. The sidecar is the only process
// with network access; the sandbox shares its network namespace.
package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Aureliolo/synthorg/sidecar/internal/allowlist"
	"github.com/Aureliolo/synthorg/sidecar/internal/config"
	"github.com/Aureliolo/synthorg/sidecar/internal/dns"
	"github.com/Aureliolo/synthorg/sidecar/internal/health"
	"github.com/Aureliolo/synthorg/sidecar/internal/privdrop"
	"github.com/Aureliolo/synthorg/sidecar/internal/proxy"
)

const (
	version         = "0.1.0"
	shutdownTimeout = 30 * time.Second

	// The account the relay serves under, declared by the image. The
	// container enters as uid 0 because that is the only way Docker can hand
	// a process CAP_NET_ADMIN (see internal/privdrop), and leaves it here.
	servingAccount = "sidecar"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == healthcheckArg {
		os.Exit(runHealthcheck())
	}

	cfg, err := config.Load()
	if err != nil {
		logFatal("config.load.failed", "error", err.Error())
	}

	logger := newLogger(cfg.LogLevel)
	logger.Info("sidecar.starting", "version", version)

	// Resolved before anything binds or listens: an image without the account
	// cannot give up its privilege, and starting anyway would serve as root.
	account, err := privdrop.Lookup(servingAccount)
	if err != nil {
		logger.Error("privdrop.lookup.failed", "error", err.Error())
		os.Exit(1)
	}

	if cfg.AllowAll {
		logger.Warn("sidecar.allow_all", "detail", "ALL outbound connections permitted -- network isolation DISABLED")
	}

	// Build allowlist from config.
	al := allowlist.NewWithPaths(
		cfg.AllowedHosts, cfg.AllowedPaths,
		cfg.LoopbackAllowed, cfg.ResolveInterval, cfg.AllowAll,
	)
	al.Start()

	// Start DNS server.
	dnsServer, err := dns.NewServer(al, cfg.DNSAllowed, logger)
	if err != nil {
		logger.Error("dns.init.failed", "error", err.Error())
		os.Exit(1)
	}
	if err := dnsServer.Start(); err != nil {
		logger.Error("dns.start.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("dns.started", "port", 53)

	// Start health + admin API.
	adminServer := health.NewServer(cfg.HealthPort, al, cfg.AdminToken, cfg.AllowedHosts, cfg.AllowAll, logger)
	if err := adminServer.Start(); err != nil {
		logger.Error("health.start.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("health.started", "port", cfg.HealthPort)

	// Bind before the drop, serve after it: a low port needs the privilege to
	// bind, and nothing should be relayed by a process that can still edit
	// the rules doing the relaying.
	tcpProxy := proxy.New(cfg.ProxyPort, al, logger)
	if err := tcpProxy.Listen(); err != nil {
		logger.Error("proxy.listen.failed", "error", err.Error())
		os.Exit(1)
	}

	plan := proxy.PlanRules(cfg.ProxyPort, cfg.DNSAllowed, account.UID)
	if err := proxy.InstallRules(context.Background(), plan); err != nil {
		logger.Error("dnat.setup.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("dnat.setup.complete", "proxy_port", cfg.ProxyPort)

	if err := privdrop.Drop(account); err != nil {
		logger.Error("privdrop.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("privdrop.complete", "uid", account.UID, "gid", account.GID)

	if err := tcpProxy.Serve(); err != nil {
		logger.Error("proxy.start.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("proxy.started", "port", cfg.ProxyPort)

	// Wait for shutdown signal.
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	sig := <-sigCh
	logger.Info("sidecar.signal", "signal", sig.String())

	// Graceful shutdown.
	ctx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()

	if err := tcpProxy.Shutdown(ctx); err != nil {
		logger.Error("proxy.shutdown.failed", "error", err.Error())
	}
	dnsServer.Stop()
	if err := adminServer.Shutdown(ctx); err != nil {
		logger.Error("health.shutdown.failed", "error", err.Error())
	}
	al.Stop()

	logger.Info("sidecar.shutdown.complete")
}
