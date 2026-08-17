# License policy

SynthOrg ships under the Business Source License 1.1, converting to
Apache License 2.0 after the Change Date (see `LICENSE`). To keep
redistribution unencumbered, the dependency set is held to a
copyleft-exclusion policy enforced by `scripts/check_license_compat.py`
(pre-push and CI).

## Rules

- **AGPL and GPL (non-LGPL) are forbidden.** No strong-copyleft package
  may enter a shipped dependency table. `pymupdf` / `fitz` / `pymupdf4llm`
  (AGPL-3.0) are named on a hard denylist and must not appear in
  `pyproject.toml` or in
  the resolved `uv.lock` closure at any depth.
- **LGPL is permitted with attribution.** Weak-copyleft (LGPL-3.0)
  components are dynamically linked / imported as separate works, so they
  may ship, but every LGPL distribution MUST be attributed in the
  top-level `NOTICE` file. This applies to both Python and JS deps: today
  the Python side is `psycopg`, `psycopg_pool`, and `psycopg_binary` (the
  optional `postgres` extra and the published backend image), and the JS
  side covers any LGPL package discovered dynamically in
  `web/package-lock.json`. The Python `_KNOWN_LGPL` set is a curated
  backstop list; JS LGPL deps are not curated but are detected from the
  lockfile's per-package SPDX `license` field.
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
4. **Web JS copyleft scan** -- every package in `web/package-lock.json`
   is classified from its per-entry SPDX `license` field. AGPL / GPL
   (non-LGPL) is a hard failure; an LGPL package must be attributed in
   `NOTICE` (matched by npm package name, scoped or plain) or the gate fails.
   A missing `web/package-lock.json` is tolerated (the scan yields no
   violations) so the gate stays usable before the lockfile exists.
5. **Elected disjunctions** -- a dist named in `_ELECTED_DISJUNCTIVE` is
   resolved from its installed metadata and checked two ways: the offer
   must still reach the arm this project elected, and that election must
   be recorded in `NOTICE`. An unresolvable dist is a violation rather
   than a skip, because absence means the environment cannot answer the
   question the check exists to ask.

Transitive copyleft of unknown packages is covered by the name denylist
over the full `uv.lock` closure rather than by classifying every
transitive distribution: transitive licence metadata is too unreliable
to classify by scanning (a permissive package's bundled-component
attribution text routinely names other licences). When a genuinely new
copyleft dependency needs handling, add it to the denylist (to exclude)
or to `NOTICE` plus the known-LGPL set (to attribute) in the same change.

## Disjunctive licences

An SPDX expression may be a DISJUNCTION -- `MPL-1.1 OR GPL-2.0-only OR
LGPL-2.1-or-later` -- which offers alternatives rather than stacking
obligations. The licensee elects one arm and is bound by that arm alone,
so matching against the whole expression answers a different question
than the one being asked: it would reject a package that is compatible
under an arm we can take, or accept one whose only usable-looking arm we
cannot.

The gate therefore classifies each arm and takes the least restrictive,
which is the arm a licensee elects. Splitting is on the SPACE-DELIMITED
operator, never a word boundary: `GPL-3.0-or-later` is one licence whose
name happens to contain `or`, and splitting inside it yields an arm that
classifies as permissive, quietly passing the strongest copyleft there
is.

Two consequences worth knowing:

- The **direct** scan reads `pyproject.toml`, so a dist reached
  transitively is classified by nothing unless it is named. `tld`
  arrives through `trafilatura` -> `courlan` and is listed in
  `_ELECTED_DISJUNCTIVE` for exactly that reason; without the entry the
  disjunction handling would never run against the package it was
  written for, and `NOTICE`'s election would be prose with no check
  behind it.
- The elected arm is re-verified on every run, not just recorded once. A
  version bump can drop an arm while the package name stays put, which
  leaves a dependency nobody may redistribute sitting behind a green
  gate; the name denylist cannot see that, because nothing about the
  name changed.

## Re-linking LGPL components

The LGPL-3.0 obligation to allow substitution of the linked library is
satisfied as described in `NOTICE`: rebuild the backend image with a
replacement `psycopg` wheel in place of the pinned version. The
`psycopg_binary` C extension is an optional acceleration of the
pure-Python `psycopg`, which can be used standalone.
