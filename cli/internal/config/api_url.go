package config

import (
	"fmt"
	"os"
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

// APIPrefix returns the path prefix the backend serves its API under.
//
// An operator who exports this before `synthorg start` moves every route, and
// a CLI that kept its own hardcoded copy would 404 on every call with nothing
// to say why.
func APIPrefix() string {
	prefix := strings.TrimSpace(os.Getenv(EnvAPIPrefix)) // lint-allow: env-read -- mirrors the backend's own override for the same setting
	if prefix == "" {
		return DefaultAPIPrefix
	}
	if !strings.HasPrefix(prefix, "/") {
		prefix = "/" + prefix
	}
	return strings.TrimSuffix(prefix, "/")
}

// APIURL builds a backend URL for path, which must start with "/".
//
// Every CLI-to-backend call goes through here so the prefix has one owner.
func APIURL(port int, path string) string {
	return fmt.Sprintf("http://localhost:%d%s%s", port, APIPrefix(), path)
}
