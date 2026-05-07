package cmd

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"strconv"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

const (
	defaultWorkerCount = 4
)

var (
	workerStartCount        int
	workerStartNatsURL      string
	workerStartStreamPrefix string
	workerStartContainer    string
)

var workerStartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start a pool of distributed task queue workers",
	Long: `Spawns a worker pool inside the backend container via ` + "`docker exec`" + `.

Workers connect to NATS JetStream, pull task claims from the work
queue, execute the task via the agent runtime, and transition the
task back through the backend HTTP API.

Requires the distributed runtime profile to be running
(` + "`docker compose --profile distributed up`" + `). The default NATS URL targets
the in-network DNS name ` + "`nats`" + `; override with --nats-url for external setups.`,
	Example: `  synthorg worker start                               # 4 workers, default NATS URL
  synthorg worker start --workers 8                   # 8 workers
  synthorg worker start --nats-url nats://nats:4222   # explicit NATS URL
  synthorg worker start --container synthorg-backend  # explicit container name`,
	RunE: runWorkerStart,
}

func init() {
	// Flag defaults for --nats-url and --stream-prefix are the compiled-in
	// baseline so `--help` is stable. The resolved env/config defaults are
	// substituted inside runWorkerStart when the user omits the flag.
	workerStartCmd.Flags().IntVar(&workerStartCount, "workers", defaultWorkerCount,
		"number of concurrent workers in the pool (default 4)")
	workerStartCmd.Flags().StringVar(&workerStartNatsURL, "nats-url", config.DefaultNATSURLValue,
		"NATS server URL reachable from inside the backend container")
	workerStartCmd.Flags().StringVar(&workerStartStreamPrefix, "stream-prefix", config.DefaultNATSStreamPrefixValue,
		"JetStream stream name prefix")
	workerStartCmd.Flags().StringVar(&workerStartContainer, "container", "",
		"backend container name (default: synthorg-backend)")
	workerCmd.AddCommand(workerStartCmd)
}

func runWorkerStart(cmd *cobra.Command, _ []string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// Precedence for --nats-url: explicit flag > SYNTHORG_NATS_URL env >
	// compiled-in fallback. Single source of truth shared with the
	// backend's ``communication.nats_url`` setting; no parallel
	// CLI-only tunable layer.
	if !cmd.Flags().Changed("nats-url") {
		if envURL := strings.TrimSpace(os.Getenv("SYNTHORG_NATS_URL")); envURL != "" {
			workerStartNatsURL = envURL
		}
	}
	if !cmd.Flags().Changed("stream-prefix") {
		workerStartStreamPrefix = opts.Tunables.DefaultNATSStreamPrefix
	}

	if workerStartCount <= 0 {
		return fmt.Errorf("--workers must be > 0, got %d", workerStartCount)
	}
	if err := validateNatsURL(workerStartNatsURL); err != nil {
		return err
	}
	// Validate the stream prefix up front so an explicit --stream-prefix
	// with bad characters is rejected here rather than deep inside the
	// worker pool. The tunable path already runs through the same regex
	// via config.ResolveTunables, but an explicit flag skips that check
	// and would otherwise end up as an env var on the backend process.
	if !config.IsValidStreamPrefix(workerStartStreamPrefix) {
		return fmt.Errorf(
			"invalid --stream-prefix %q: must match [A-Z0-9][A-Z0-9_-]*",
			workerStartStreamPrefix,
		)
	}
	if err := validateContainerName(workerStartContainer); err != nil {
		return err
	}

	container := workerStartContainer
	if container == "" {
		container = "synthorg-backend"
	}

	// Pass the NATS URL via env only, and pass env values via the
	// docker process environment rather than argv. Putting
	// `--nats-url <value>` into the CLI argv or `-e KEY=value` pairs
	// into the docker exec argv would expose `nats://user:pass@host`
	// (and the stream prefix + worker count) to anyone reading the
	// docker process list even though the log output is redacted.
	// `docker exec -e KEY <container>` tells docker to forward the
	// variable from the parent process environment without putting
	// its value on the command line.
	args := []string{
		"exec",
		"-e", "SYNTHORG_NATS_URL",
		"-e", "SYNTHORG_NATS_STREAM_PREFIX",
		"-e", "SYNTHORG_WORKER_COUNT",
		container,
		"python", "-m", "synthorg.workers",
		"--workers", strconv.Itoa(workerStartCount),
	}
	env := append(os.Environ(),
		"SYNTHORG_NATS_URL="+workerStartNatsURL,
		"SYNTHORG_NATS_STREAM_PREFIX="+workerStartStreamPrefix,
		"SYNTHORG_WORKER_COUNT="+strconv.Itoa(workerStartCount),
	)

	out.KeyValue("Workers", strconv.Itoa(workerStartCount))
	out.KeyValue("NATS URL", redactNatsURL(workerStartNatsURL))
	out.KeyValue("Stream prefix", workerStartStreamPrefix)
	out.KeyValue("Container", container)
	out.HintNextStep("Press Ctrl+C to stop workers.")

	return execDocker(cmd.Context(), args, env)
}

// validateNatsURL rejects obviously malformed URLs before we pass them
// to docker exec. nats-py does its own validation at connection time,
// but catching a typo up front gives a better error message and
// avoids wasted container startup.
//
// Hostname presence is checked with url.URL.Hostname() rather than the
// raw Host field so a value like `nats://:4222` (port only, no host)
// is rejected instead of being silently accepted because Host contains
// a non-empty string. If a port is present we also validate that it
// parses as an integer inside the legal TCP range, which url.Parse
// does not enforce by itself.
func validateNatsURL(raw string) error {
	if raw == "" {
		return fmt.Errorf("--nats-url must not be empty")
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("invalid --nats-url %q: %w", redactNatsURL(raw), err)
	}
	switch parsed.Scheme {
	case "nats", "tls", "nats+tls":
		// ok
	default:
		return fmt.Errorf(
			"invalid --nats-url scheme %q: must be nats://, tls://, or nats+tls://",
			parsed.Scheme,
		)
	}
	if parsed.Hostname() == "" {
		return fmt.Errorf("invalid --nats-url %q: missing host", redactNatsURL(raw))
	}
	if rawPort := parsed.Port(); rawPort != "" {
		port, err := strconv.Atoi(rawPort)
		if err != nil {
			return fmt.Errorf(
				"invalid --nats-url %q: non-numeric port %q",
				redactNatsURL(raw), rawPort,
			)
		}
		if port < 1 || port > 65535 {
			return fmt.Errorf(
				"invalid --nats-url %q: port %d out of range (must be 1-65535)",
				redactNatsURL(raw), port,
			)
		}
	}
	return nil
}

// validateContainerName rejects container names that would fail
// docker's own parsing before we shell out. Docker allows
// alphanumerics, underscores, hyphens, and periods; anything else is
// a user error.
func validateContainerName(name string) error {
	if name == "" {
		// Empty means "use default" -- validated later.
		return nil
	}
	for _, r := range name {
		ok := (r >= 'a' && r <= 'z') ||
			(r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') ||
			r == '_' || r == '-' || r == '.'
		if !ok {
			return fmt.Errorf(
				"invalid --container %q: must match [a-zA-Z0-9_.-]",
				name,
			)
		}
	}
	return nil
}

// redactNatsURL strips credentials from a NATS URL so the caller can
// log it safely. nats://user:pass@host:port becomes nats://***@host:port.
// Non-URL strings pass through so the user still sees something useful
// in error messages.
func redactNatsURL(raw string) string {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Host == "" {
		return raw
	}
	if parsed.User == nil {
		return raw
	}
	scheme := parsed.Scheme
	if scheme == "" {
		scheme = "nats"
	}
	rest := parsed.Path
	if parsed.RawQuery != "" {
		rest += "?" + parsed.RawQuery
	}
	return strings.TrimRight(fmt.Sprintf("%s://***@%s%s", scheme, parsed.Host, rest), "/")
}

// execDocker runs `docker <args...>` and streams output to the parent
// process. Factored out so worker_start_test.go can override it in
// unit tests. The env argument is the complete environment the child
// docker process should run with (typically `os.Environ()` merged with
// the secrets `docker exec -e NAME` should forward into the target
// container); passing env values via the process environment instead
// of the argv prevents them from leaking through `docker ps` / the
// host process list.
var execDocker = func(ctx context.Context, args []string, env []string) error {
	//nolint:gosec // args are constructed from validated flags above.
	dockerCmd := exec.CommandContext(ctx, "docker", args...)
	dockerCmd.Stdout = os.Stdout
	dockerCmd.Stderr = os.Stderr
	dockerCmd.Stdin = os.Stdin
	dockerCmd.Env = env
	return dockerCmd.Run()
}
