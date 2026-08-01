package cmd

import (
	"context"
	"errors"
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
	// The container `synthorg init` names the backend service, and the
	// only one `worker start` can exec into without being told otherwise.
	defaultWorkerContainer = "synthorg-backend"
)

var (
	workerStartCount        int
	workerStartNATSURL      string
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
	workerStartCmd.Flags().StringVar(&workerStartNATSURL, "nats-url", config.DefaultNATSURLValue,
		"NATS server URL reachable from inside the backend container")
	workerStartCmd.Flags().StringVar(&workerStartStreamPrefix, "stream-prefix", config.DefaultNATSStreamPrefixValue,
		"JetStream stream name prefix")
	workerStartCmd.Flags().StringVar(&workerStartContainer, "container", "",
		"backend container name (default: synthorg-backend)")
	workerCmd.AddCommand(workerStartCmd)
}

// workerStartPlan is the fully resolved, fully validated shape of one
// invocation: everything precedence and validation decide, and nothing
// the docker exec still has to work out for itself.
type workerStartPlan struct {
	natsURL      string
	streamPrefix string
	container    string
	workers      int
}

// resolveWorkerStartFlags applies the documented precedence
// (explicit flag > env > compiled-in default) to every flag that has one.
//
// Reused-process safety: pflag's StringVar binds to a package global. A
// previous explicit `--nats-url=foo` invocation leaves
// `workerStartNATSURL == "foo"` after the command returns, so a later
// invocation that omits the flag would inherit `"foo"` instead of falling
// back to the compiled default. The omitted-flag branch therefore starts
// from `config.DefaultNATSURLValue` rather than from the bound global.
func resolveWorkerStartFlags(cmd *cobra.Command, opts *GlobalOpts) workerStartPlan {
	natsURL := config.DefaultNATSURLValue
	if cmd.Flags().Changed("nats-url") {
		natsURL = workerStartNATSURL
	} else if envURL := strings.TrimSpace(os.Getenv(config.EnvNATSURL)); envURL != "" {
		natsURL = envURL
	}
	streamPrefix := workerStartStreamPrefix
	if !cmd.Flags().Changed("stream-prefix") {
		streamPrefix = opts.Tunables.DefaultNATSStreamPrefix
	}
	// Same reused-process hazard as --nats-url above: an earlier explicit
	// --container leaves the bound global set, so a later invocation that
	// omits the flag would silently exec into the previous container.
	container := defaultWorkerContainer
	if cmd.Flags().Changed("container") && workerStartContainer != "" {
		container = workerStartContainer
	}
	return workerStartPlan{
		natsURL:      natsURL,
		streamPrefix: streamPrefix,
		container:    container,
		workers:      workerStartCount,
	}
}

// validateWorkerStartPlan rejects anything that would otherwise become an
// env var on the backend process or an argument to docker.
func validateWorkerStartPlan(plan workerStartPlan) error {
	if plan.workers <= 0 {
		return fmt.Errorf("--workers must be > 0, got %d", plan.workers)
	}
	if err := validateNATSURL(plan.natsURL); err != nil {
		return err
	}
	// The tunable path already runs the same regex via
	// config.ResolveTunables, but an explicit --stream-prefix skips that
	// check and would otherwise reach the backend unvalidated.
	if !config.IsValidStreamPrefix(plan.streamPrefix) {
		return fmt.Errorf(
			"invalid --stream-prefix %q: must match [A-Z0-9][A-Z0-9_-]*",
			plan.streamPrefix,
		)
	}
	// The resolved value, not the bound global: the global is empty on an
	// omitted flag and stale on a reused process, so validating it lets the
	// value that actually reaches docker through unchecked.
	if plan.container == "" {
		return errors.New("resolved container name is empty")
	}
	return validateContainerName(plan.container)
}

// workerExecArgs builds the docker exec argv for a validated plan.
//
// The NATS URL and stream prefix travel in the docker process environment
// rather than in argv: `-e KEY=value` pairs or an explicit
// `--nats-url <value>` would expose `nats://user:pass@host` to anyone
// reading the docker process list, even though the log output is redacted.
// `docker exec -e KEY <container>` forwards the variable from the parent
// environment without putting its value on the command line. The `--` stops
// docker's own flag parsing so the container name can only ever be read as
// the CONTAINER positional.
func workerExecArgs(plan workerStartPlan) []string {
	return []string{
		"exec",
		"-e", config.EnvNATSURL,
		"-e", config.EnvNATSStreamPrefix,
		"-e", config.EnvWorkers,
		// Forwarded so the documented precedence (flag > env > registered
		// default) holds inside the container; without it the operator's
		// value never crosses the exec boundary and the default silently wins.
		"-e", config.EnvWorkerHTTPTimeoutSeconds,
		"--",
		plan.container,
		"python", "-m", "synthorg.workers",
		"--workers", strconv.Itoa(plan.workers),
	}
}

func runWorkerStart(cmd *cobra.Command, _ []string) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	plan := resolveWorkerStartFlags(cmd, opts)
	if err := validateWorkerStartPlan(plan); err != nil {
		return err
	}

	env := append(os.Environ(),
		config.EnvNATSURL+"="+plan.natsURL,
		config.EnvNATSStreamPrefix+"="+plan.streamPrefix,
		config.EnvWorkers+"="+strconv.Itoa(plan.workers),
	)

	out.KeyValue("Workers", strconv.Itoa(plan.workers))
	out.KeyValue("NATS URL", redactNATSURL(plan.natsURL))
	out.KeyValue("Stream prefix", plan.streamPrefix)
	out.KeyValue("Container", plan.container)
	out.HintNextStep("Press Ctrl+C to stop workers.")

	return execDocker(cmd.Context(), workerExecArgs(plan), env)
}

// validateNATSURL rejects obviously malformed URLs before we pass them
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
func validateNATSURL(raw string) error {
	if raw == "" {
		return fmt.Errorf("--nats-url must not be empty")
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("invalid --nats-url %q: %w", redactNATSURL(raw), err)
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
		return fmt.Errorf("invalid --nats-url %q: missing host", redactNATSURL(raw))
	}
	if rawPort := parsed.Port(); rawPort != "" {
		port, err := strconv.Atoi(rawPort)
		if err != nil {
			return fmt.Errorf(
				"invalid --nats-url %q: non-numeric port %q",
				redactNATSURL(raw), rawPort,
			)
		}
		if port < 1 || port > 65535 {
			return fmt.Errorf(
				"invalid --nats-url %q: port %d out of range (must be 1-65535)",
				redactNATSURL(raw), port,
			)
		}
	}
	return nil
}

// validateContainerName rejects container names that would fail
// docker's own parsing before we shell out. Docker's grammar is
// [a-zA-Z0-9][a-zA-Z0-9_.-]*: the leading character must be
// alphanumeric. Enforcing that is what stops a value like
// `--user=root` from reaching docker's own flag parser as an option
// rather than as the CONTAINER positional.
func validateContainerName(name string) error {
	if name == "" {
		// Empty means "use default" -- validated later.
		return nil
	}
	if !isContainerNameAlnum(rune(name[0])) {
		return fmt.Errorf(
			"invalid --container %q: must start with a letter or digit",
			name,
		)
	}
	for _, r := range name {
		if !isContainerNameRune(r) {
			return fmt.Errorf(
				"invalid --container %q: must match [a-zA-Z0-9][a-zA-Z0-9_.-]*",
				name,
			)
		}
	}
	return nil
}

// isContainerNameAlnum reports whether r is ASCII alphanumeric, the
// only thing Docker accepts as the first character of a name.
func isContainerNameAlnum(r rune) bool {
	return (r >= 'a' && r <= 'z') ||
		(r >= 'A' && r <= 'Z') ||
		(r >= '0' && r <= '9')
}

// isContainerNameRune reports whether r is a character Docker accepts
// in a container name: ASCII alphanumeric plus underscore, hyphen, dot.
func isContainerNameRune(r rune) bool {
	return isContainerNameAlnum(r) || r == '_' || r == '-' || r == '.'
}

// redactNATSURL strips credentials from a NATS URL so the caller can
// log it safely. nats://user:pass@host:port becomes nats://***@host:port.
// Non-URL strings pass through so the user still sees something useful
// in error messages.
func redactNATSURL(raw string) string {
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
	dockerCmd := exec.CommandContext(ctx, "docker", args...) //nolint:gosec // G204: "docker" is a constant; args are built from validated flags (container name, stream prefix, NATS URL, worker count)
	dockerCmd.Stdout = os.Stdout
	dockerCmd.Stderr = os.Stderr
	dockerCmd.Stdin = os.Stdin
	dockerCmd.Env = env
	return dockerCmd.Run()
}
