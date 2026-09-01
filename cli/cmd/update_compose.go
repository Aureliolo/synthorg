package cmd

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/images"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
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
		out.Plain(lineDiff(string(existing), string(fresh), secretValues(state)))
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

// secretKeyPattern matches YAML lines whose KEY names a secret. It is the
// weaker of the two redaction layers and cannot be the only one: it has to
// anticipate every name the template might ever render, and two of the names
// it renders today carry a secret while matching none of these alternatives
// (SYNTHORG_MASTER_KEY, and SYNTHORG_DATABASE_URL, whose DSN embeds the
// Postgres password). It is kept because it covers a key whose value this
// process does not hold, and because it redacts to the tidier "KEY: [REDACTED]"
// form rather than punching a hole in the middle of a line.
var secretKeyPattern = regexp.MustCompile(
	`(?i)^\s*\w*(SECRET|PASSWORD|TOKEN|API_KEY|CREDENTIALS|ENCRYPTION_KEY|SETTINGS_KEY|PRIVATE_KEY|CERT)\w*\s*:`,
)

// redactedMarker replaces a secret wherever one is found.
const redactedMarker = "[REDACTED]"

// minRedactableSecretLen is the shortest value worth replacing by value.
// Redaction by value is what covers a key the name pattern cannot anticipate,
// but a one- or two-character value occurs all over an ordinary compose file,
// so replacing every occurrence would corrupt the diff rather than protect
// anything. A short value under a key the pattern does know is still covered
// by the first layer.
const minRedactableSecretLen = 8

// secretValues returns the secrets this deployment actually holds, longest
// first so that a value containing another is replaced whole instead of
// leaving the shorter one's tail behind.
func secretValues(state config.State) []string {
	values := make([]string, 0, 5)
	for _, candidate := range []string{
		state.MasterKey,
		state.PostgresPassword,
		state.JWTSecret,
		state.SettingsKey,
		state.CursorSecret,
	} {
		if len(candidate) >= minRedactableSecretLen {
			values = append(values, candidate)
		}
	}
	slices.SortFunc(values, func(a, b string) int { return len(b) - len(a) })
	return values
}

// lineDiff produces a bag-based diff showing added (+) and removed (-) lines
// between two strings. Secrets are redacted, both by key name and by the
// values in `secrets` (see redactSecret).
//
// Note: this uses multiset membership, not positional diffing. Reordered
// lines are not reported as changes. This is acceptable for compose files
// where the user approves structural additions/removals, not reorderings.
func lineDiff(oldText, updated string, secrets []string) string {
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
		b.WriteString(redactSecret(l, secrets))
		b.WriteByte('\n')
	}
	for _, l := range newLines {
		if oldSet[l] > 0 {
			oldSet[l]--
			continue
		}
		b.WriteString("  + ")
		b.WriteString(redactSecret(l, secrets))
		b.WriteByte('\n')
	}
	return b.String()
}

// redactSecret replaces secret values with [REDACTED] in diff output, in two
// layers. The key-name layer runs first and wins the whole line, because it
// produces the tidier form and covers a key whose value this process does not
// hold. The value layer then catches what the names cannot: a secret reaches
// the diff under whatever key the template happens to render it under, and
// matching the value is the only form of that check which cannot be outrun by
// a key added later.
func redactSecret(line string, secrets []string) string {
	loc := secretKeyPattern.FindStringIndex(line)
	if loc != nil {
		// loc[1] is past the trailing ":", so the key + colon is line[:loc[1]].
		return line[:loc[1]] + " " + redactedMarker
	}
	for _, secret := range secrets {
		line = strings.ReplaceAll(line, secret, redactedMarker)
	}
	return line
}

// writeOrPatchCompose either regenerates compose from the template or
// patches only image references in the existing file.
func writeOrPatchCompose(state config.State, digestPins map[string]string, safeDir string, preserveCompose bool) error {
	if !preserveCompose {
		return writeDigestPinnedCompose(state, digestPins, safeDir)
	}
	return patchComposeImageRefs(state, digestPins, safeDir)
}

// imageLinePattern returns the regex that matches Docker image references
// for synthorg services in compose YAML. The repo prefix is built from the
// currently configured tunable (images.RepoPrefix()) rather than hardcoded
// to ghcr.io/aureliolo/synthorg- so custom-registry deployments continue to
// match the lines they generated. Rebuilding the regex per call keeps the
// matcher in sync when tunables are reconfigured between invocations.
//
// Only backend and web are ever declared as their own compose service with
// an `image:` line (compose.yml.tmpl): sandbox, sidecar, and fine-tune are
// images the backend pulls and runs itself via aiodocker, so their
// references travel to it as the SYNTHORG_{SANDBOX,SIDECAR,FINE_TUNE}_IMAGE
// environment values on the backend service instead -- see
// standaloneImageEnvPattern for those.
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
			`(backend|web)(?:[:@]\S+)?([ \t]*(?:#[^\r\n]*)?)$`,
	)
}

// standaloneImageEnvPattern matches the SYNTHORG_SANDBOX_IMAGE /
// SYNTHORG_SIDECAR_IMAGE / SYNTHORG_FINE_TUNE_IMAGE environment lines
// compose.yml.tmpl writes into the backend service. These three images are
// never their own compose service (see imageLinePattern), so patching only
// `image:` lines silently leaves them at their old digests -- and, when
// sandbox mode requires them, patchComposeImageRefs must be able to find
// them to satisfy its own completeness check.
//
// The value group admits a backslash escape, because compose.YAMLStr (which
// renders these lines, and which this file's own patch path re-uses) writes
// an embedded quote as \". A group that stopped at the first `"` could not
// re-read what its own writer can produce, so a registry host carrying a
// literal quote would match nothing and report the line as absent.
var standaloneImageEnvPattern = regexp.MustCompile(
	`(?m)^([ \t]*SYNTHORG_(SANDBOX|SIDECAR|FINE_TUNE)_IMAGE:\s+)"(?:[^"\\\r\n]|\\.)*"([ \t]*(?:#[^\r\n]*)?)$`,
)

// standaloneImageName maps a standaloneImageEnvPattern kind capture
// (SANDBOX/SIDECAR/FINE_TUNE) to the image name FormatImageRef and
// digestPins key on. FINE_TUNE resolves through the configured variant so a
// CPU deployment patches fine-tune-cpu rather than always assuming
// fine-tune-gpu. Returns ok=false for a kind the regex should never
// capture: standaloneImageEnvPattern's alternation only ever produces
// these three literals, so an unrecognised kind means the regex changed
// without this switch changing to match -- failing to patch (via the
// caller's ok check) rather than silently defaulting to fine-tune is what
// surfaces that as a loud "not found" from requireComposeImageRefsPatched
// instead of a mis-patched image.
func standaloneImageName(kind string, state config.State) (name string, ok bool) {
	switch kind {
	case "SANDBOX":
		return "sandbox", true
	case "SIDECAR":
		return "sidecar", true
	case "FINE_TUNE":
		return verify.FineTuneServiceName(state.FineTuneVariantOrDefault()), true
	default:
		return "", false
	}
}

// patchServiceImageRefs updates the backend/web compose `image:` lines (see
// imageLinePattern) and reports which service names it found. The sibling
// of patchStandaloneImageEnvRefs: same find-submatch-track-what-was-found
// shape, over the other half of compose.yml's image references.
func patchServiceImageRefs(existing string, state config.State, digestPins map[string]string) (patched string, found map[string]bool) {
	found = make(map[string]bool)
	pattern := imageLinePattern()
	patched = pattern.ReplaceAllStringFunc(existing, func(match string) string {
		sub := pattern.FindStringSubmatch(match)
		if len(sub) < 4 {
			return match
		}
		prefix := sub[1]  // e.g. "    image: "
		name := sub[2]    // e.g. "backend"
		trailer := sub[3] // e.g. "" or "  # comment"
		repo := images.RepoPrefix() + name
		found[name] = true

		if d, ok := digestPins[name]; ok && d != "" {
			return prefix + repo + "@" + d + trailer
		}
		return prefix + repo + ":" + state.ImageTag + trailer
	})
	return patched, found
}

// patchStandaloneImageEnvRefs updates the sandbox/sidecar/fine-tune image
// references carried in backend's environment block (see
// standaloneImageEnvPattern) and reports which kinds it found, so the
// caller's completeness check can tell a missing line from one that was
// never expected. Quotes the replacement value through compose.YAMLStr,
// the same escaping compose.yml.tmpl itself uses to render these lines, so
// a registry_host/image_repo_prefix override containing a `$` cannot embed
// live, unescaped Compose variable-interpolation syntax via this
// hand-patched path when a full regen would have escaped it.
func patchStandaloneImageEnvRefs(existing string, state config.State, digestPins map[string]string) (patched string, found map[string]bool) {
	found = make(map[string]bool)
	patched = standaloneImageEnvPattern.ReplaceAllStringFunc(existing, func(match string) string {
		sub := standaloneImageEnvPattern.FindStringSubmatch(match)
		if len(sub) < 4 {
			return match
		}
		prefix := sub[1]  // e.g. "      SYNTHORG_SANDBOX_IMAGE: "
		kind := sub[2]    // "SANDBOX", "SIDECAR", or "FINE_TUNE"
		trailer := sub[3] // e.g. "" or "  # comment"

		name, ok := standaloneImageName(kind, state)
		if !ok {
			return match
		}
		found[kind] = true
		ref := verify.FormatImageRef(name, state.ImageTag, digestPins[name])
		return prefix + compose.YAMLStr(ref) + trailer
	})
	return patched, found
}

// patchComposeImageRefs updates only the image references in an existing
// compose.yml without regenerating from the template. This preserves the
// user's compose configuration while allowing image updates.
//
// Returns an error if no image references were found or if not every
// expected reference (backend, web, and -- when enabled -- sandbox,
// sidecar, fine-tune) was patched -- this prevents config.Save from
// advancing state when compose is only partially updated.
func patchComposeImageRefs(state config.State, digestPins map[string]string, safeDir string) error {
	composePath := filepath.Join(safeDir, "compose.yml")
	existing, err := os.ReadFile(composePath) //nolint:gosec // G304: composePath is <data-dir>/compose.yml under the SecurePath-cleaned data dir
	if err != nil {
		return fmt.Errorf("reading compose for image patching: %w", err)
	}

	patched, replaced := patchServiceImageRefs(string(existing), state, digestPins)
	var standaloneFound map[string]bool
	patched, standaloneFound = patchStandaloneImageEnvRefs(patched, state, digestPins)

	if len(replaced) == 0 && len(standaloneFound) == 0 {
		return fmt.Errorf("no synthorg image references found in %s -- compose may be manually edited; run 'synthorg init' to regenerate", composePath)
	}

	if err := requireComposeImageRefsPatched(state, replaced, standaloneFound, composePath); err != nil {
		return err
	}

	return compose.AtomicWriteFile(safeDir, "compose.yml", []byte(patched))
}

// requireComposeImageRefsPatched fails loud when a reference the current
// configuration expects was not found and patched, mirroring the shape of
// compose.yml.tmpl's own conditionals: backend and web unconditionally,
// sandbox and sidecar when sandbox mode is on, fine-tune only when sandbox
// mode AND fine-tuning are both on (fine-tuning always runs inside a
// sandbox container).
func requireComposeImageRefsPatched(state config.State, replaced, standaloneFound map[string]bool, composePath string) error {
	for _, svc := range []string{"backend", "web"} {
		if !replaced[svc] {
			return fmt.Errorf("image reference for %q not found in %s -- compose may be manually edited; run 'synthorg init' to regenerate", svc, composePath)
		}
	}
	if !state.Sandbox {
		return nil
	}
	for _, kind := range []string{"SANDBOX", "SIDECAR"} {
		if !standaloneFound[kind] {
			return fmt.Errorf("SYNTHORG_%s_IMAGE not found in %s -- compose may be manually edited; run 'synthorg init' to regenerate", kind, composePath)
		}
	}
	if state.FineTuning && !standaloneFound["FINE_TUNE"] {
		return fmt.Errorf("SYNTHORG_FINE_TUNE_IMAGE not found in %s -- compose may be manually edited; run 'synthorg init' to regenerate", composePath)
	}
	return nil
}
