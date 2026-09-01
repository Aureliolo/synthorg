package config

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"path"
	"strconv"
	"strings"
)

// EnvAPIPrefix is the backend's own api.api_prefix override.
//
// Cross-language: the name is derived by src/synthorg/settings/_value_rules.py
// (env_var_name) from the api namespace and the api_prefix key, and both
// compose files pass it through to the backend. The CLI reads the same
// variable rather than a knob of its own, because a second knob is a second
// answer to the same question and only one of them reaches the server.
const EnvAPIPrefix = "SYNTHORG_API_API_PREFIX"

// DefaultAPIPrefix mirrors the api.api_prefix setting's registered default.
const DefaultAPIPrefix = "/api/v1"

// apiHost is the only host the CLI ever talks to. It is a constant of this
// package rather than anything derived from configuration: the stack the CLI
// drives runs on the operator's own machine, and nothing an operator writes
// into EnvAPIPrefix may move a request off it.
const apiHost = "localhost"

// resolveAPIPrefix reduces the configured override to the path the backend
// serves its API under, or refuses a value that is not a path at all.
//
// Only the path survives: a value carrying a scheme, an authority, a query or
// a fragment is refused rather than trimmed down to its path, because a
// prefix that had to be reduced is a misconfiguration and the operator is the
// only one who can say what they meant by it. The same variable is handed to
// compose, so a value this cannot use is one the backend cannot serve either.
func resolveAPIPrefix() (string, error) {
	raw := strings.TrimSpace(os.Getenv(EnvAPIPrefix)) // lint-allow: env-read -- mirrors the backend's own override for the same setting
	if raw == "" {
		return DefaultAPIPrefix, nil
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return "", fmt.Errorf("%s is not a path: %w", EnvAPIPrefix, err)
	}
	switch {
	case parsed.Scheme != "", parsed.Host != "", parsed.User != nil:
		return "", fmt.Errorf(
			"%s must be a path, not a URL: %q", EnvAPIPrefix, raw)
	case parsed.RawQuery != "", parsed.Fragment != "":
		return "", fmt.Errorf(
			"%s must be a path with no query or fragment: %q", EnvAPIPrefix, raw)
	}
	cleaned := path.Clean("/" + parsed.Path)
	if cleaned == "/" {
		// The backend serving at the root is a prefix of nothing, and an
		// empty string is what concatenates correctly with a route.
		return "", nil
	}
	return cleaned, nil
}

// ValidateAPIPrefix refuses a configured prefix that is not a path.
//
// It is called once from the root command's pre-run, which is the only place
// that can still tell the operator what is wrong; every URL builder below it
// is a total function with nowhere to report to.
func ValidateAPIPrefix() error {
	_, err := resolveAPIPrefix()
	return err
}

// APIPrefix returns the path prefix the backend serves its API under.
//
// An operator who exports this before `synthorg start` moves every route, and
// a CLI that kept its own hardcoded copy would 404 on every call with nothing
// to say why. A value ValidateAPIPrefix would refuse has already stopped the
// command, so the default here is the safe answer for a caller reached
// without that pre-run rather than a fallback any real invocation takes.
func APIPrefix() string {
	prefix, err := resolveAPIPrefix()
	if err != nil {
		return DefaultAPIPrefix
	}
	return prefix
}

// APIURL builds a backend URL for route, which must start with "/".
//
// Every CLI-to-backend call goes through here so the prefix has one owner.
// The URL is assembled field by field rather than formatted, so the host is
// this package's own constant and the configured prefix reaches the path and
// nothing else.
func APIURL(port int, route string) string {
	u := url.URL{
		Scheme: "http",
		Host:   net.JoinHostPort(apiHost, strconv.Itoa(port)),
		Path:   APIPrefix() + route,
	}
	return u.String()
}
