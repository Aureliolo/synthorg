// Package scaffold renders SynthOrg-conforming Python file sets from
// embedded templates. Each scaffold target (service / persistence / tool
// / controller) writes a small group of new files under
// src/synthorg/<domain>/ (and tests under tests/) so a fresh feature
// starts from a layout that already passes every project gate.
//
// The package is consumed by the `synthorg new` Cobra command in
// cli/cmd/new.go; the patterns mirror cli/internal/compose (Go
// text/template + embed.FS, validateParams gate before any string
// interpolation).
package scaffold

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"
)

// Domain is the snake_case identifier that names the new feature, e.g.
// "ping" or "agent_health". It becomes the directory name under
// src/synthorg/, the module slug in test paths, and the prefix on
// generated repository / service / handler symbols.
type Domain string

// Params are the inputs every scaffold template receives. The shared
// fields are computed from a single user-supplied Domain: ClassName is
// the PascalCase form ("agent_health" -> "AgentHealth") used for class
// names; ModuleDocSlug is a human-readable slug ("agent health") used in
// module docstrings.
//
// SettingsNamespace is a stable enum value the user may have defined in
// src/synthorg/settings/enums.py SettingNamespace; for the v0 templates
// we do NOT register settings (registering an unconsumed setting would
// trip scripts/check_setting_to_startup_trace.py the moment the file
// lands). The field is reserved for future template variants.
type Params struct {
	Domain            Domain
	ClassName         string
	ModuleDocSlug     string
	SettingsNamespace string
}

// NewParams validates the user-supplied domain and derives the rest of
// the template inputs. Validation is strict on purpose: the domain name
// flows into class names, file paths, import statements, and SQL table
// names, so any character outside [a-z0-9_] would produce code that
// Python or SQLite would reject downstream.
func NewParams(rawDomain string) (Params, error) {
	domain, err := validateDomain(rawDomain)
	if err != nil {
		return Params{}, err
	}
	return Params{
		Domain:        domain,
		ClassName:     pascalCase(string(domain)),
		ModuleDocSlug: strings.ReplaceAll(string(domain), "_", " "),
	}, nil
}

// domainPattern matches a snake_case Python identifier that does not
// start or end with an underscore and contains at least one letter.
// Anchored so partial matches are rejected.
var domainPattern = regexp.MustCompile(`^[a-z][a-z0-9]*(_[a-z0-9]+)*$`)

// reservedDomains lists names that already exist as top-level packages
// under src/synthorg/ (or are Python reserved words, or would shadow
// stdlib modules). Generating into one of these would silently corrupt
// the existing package; reject up front instead.
var reservedDomains = map[string]struct{}{
	"api":           {},
	"backup":        {},
	"budget":        {},
	"cli":           {},
	"communication": {},
	"core":          {},
	"engine":        {},
	"hr":            {},
	"integrations":  {},
	"memory":        {},
	"meta":          {},
	"notifications": {},
	"observability": {},
	"ontology":      {},
	"persistence":   {},
	"providers":     {},
	"security":      {},
	"settings":      {},
	"telemetry":     {},
	"tools":         {},
	"utils":         {},
	"workers":       {},
	// Python keywords / common stdlib names that would create import
	// shadows even though they are not yet present in src/synthorg/.
	"async":  {},
	"await":  {},
	"class":  {},
	"def":    {},
	"import": {},
	"return": {},
	"types":  {},
	"typing": {},
}

// validateDomain enforces snake_case and rejects reserved names.
func validateDomain(raw string) (Domain, error) {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return "", fmt.Errorf("domain is required")
	}
	if !domainPattern.MatchString(trimmed) {
		return "", fmt.Errorf(
			"invalid domain %q: must be snake_case ([a-z][a-z0-9_]*), no leading/trailing underscore",
			trimmed,
		)
	}
	if _, reserved := reservedDomains[trimmed]; reserved {
		return "", fmt.Errorf(
			"domain %q is reserved (existing package or Python keyword)",
			trimmed,
		)
	}
	return Domain(trimmed), nil
}

// pascalCase converts a snake_case identifier to PascalCase.
//
// Example: "agent_health" -> "AgentHealth", "ping" -> "Ping".
func pascalCase(snake string) string {
	if snake == "" {
		return ""
	}
	var b strings.Builder
	upper := true
	for _, r := range snake {
		if r == '_' {
			upper = true
			continue
		}
		if upper {
			b.WriteRune(unicode.ToUpper(r))
			upper = false
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}
