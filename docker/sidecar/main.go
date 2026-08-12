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

// dnatTimeout bounds the netfilter setup.
//
// InstallRules shells out to iptables-nft, which can block on the xtables
// lock under host contention. An unbounded wait there parks the process in
// its most privileged state indefinitely and never starts the relay, so a
// stuck subprocess fails loud instead.
const dnatTimeout = 15 * time.Second

// bindAll binds every listener without serving on any of them.
//
// Exits the process when any bind fails: a sidecar that cannot bind cannot
// enforce, and starting the sandbox against it would leave egress open.
func bindAll(logger *logger, dnsServer *dns.Server, adminServer *health.Server, tcpProxy *proxy.Proxy) {
	if err := dnsServer.Listen(); err != nil {
		logger.Error("dns.listen.failed", "error", err.Error())
		os.Exit(1)
	}
	if err := adminServer.Listen(); err != nil {
		logger.Error("health.listen.failed", "error", err.Error())
		os.Exit(1)
	}
	if err := tcpProxy.Listen(); err != nil {
		logger.Error("proxy.listen.failed", "error", err.Error())
		os.Exit(1)
	}
}

// enforceEgress installs the DNAT rules that make the allowlist real.
//
// Exits the process when the rules cannot be installed, so a sandbox never
// joins a namespace that forwards traffic nothing is filtering.
func enforceEgress(logger *logger, cfg config.Config, account privdrop.Account) {
	if err := installEgress(cfg, account); err != nil {
		logger.Error("dnat.setup.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("dnat.setup.complete", "proxy_port", cfg.ProxyPort)
}

// installEgress runs the netfilter plan under the setup timeout.
//
// Separate from its caller so the timeout is released on the failure path
// too: os.Exit runs no deferred call.
//
// Returns:
//
//	The first rule failure, or nil when the whole plan installed.
func installEgress(cfg config.Config, account privdrop.Account) error {
	ctx, cancel := context.WithTimeout(context.Background(), dnatTimeout)
	defer cancel()

	return proxy.InstallRules(ctx, proxy.PlanRules(cfg.ProxyPort, cfg.DNSAllowed, account.UID))
}

// serveAll starts answering on every already-bound listener.
func serveAll(logger *logger, cfg config.Config, dnsServer *dns.Server, adminServer *health.Server, tcpProxy *proxy.Proxy) {
	if err := dnsServer.Serve(); err != nil {
		logger.Error("dns.serve.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("dns.started", "port", 53)

	if err := adminServer.Serve(); err != nil {
		logger.Error("health.serve.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("health.started", "port", cfg.HealthPort)

	if err := tcpProxy.Serve(); err != nil {
		logger.Error("proxy.serve.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("proxy.started", "port", cfg.ProxyPort)
}

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

	dnsServer, err := dns.NewServer(al, cfg.DNSAllowed, logger)
	if err != nil {
		logger.Error("dns.init.failed", "error", err.Error())
		os.Exit(1)
	}
	adminServer := health.NewServer(cfg.HealthPort, al, cfg.AdminToken, cfg.AllowedHosts, cfg.AllowAll, logger)
	tcpProxy := proxy.New(cfg.ProxyPort, al, logger)

	// Bind everything first, serve nothing yet. Port 53 and the proxy port
	// need the privilege this process is about to give up, while every one of
	// these listeners is reachable from the sandbox sharing this network
	// namespace, so none of them should be answering while the process can
	// still edit the rules confining it.
	bindAll(logger, dnsServer, adminServer, tcpProxy)
	enforceEgress(logger, cfg, account)

	if err := privdrop.Drop(account); err != nil {
		logger.Error("privdrop.failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("privdrop.complete", "uid", account.UID, "gid", account.GID)

	// Started only now: the rules installed above exempt account.UID alone, so
	// a resolver dial made while the process was still uid 0 would be
	// redirected to a relay that is not serving yet, or dropped outright when
	// dns_allowed is false, and the first resolution round would fail.
	al.Start()

	serveAll(logger, cfg, dnsServer, adminServer, tcpProxy)

	// Only now: the egress rules are installed and the privilege is gone, so
	// a caller that reads healthy and joins its sandbox to this namespace is
	// joining one that actually enforces.
	adminServer.MarkReady()

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
