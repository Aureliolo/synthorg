# License policy

SynthOrg ships under the Business Source License 1.1, converting to
Apache License 2.0 after the Change Date (see `LICENSE`). To keep
redistribution unencumbered, the dependency set is held to a
copyleft-exclusion policy enforced by `scripts/check_license_compat.py`
(pre-push and CI).

## Rules

- **AGPL and GPL (non-LGPL) are forbidden.** No strong-copyleft package
  may enter a shipped dependency table. `pymupdf` / `fitz` (AGPL-3.0) are
  named on a hard denylist and must not appear in `pyproject.toml` or in
  the resolved `uv.lock` closure at any depth.
- **LGPL is permitted with attribution.** Weak-copyleft (LGPL-3.0)
  components are dynamically linked / imported as separate works, so they
  may ship, but every LGPL distribution MUST be attributed in the
  top-level `NOTICE` file. Today that is `psycopg`, `psycopg_pool`, and
  `psycopg_binary` (the optional `postgres` extra and the published
  backend image).
- **GPL developer tooling stays external.** `golangci-lint` (GPL-3.0) is
  installed as a standalone binary and is never added as a `go tool`
  directive, so its transitive closure never enters `cli/go.mod` /
  `cli/go.sum`. Python developer linters with GPL licences (`codespell`,
  `yamllint`) live only in the `dependency-groups` dev set and are never
  shipped to a consumer.

## What the gate checks

1. **Denylist** -- `pyproject.toml` dependency tables and the full
   `uv.lock` package set are parsed (via `tomllib`, so a prose comment
   that merely names a package does not trip the gate) and matched
   against the hard denylist.
2. **Go GPL exclusion** -- `cli/go.mod` and `cli/go.sum` are scanned for
   `golangci-lint`.
3. **Direct-dependency copyleft scan** -- every direct runtime / extras
   dependency declared in `pyproject.toml` is classified from its
   structured licence metadata (the SPDX `License-Expression` and the
   `License ::` trove classifiers, never the freeform licence text).
   AGPL / GPL is a hard failure; LGPL requires `NOTICE` coverage. A
   curated known-LGPL set is also asserted against `NOTICE` so the
   attribution check holds even when the `postgres` extra is not synced
   into the gate's environment.

Transitive copyleft of unknown packages is covered by the name denylist
over the full `uv.lock` closure rather than by classifying every
transitive distribution: transitive licence metadata is too unreliable
to classify by scanning (a permissive package's bundled-component
attribution text routinely names other licences). When a genuinely new
copyleft dependency needs handling, add it to the denylist (to exclude)
or to `NOTICE` plus the known-LGPL set (to attribute) in the same change.

## Re-linking LGPL components

The LGPL-3.0 obligation to allow substitution of the linked library is
satisfied as described in `NOTICE`: rebuild the backend image with a
replacement `psycopg` wheel in place of the pinned version. The
`psycopg_binary` C extension is an optional acceleration of the
pure-Python `psycopg`, which can be used standalone.
