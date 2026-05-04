package scaffold

import (
	"bytes"
	"embed"
	"fmt"
	"path"
	"strings"
	"text/template"
)

//go:embed templates/*.tmpl
var templatesFS embed.FS

// RenderedFile is a single output file produced by a scaffold target.
// Path is repository-relative (forward-slash separated). Contents is
// the rendered file body, ready to be written verbatim.
type RenderedFile struct {
	Path     string
	Contents []byte
}

// Kind names a scaffold target. Stable strings; the Cobra subcommands
// in cli/cmd/new.go pass these through directly.
type Kind string

const (
	KindService     Kind = "service"
	KindPersistence Kind = "persistence"
	KindTool        Kind = "tool"
	KindController  Kind = "controller"
)

// Render dispatches to the per-Kind layout. Returns the full set of
// files the scaffold emits, plus the inline wiring snippet the user
// must apply manually (a markdown body bundled into a WIRING.md file
// alongside the new code).
//
// Auto-wiring of EXCEPTION_HANDLERS / PersistenceBackend / auto_wire.py
// is intentionally out of scope for v0: those files use Python AST
// patterns (MappingProxyType literals, factory dispatch tables) that
// would require brittle string-search-replace from Go. The scaffolder
// emits the wiring snippets as markdown so the user applies them with
// a known-good visual diff and the next iteration can replace this
// step with an AST-aware tool when the project gains one.
func Render(kind Kind, p Params) ([]RenderedFile, error) {
	switch kind {
	case KindService:
		return renderService(p)
	case KindPersistence:
		return renderPersistence(p)
	case KindTool:
		return renderTool(p)
	case KindController:
		return renderController(p)
	default:
		return nil, fmt.Errorf("unknown scaffold kind %q", kind)
	}
}

// renderTemplate parses and executes a single embedded template by
// filename (without the templates/ prefix). Returns the rendered bytes
// or a wrapped parse / execute error.
func renderTemplate(name string, p Params) ([]byte, error) {
	body, err := templatesFS.ReadFile(path.Join("templates", name))
	if err != nil {
		return nil, fmt.Errorf("reading template %q: %w", name, err)
	}
	tmpl, err := template.New(name).Funcs(funcMap).Parse(string(body))
	if err != nil {
		return nil, fmt.Errorf("parsing template %q: %w", name, err)
	}
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, p); err != nil {
		return nil, fmt.Errorf("executing template %q: %w", name, err)
	}
	return buf.Bytes(), nil
}

// funcMap is the shared template helper set. Keep it tiny: helpers are
// hard to discover from the template body, so prefer pre-computing
// values into Params over adding a helper.
//
// Helpers accept `any` so they tolerate string-alias types (Params.Domain
// is scaffold.Domain, a `type Domain string`); Go's text/template is
// strict about types and would refuse to call a `func(string)` with a
// Domain value otherwise. fmt.Sprint formats any string-kind value
// without a reflect-allocation hot loop.
var funcMap = template.FuncMap{
	// title returns its argument with the first rune in upper case.
	// Used in templates where Domain (snake_case) feeds into a prose
	// sentence like "{{title .Domain}} is initialised". For type names,
	// prefer Params.ClassName which is already PascalCase.
	"title": func(v any) string {
		s := fmt.Sprint(v)
		if s == "" {
			return s
		}
		return strings.ToUpper(s[:1]) + s[1:]
	},
	// shout returns its argument upper-cased, the form event constants
	// take in synthorg.observability.events (e.g. WORKERS_WORKER_STARTED
	// for the workers domain).
	"shout": func(v any) string { return strings.ToUpper(fmt.Sprint(v)) },
}
