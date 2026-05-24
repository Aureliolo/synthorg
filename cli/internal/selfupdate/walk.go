package selfupdate

import (
	"context"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const (
	// releasesPerPage is the page size used by listReleases. GitHub allows
	// up to 100 entries per page on the releases listing endpoint.
	releasesPerPage = 100

	// maxReleasePages bounds how far back the walk will read. 5 pages * 100
	// entries = up to 500 releases, which covers years of history at the
	// current cadence. Beyond that, the early-stop "older than installed"
	// check means real-world walks finish in 1-2 pages.
	maxReleasePages = 5
)

// ReleasesBetween returns the releases in (installed, target], ordered
// oldest-to-newest. Drafts are excluded; pre-releases are excluded unless
// includeDev is true. Returns an empty slice (no error) when no releases
// fall in the range.
func ReleasesBetween(ctx context.Context, installed, target string, includeDev bool) ([]Release, error) {
	return releasesBetweenFromURL(ctx, releasesBaseURL(), installed, target, includeDev)
}

// releasesBaseURL returns the GitHub API endpoint for listing releases. Kept
// as a function so tests can inject an httptest URL via releasesBetweenFromURL.
func releasesBaseURL() string {
	return "https://api.github.com/repos/" + repoSlug + "/releases"
}

// versionTagRe matches the project's release-tag grammar: an optional `v`
// prefix, three numeric components (major.minor.patch), and an optional
// `-dev.N` pre-release suffix. compareWithDev is internally permissive
// (parsePart silently coerces non-numeric components to 0), so an upfront
// regex check is required to surface obviously-malformed installed/target
// strings instead of producing a confusing empty walk result.
var versionTagRe = regexp.MustCompile(`^v?\d+\.\d+\.\d+(-dev\.\d+)?$`)

// releasesBetweenFromURL is the testable core of ReleasesBetween. baseURL is
// the endpoint to paginate against (without per_page / page query params --
// listReleases adds those).
func releasesBetweenFromURL(ctx context.Context, baseURL, installed, target string, includeDev bool) ([]Release, error) {
	if !versionTagRe.MatchString(installed) {
		return nil, fmt.Errorf("invalid installed version %q: expected vX.Y.Z[-dev.N]", installed)
	}
	if !versionTagRe.MatchString(target) {
		return nil, fmt.Errorf("invalid target version %q: expected vX.Y.Z[-dev.N]", target)
	}

	all, err := listReleases(ctx, baseURL)
	if err != nil {
		return nil, err
	}
	filtered := make([]Release, 0, len(all))
	for _, r := range all {
		if !inReleaseWindow(r, installed, target, includeDev) {
			continue
		}
		filtered = append(filtered, Release{
			TagName:     r.TagName,
			Body:        r.Body,
			PublishedAt: r.PublishedAt,
			Assets:      r.Assets,
		})
	}
	sort.SliceStable(filtered, func(i, j int) bool {
		c, _ := compareWithDev(filtered[i].TagName, filtered[j].TagName)
		return c < 0
	})
	return filtered, nil
}

// inReleaseWindow reports whether r belongs in the (installed, target]
// window. Drafts are always rejected; dev pre-releases are rejected
// unless includeDev is true. Malformed tags (compareWithDev error) are
// silently skipped.
func inReleaseWindow(r devRelease, installed, target string, includeDev bool) bool {
	if r.Draft {
		return false
	}
	if !includeDev && isDevTag(r.TagName) {
		return false
	}
	cmpInst, err := compareWithDev(r.TagName, installed)
	if err != nil || cmpInst <= 0 {
		return false
	}
	cmpTar, err := compareWithDev(r.TagName, target)
	if err != nil || cmpTar > 0 {
		return false
	}
	return true
}

// listReleases paginates the releases endpoint with per_page=releasesPerPage
// up to maxReleasePages. Stops when a page returns < releasesPerPage entries
// (last page) or the page cap is reached.
//
// A page-level "older than installed" early-stop is intentionally NOT used:
// the GitHub /releases endpoint orders by publish time, not semver, so a
// page entirely <= installed (e.g. recent backports for older series) can
// still be followed by pages containing in-range releases. The maxReleasePages
// cap bounds the worst case to releasesPerPage * maxReleasePages entries.
//
// Returns the union of all fetched pages (unsorted; caller filters + sorts).
func listReleases(ctx context.Context, baseURL string) ([]devRelease, error) {
	var combined []devRelease
	for page := 1; page <= maxReleasePages; page++ {
		pageURL, err := buildPageURL(baseURL, page)
		if err != nil {
			return nil, fmt.Errorf("building page URL: %w", err)
		}
		entries, err := fetchJSON[[]devRelease](ctx, pageURL)
		if err != nil {
			return nil, err
		}
		if len(entries) == 0 {
			break
		}
		combined = append(combined, entries...)
		if len(entries) < releasesPerPage {
			break
		}
	}
	return combined, nil
}

// buildPageURL appends per_page + page query params to baseURL. Returns the
// formatted URL or an error if baseURL is malformed.
func buildPageURL(baseURL string, page int) (string, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return "", err
	}
	q := parsed.Query()
	q.Set("per_page", strconv.Itoa(releasesPerPage))
	q.Set("page", strconv.Itoa(page))
	parsed.RawQuery = q.Encode()
	return parsed.String(), nil
}

// isDevTag reports whether a tag carries the "-dev.N" pre-release suffix.
func isDevTag(tag string) bool {
	return strings.Contains(tag, "-dev.")
}
