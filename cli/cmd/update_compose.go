package cmd

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/images"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

// refreshCompose regenerates compose.yml from the current embedded template.
// If the regenerated compose differs from what is on disk, it shows the diff
// and asks the user to approve. When force is true (recovery mode), a missing
// compose.yml is generated from the template without prompting.
// Returns true if compose is up to date or changes were applied; false if
// the user declined.
func refreshCompose(cmd *cobra.Command, state config.State, force bool) (bool, error) {
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), GetGlobalOpts(cmd.Context()).UIOptions())

	safeDir, err := safeStateDir(state)
	if err != nil {
		return false, err
	}

	composePath := filepath.Join(safeDir, "compose.yml")
	existing, fresh, err := loadAndGenerate(composePath, state)
	if err != nil {
		return false, err
	}
	if existing == nil {
		if !force {
			return true, nil // no compose.yml on disk -- nothing to refresh
		}
		return recoverMissingCompose(out, composePath, state, safeDir)
	}

	if bytes.Equal(existing, fresh) {
		// Even when compose itself is unchanged, the NATS config file
		// it references may be missing or stale (older CLI versions
		// did not write it). Re-emit so the file always exists when
		// distributed bus mode is on.
		if err := compose.WriteNATSConfig(state.BusBackend, safeDir); err != nil {
			return false, err
		}
		out.Success("Compose configuration is up to date.")
		return true, nil
	}

	// Auto-apply when only the version comment and/or image references
	// changed -- these are expected during an update and don't need
	// user confirmation (template structure is unchanged).
	if isUpdateBoilerplateOnly(existing, fresh) {
		if err := compose.WriteComposeAndNATS("compose.yml", fresh, state.BusBackend, safeDir); err != nil {
			return false, fmt.Errorf("writing updated compose: %w", err)
		}
		out.Success("Compose configuration is up to date.")
		return true, nil
	}

	applied, err := applyComposeDiff(cmd, composePath, existing, fresh, safeDir, state, state.AutoApplyCompose)
	if err != nil {
		return false, err
	}
	return applied, nil
}

// recoverMissingCompose generates compose.yml from the template during
// recovery mode when the file is absent.
func recoverMissingCompose(out *ui.UI, _ string, state config.State, safeDir string) (bool, error) {
	params, err := compose.ParamsFromState(state)
	if err != nil {
		return false, fmt.Errorf("building compose params during recovery: %w", err)
	}
	// ParamsFromState already populates DigestPins from state.VerifiedDigests
	// when the deployment is on the default (trusted) registry and
	// leaves it nil for custom-registry trust transfers. Do not override.
	generated, genErr := compose.Generate(params)
	if genErr != nil {
		return false, fmt.Errorf("generating compose.yml during recovery: %w", genErr)
	}
	if wErr := compose.WriteComposeAndNATS("compose.yml", generated, state.BusBackend, safeDir); wErr != nil {
		return false, fmt.Errorf("writing compose files during recovery: %w", wErr)
	}
	out.Success("Generated compose.yml from template.")
	return true, nil
}

// isUpdateBoilerplateOnly returns true if the only differences between
// existing and fresh are the version comment (line 1) and/or image-line
// tag/digest bumps where the repository AND the suffix shape (presence
// of `:tag` and presence of `@digest`) are both unchanged. Pinned-image
// bumps for synthorg images and DHI base images (nats, postgres) are
// template-driven (Renovate digests embedded in the CLI), so the user
// has nothing to review. Any change in suffix shape -- pin removal, pin
// addition, OR a tag-only ref gaining a digest (and vice versa) -- is
// intentionally NOT auto-applied: each of those flips the deployment's
// trust posture and must reach the user prompt.
func isUpdateBoilerplateOnly(existing, fresh []byte) bool {
	if len(existing) == 0 && len(fresh) == 0 {
		return true
	}
	oldLines := strings.Split(string(existing), "\n")
	newLines := strings.Split(string(fresh), "\n")
	if len(oldLines) != len(newLines) {
		return false
	}
	for i := range oldLines {
		// Compose files on Windows can carry CRLF endings; strip \r so
		// the predicate behaves identically across platforms.
		oldLine := strings.TrimSuffix(oldLines[i], "\r")
		newLine := strings.TrimSuffix(newLines[i], "\r")
		if !isBoilerplateLineMatch(i, oldLine, newLine) {
			return false
		}
	}
	return true
}

// isBoilerplateLineMatch reports whether oldLine and newLine at index i
// represent the same compose content for boilerplate-detection purposes:
// byte-identical, the same generator banner, or compose `image:` lines
// whose repo / tag-presence / digest-presence triple match.
func isBoilerplateLineMatch(i int, oldLine, newLine string) bool {
	if oldLine == newLine {
		return true
	}
	if i == 0 && strings.HasPrefix(oldLine, "# Generated by SynthOrg CLI") &&
		strings.HasPrefix(newLine, "# Generated by SynthOrg CLI") {
		return true
	}
	oldRepo, oldHasTag, oldHasDigest, ok1 := extractImageRepo(oldLine)
	if !ok1 {
		return false
	}
	newRepo, newHasTag, newHasDigest, ok2 := extractImageRepo(newLine)
	if !ok2 {
		return false
	}
	return oldRepo == newRepo && oldHasTag == newHasTag && oldHasDigest == newHasDigest
}

// genericImageLinePattern matches a single-line compose `image:` declaration
// and captures the repository portion of the reference. The repository may
// include an optional `:port` segment in the host (e.g. `localhost:5000`)
// followed by `/`-separated path segments, but excludes the trailing
// `:tag` or `@digest`. Multi-line image specs (YAML arrays, flow mappings)
// intentionally fail to match so callers fall through to the "needs user
// review" path rather than auto-applying an unrecognised structural change.
var genericImageLinePattern = regexp.MustCompile(
	`^([ \t]*image:\s+)([^\s@:]+(?::\d+)?(?:/[^\s@:]+)*)([:@]\S+)?([ \t]*(?:#[^\r\n]*)?)$`,
)

// extractImageRepo parses a compose `image:` line and returns the repository
// portion (e.g. "ghcr.io/aureliolo/synthorg-backend" or "dhi.io/nats"),
// whether a `:tag` was present, whether an `@digest` was present, and
// whether the line is a recognisable image declaration at all. Returns
// ("", false, false, false) for non-image lines. Callers that auto-apply
// boilerplate must compare BOTH suffix flags, not just the repository,
// because every transition between (no suffix) / `:tag` / `@digest` /
// `:tag@digest` flips the deployment's trust posture.
func extractImageRepo(line string) (repo string, hasTag, hasDigest, ok bool) {
	sub := genericImageLinePattern.FindStringSubmatch(line)
	if len(sub) < 4 {
		return "", false, false, false
	}
	suffix := sub[3]
	return sub[2], strings.HasPrefix(suffix, ":"), strings.Contains(suffix, "@"), true
}

// loadAndGenerate reads the existing compose and generates a fresh one from
// the template. Returns (nil, nil, nil) if no compose.yml exists on disk.
func loadAndGenerate(composePath string, state config.State) ([]byte, []byte, error) {
	existing, err := os.ReadFile(composePath) //nolint:gosec // G304: composePath is <data-dir>/compose.yml under the SecurePath-cleaned data dir
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil, nil
		}
		return nil, nil, fmt.Errorf("reading existing compose: %w", err)
	}

	params, err := compose.ParamsFromState(state)
	if err != nil {
		return nil, nil, fmt.Errorf("building compose params: %w", err)
	}
	// ParamsFromState already populates DigestPins from state.VerifiedDigests
	// when the deployment is on the default (trusted) registry and
	// leaves it nil for custom-registry trust transfers. Do not override.
	fresh, err := compose.Generate(params)
	if err != nil {
		return nil, nil, fmt.Errorf("generating compose from template: %w", err)
	}
	return existing, fresh, nil
}

// applyComposeDiff shows the diff between existing and fresh compose,
// asks the user to approve, and writes the fresh compose (plus any
// required nats.conf side-file) atomically if approved.
// Returns true if applied, false if declined.
func applyComposeDiff(cmd *cobra.Command, _ string, existing, fresh []byte, safeDir string, state config.State, autoApply bool) (bool, error) {
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), GetGlobalOpts(cmd.Context()).UIOptions())

	out.Step("Compose template has changed:")
	// The diff is multi-line verbatim content; print it through the UI's
	// passthrough so it still respects quiet mode.
	if !out.IsQuiet() {
		out.Plain(lineDiff(string(existing), string(fresh)))
	}

	ok, err := confirmUpdate(cmd.Context(), "Apply compose configuration changes?", autoApply)
	if err != nil {
		return false, err
	}
	if !ok {
		out.Step("Compose changes skipped.")
		return false, nil
	}

	if err := compose.WriteComposeAndNATS("compose.yml", fresh, state.BusBackend, safeDir); err != nil {
		return false, fmt.Errorf("writing updated compose: %w", err)
	}
	out.Success("Compose configuration updated.")
	return true, nil
}

// secretKeyPattern matches YAML lines containing known sensitive keys.
// Used by lineDiff to redact sensitive values before displaying.
// Covers common secret naming conventions to prevent leaking credentials
// in terminal scrollback or CI logs when the compose template changes.
var secretKeyPattern = regexp.MustCompile(
	`(?i)^\s*\w*(SECRET|PASSWORD|TOKEN|API_KEY|CREDENTIALS|ENCRYPTION_KEY|SETTINGS_KEY|PRIVATE_KEY|CERT)\w*\s*:`,
)

// lineDiff produces a bag-based diff showing added (+) and removed (-) lines
// between two strings. Lines containing secret keys are redacted.
//
// Note: this uses multiset membership, not positional diffing. Reordered
// lines are not reported as changes. This is acceptable for compose files
// where the user approves structural additions/removals, not reorderings.
func lineDiff(oldText, updated string) string {
	oldLines := strings.Split(oldText, "\n")
	newLines := strings.Split(updated, "\n")

	newSet := make(map[string]int, len(newLines))
	for _, l := range newLines {
		newSet[l]++
	}

	oldSet := make(map[string]int, len(oldLines))
	for _, l := range oldLines {
		oldSet[l]++
	}

	var b strings.Builder
	for _, l := range oldLines {
		if newSet[l] > 0 {
			newSet[l]--
			continue
		}
		b.WriteString("  - ")
		b.WriteString(redactSecret(l))
		b.WriteByte('\n')
	}
	for _, l := range newLines {
		if oldSet[l] > 0 {
			oldSet[l]--
			continue
		}
		b.WriteString("  + ")
		b.WriteString(redactSecret(l))
		b.WriteByte('\n')
	}
	return b.String()
}

// redactSecret replaces secret values with [REDACTED] in diff output.
// Uses the regex submatch end position to find the colon reliably,
// rather than scanning from the start of the line.
func redactSecret(line string) string {
	loc := secretKeyPattern.FindStringIndex(line)
	if loc != nil {
		// loc[1] is past the trailing ":", so the key + colon is line[:loc[1]].
		return line[:loc[1]] + " [REDACTED]"
	}
	return line
}

// writeOrPatchCompose either regenerates compose from the template or
// patches only image references in the existing file.
func writeOrPatchCompose(state config.State, digestPins map[string]string, safeDir string, preserveCompose bool) error {
	if !preserveCompose {
		return writeDigestPinnedCompose(state, digestPins, safeDir)
	}
	return patchComposeImageRefs(state.ImageTag, digestPins, state.Sandbox, safeDir)
}

// imageLinePattern returns the regex that matches Docker image references
// for synthorg services in compose YAML. The repo prefix is built from the
// currently configured tunable (images.RepoPrefix()) rather than hardcoded
// to ghcr.io/aureliolo/synthorg- so custom-registry deployments continue to
// match the lines they generated. Rebuilding the regex per call keeps the
// matcher in sync when tunables are reconfigured between invocations.
//
// Handles both digest-pinned (repo@sha256:...) and tag-based (repo:tag).
// Anchors to the full compose line (with `(?m)^...$`) and captures the
// trailing whitespace/comment so the service name token must be followed
// by either a tag/digest separator or end-of-line. Without that anchor,
// `image: ghcr.io/aureliolo/synthorg-backend-fips:tag` would match at
// `synthorg-backend` and leave `-fips:tag` behind, corrupting the ref.
// RE2 (Go's regexp) does not support lookahead, so we use anchors + a
// captured trailer instead.
func imageLinePattern() *regexp.Regexp {
	return regexp.MustCompile(
		`(?m)^([ \t]*image:\s+)` +
			regexp.QuoteMeta(images.RepoPrefix()) +
			`(backend|web|sandbox)(?:[:@]\S+)?([ \t]*(?:#[^\r\n]*)?)$`,
	)
}

// patchComposeImageRefs updates only the image references in an existing
// compose.yml without regenerating from the template. This preserves the
// user's compose configuration while allowing image updates.
//
// Returns an error if no image references were found or if not all expected
// services (backend, web, and optionally sandbox) were patched -- this
// prevents config.Save from advancing state when compose is unpatched.
func patchComposeImageRefs(tag string, digestPins map[string]string, sandboxEnabled bool, safeDir string) error {
	composePath := filepath.Join(safeDir, "compose.yml")
	existing, err := os.ReadFile(composePath) //nolint:gosec // G304: composePath is <data-dir>/compose.yml under the SecurePath-cleaned data dir
	if err != nil {
		return fmt.Errorf("reading compose for image patching: %w", err)
	}

	replaced := make(map[string]bool)
	pattern := imageLinePattern()
	patched := pattern.ReplaceAllStringFunc(string(existing), func(match string) string {
		sub := pattern.FindStringSubmatch(match)
		if len(sub) < 4 {
			return match
		}
		prefix := sub[1]  // e.g. "    image: "
		name := sub[2]    // e.g. "backend"
		trailer := sub[3] // e.g. "" or "  # comment"
		repo := images.RepoPrefix() + name
		replaced[name] = true

		if d, ok := digestPins[name]; ok && d != "" {
			return prefix + repo + "@" + d + trailer
		}
		return prefix + repo + ":" + tag + trailer
	})

	if len(replaced) == 0 {
		return fmt.Errorf("no synthorg image references found in %s -- compose may be manually edited; run 'synthorg init' to regenerate", composePath)
	}

	// Backend and web are always required; sandbox only when enabled.
	required := []string{"backend", "web"}
	if sandboxEnabled {
		required = append(required, "sandbox")
	}
	for _, svc := range required {
		if !replaced[svc] {
			return fmt.Errorf("image reference for %q not found in %s -- compose may be manually edited; run 'synthorg init' to regenerate", svc, composePath)
		}
	}

	return compose.AtomicWriteFile(safeDir, "compose.yml", []byte(patched))
}
