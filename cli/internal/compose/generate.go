// Package compose generates Docker Compose YAML from an embedded template.
package compose

import (
	"bytes"
	_ "embed"
	"fmt"
	"regexp"
	"strings"
	"text/template"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

//go:embed compose.yml.tmpl
var composeTmpl string

// imageTagPattern validates image tags to prevent YAML injection.
var imageTagPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`)

// allowedLogLevels restricts log level values to a known safe set.
var allowedLogLevels = map[string]bool{
	"debug": true,
	"info":  true,
	"warn":  true,
	"error": true,
}

// Params are the template parameters for compose generation.
type Params struct {
	CLIVersion  string
	ImageTag    string
	BackendPort int
	WebPort     int
	LogLevel    string
	JWTSecret   string
	Sandbox     bool
	DockerSock  string
}

// ParamsFromState creates Params from a persisted State.
func ParamsFromState(s config.State) Params {
	return Params{
		CLIVersion:  version.Version,
		ImageTag:    s.ImageTag,
		BackendPort: s.BackendPort,
		WebPort:     s.WebPort,
		LogLevel:    s.LogLevel,
		JWTSecret:   s.JWTSecret,
		Sandbox:     s.Sandbox,
		DockerSock:  s.DockerSock,
	}
}

// Generate renders the compose template with the given parameters.
// It validates all string parameters before rendering to prevent YAML injection.
func Generate(p Params) ([]byte, error) {
	if err := validateParams(p); err != nil {
		return nil, err
	}

	funcMap := template.FuncMap{
		"yamlStr": yamlStr,
	}

	tmpl, err := template.New("compose").Funcs(funcMap).Parse(composeTmpl)
	if err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, p); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// validateParams checks all template parameters for safe values.
func validateParams(p Params) error {
	if !imageTagPattern.MatchString(p.ImageTag) {
		return fmt.Errorf("invalid image tag %q: must match %s", p.ImageTag, imageTagPattern.String())
	}
	if p.LogLevel != "" && !allowedLogLevels[p.LogLevel] {
		return fmt.Errorf("invalid log level %q: must be one of debug, info, warn, error", p.LogLevel)
	}
	if p.BackendPort < 1 || p.BackendPort > 65535 {
		return fmt.Errorf("invalid backend port %d: must be 1-65535", p.BackendPort)
	}
	if p.WebPort < 1 || p.WebPort > 65535 {
		return fmt.Errorf("invalid web port %d: must be 1-65535", p.WebPort)
	}
	if p.BackendPort == p.WebPort {
		return fmt.Errorf("backend and web ports must be different (both set to %d)", p.BackendPort)
	}
	if p.Sandbox {
		if p.DockerSock == "" {
			return fmt.Errorf("docker socket path must be set when sandbox is enabled")
		}
		if strings.ContainsAny(p.DockerSock, "\"'`$\n\r{}[]") {
			return fmt.Errorf("docker socket path %q contains unsafe characters", p.DockerSock)
		}
	}
	return nil
}

// yamlStr safely quotes a string value for YAML, escaping special characters.
// Also escapes $ to prevent Docker Compose variable interpolation.
func yamlStr(s string) string {
	// If the string contains YAML-special or Compose-interpolation characters,
	// double-quote and escape.
	if strings.ContainsAny(s, "$:#{}[]|>&*!%@`\"'\\\n\r\t") {
		escaped := strings.ReplaceAll(s, `\`, `\\`)
		escaped = strings.ReplaceAll(escaped, `"`, `\"`)
		escaped = strings.ReplaceAll(escaped, "\n", `\n`)
		escaped = strings.ReplaceAll(escaped, "\r", `\r`)
		escaped = strings.ReplaceAll(escaped, "\t", `\t`)
		// Escape $ to prevent Docker Compose variable interpolation.
		escaped = strings.ReplaceAll(escaped, "$", "$$")
		return `"` + escaped + `"`
	}
	return `"` + s + `"`
}
