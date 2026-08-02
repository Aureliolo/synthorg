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
	"github.com/Aureliolo/synthorg/sidecar/internal/proxy"
)

const (
	version         = "0.1.0"
	shutdownTimeout = 30 * time.Second
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		logFatal("config.load.failed", "error", err.Error())
	}

	logger := newLogger(cfg.LogLevel)
	logger.Info("sidecar.starting", "version", version)

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

	// Setup DNAT rules.
	dnatMgr := proxy.NewDNATManager(cfg.ProxyPort, cfg.DNSAllowed)
	if err := dnatMgr.Setup(context.Background()); err != nil {
		logger.Error("dnat.setup.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("dnat.setup.complete", "proxy_port", cfg.ProxyPort)

	// Start TCP proxy.
	tcpProxy := proxy.New(cfg.ProxyPort, al, dnatMgr, logger)
	if err := tcpProxy.Start(); err != nil {
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
	if err := dnatMgr.Cleanup(ctx); err != nil {
		logger.Error("dnat.cleanup.failed", "error", err.Error())
	}
	dnsServer.Stop()
	if err := adminServer.Shutdown(ctx); err != nil {
		logger.Error("health.shutdown.failed", "error", err.Error())
	}
	al.Stop()

	logger.Info("sidecar.shutdown.complete")
}
