#!/usr/bin/env python3
"""Gate: no strong-copyleft licence enters the shipped dependency set.

SynthOrg ships under BUSL-1.1 (-> Apache-2.0 after the Change Date). The
distributed artefacts (the PyPI package, its extras, and the Docker
image) must not statically pull in an AGPL or GPL (non-LGPL) dependency,
and every weak-copyleft (LGPL) dependency that DOES ship must carry an
attribution in the top-level ``NOTICE`` file.

Four independent checks, each env-deterministic enough for a pre-push /
CI gate:

1. **Hard denylist** -- ``pymupdf`` / ``fitz`` (AGPL) must not appear
   anywhere in ``pyproject.toml`` dependency tables or in the resolved
   ``uv.lock`` package set. Parsed via ``tomllib`` so a prose comment
   that merely names the package (``# pymupdf is excluded (AGPL)``) does
   not trip the gate.

2. **Go GPL exclusion** -- ``golangci-lint`` is installed as an external
   binary, never a ``go tool`` directive, so its GPL-3.0 transitive
   closure never enters ``cli/go.mod`` / ``cli/go.sum``. Fail if the name
   appears in either.

   *Go transitive-licence scan (opt-in, ``--scan-go-modules``):* a full
   classification of the CLI's module closure is implemented via
   ``go-licenses`` but is OFF by default, so the fast pre-push gate stays
   name-based and offline. ``go.sum`` records module versions but no
   licence metadata, so the scan shells out to ``go-licenses csv ./...``
   (run in ``cli/``) which fetches every module and inspects its
   ``LICENSE`` file, then classifies each reported licence with the same
   ``_classify`` family logic used for the Python and JS closures. AGPL /
   GPL (non-LGPL) is a hard failure; LGPL must be attributed in ``NOTICE``.
   The opt-in flag is passed by the dedicated ``CLI License Scan`` CI job
   (which provisions the Go toolchain + ``go-licenses``); locally, run
   ``scripts/install_cli_tools.sh go-licenses`` first.

3. **Web JS copyleft scan** -- classify every package in the resolved
   ``web/package-lock.json`` closure by its recorded SPDX ``license``
   field. AGPL or GPL (non-LGPL) is a hard failure. The lockfile carries
   the full transitive closure, so this is a transitive scan that needs
   no ``node_modules`` on disk; an entry with no recorded licence is left
   to the name-based mechanisms.

4. **Direct-dependency copyleft scan** -- classify every DIRECT runtime
   + extras dependency declared in ``pyproject.toml``
   (``[project.dependencies]`` + ``[project.optional-dependencies]``; dev
   ``[dependency-groups]`` tools such as ``codespell`` / ``yamllint`` are
   not shipped and are excluded). Classification reads only the
   STRUCTURED licence metadata -- the SPDX ``License-Expression`` and the
   ``License ::`` trove classifiers -- never the freeform ``License``
   text, whose bundled-component attributions cause substring false
   positives (e.g. SciPy's BSD text quoting LGPL components). AGPL or GPL
   (non-LGPL) is a hard failure; LGPL is permitted only if the dist is
   attributed in ``NOTICE``. A curated ``_KNOWN_LGPL`` set is also
   asserted against ``NOTICE`` directly so the attribution holds even
   when an extra is not synced into the gate's venv. A CORE dependency
   (``[project.dependencies]``) that cannot be resolved fails closed with
   a violation -- core deps are always installed, so an unresolvable one
   would otherwise let a strong-copyleft package slip past classification;
   an unsynced EXTRA is tolerated (it is still covered by the denylist and
   the deterministic NOTICE assertion). Transitive copyleft of unknown
   packages is covered by the name denylist (check 1) over the full
   ``uv.lock`` closure, which is the maintained mechanism for that case:
   transitive licence metadata is too unreliable to classify by scanning.

Exit codes:

* 0: clean.
* 1: a licence-compatibility violation.
* 2: setup failure (missing input file, unparseable TOML).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

# Packages whose licence is incompatible with redistribution under this
# project's terms at any depth. Names are canonicalised before matching.
_HARD_DENYLIST: frozenset[str] = frozenset(
    {"pymupdf", "fitz", "pymupdf4llm"},  # AGPL-3.0
)

# Weak-copyleft (LGPL) dists known to ship. Each MUST be attributed in
# NOTICE. The shipped-closure scan also discovers any LGPL dist not
# listed here; this set guarantees coverage even when the relevant extra
# is not synced into the gate's environment. ``psycopg-binary`` ships via
# the ``psycopg[binary]`` extra (never as a direct requirement name), so
# it is asserted here rather than discovered through the declared set.
_KNOWN_LGPL: frozenset[str] = frozenset({"psycopg", "psycopg-pool", "psycopg-binary"})

_GO_GPL_TOOLS: frozenset[str] = frozenset({"golangci-lint"})

# Upper bound for the opt-in ``go-licenses`` scan: it fetches the whole CLI
# module closure and inspects each LICENSE file, so the wall is generous.
_GO_LICENSES_TIMEOUT_SECONDS: int = 600

# ``go-licenses csv`` rows are ``<import path>,<licence URL>,<licence name>``;
# rows with fewer fields are malformed and skipped.
_GO_LICENSES_CSV_MIN_FIELDS: int = 3

# A Go module root is conventionally the first three import-path components
# (``host/org/repo``); used to match NOTICE attributions.
_GO_MODULE_ROOT_SEGMENTS: int = 3


@dataclass(frozen=True)
class Violation:
    """A single licence-compatibility failure."""

    location: str
    message: str

    def render(self) -> str:
        """Format for stdout."""
        return f"{self.location}: {self.message}"


class SetupError(Exception):
    """A gate input could not be read or parsed (exit code 2)."""


# ── pyproject / uv.lock parsing ─────────────────────────────────


def _load_toml(path: Path) -> dict[str, object]:
    """Parse a TOML file, mapping I/O and syntax failures to SetupError.

    Returns:
        The parsed mapping.

    Raises:
        SetupError: If the file is missing or not valid TOML.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        msg = f"required file missing: {path}"
        raise SetupError(msg) from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"could not parse {path}: {type(exc).__name__}: {exc}"
        raise SetupError(msg) from exc


def _requirement_name(spec: str) -> str | None:
    """Canonical distribution name from a PEP 508 requirement string.

    Returns:
        The canonical name, or ``None`` when the spec is not a parseable
        requirement (e.g. a bare path or a malformed entry).
    """
    try:
        return canonicalize_name(Requirement(spec).name)
    except InvalidRequirement:
        return None


def _pyproject_dependency_specs(pyproject: dict[str, object]) -> list[str]:
    """Collect every runtime + extras requirement string from pyproject.

    Dev tooling under ``[dependency-groups]`` is intentionally excluded:
    those packages are never shipped to a consumer, so a GPL linter
    (codespell, yamllint) there is irrelevant to redistribution.

    Returns:
        Requirement strings from ``[project.dependencies]`` and every
        ``[project.optional-dependencies]`` extra.
    """
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return []
    specs: list[str] = []
    deps = project.get("dependencies")
    if isinstance(deps, list):
        specs.extend(str(item) for item in deps)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra in optional.values():
            if isinstance(extra, list):
                specs.extend(str(item) for item in extra)
    return specs


def _pyproject_core_dependency_names(pyproject: dict[str, object]) -> set[str]:
    """Canonical names of the always-installed core runtime dependencies.

    Only ``[project.dependencies]`` -- the unconditionally-installed
    runtime set -- excluding ``[project.optional-dependencies]`` extras
    (``fine-tune-*``, ``knowledge``, ...) that a gate environment may
    legitimately not sync. A core dependency that cannot be resolved is
    an anomaly worth failing closed on; an unsynced extra is expected.

    Returns:
        Canonical distribution names from ``[project.dependencies]``.
    """
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return set()
    deps = project.get("dependencies")
    if not isinstance(deps, list):
        return set()
    return {name for item in deps if (name := _requirement_name(str(item))) is not None}


def _uv_lock_package_names(lock: dict[str, object]) -> set[str]:
    """Canonical names of every package recorded in uv.lock.

    Returns:
        The full resolved closure's distribution names (direct +
        transitive + dev), so a denied transitive cannot hide.
    """
    names: set[str] = set()
    packages = lock.get("package")
    if isinstance(packages, list):
        for package in packages:
            if isinstance(package, dict):
                name = package.get("name")
                if isinstance(name, str):
                    names.add(canonicalize_name(name))
    return names


# ── installed-dist licence classification ───────────────────────


def _license_blob(dist: metadata.Distribution) -> str:
    """Lowercased STRUCTURED licence metadata of a dist.

    Deliberately excludes the freeform ``License`` field: packages often
    paste a full licence text there that quotes the names of bundled
    components under other licences (SciPy's BSD text names LGPL
    components), which substring classification would misread. The SPDX
    ``License-Expression`` and the ``License ::`` trove classifiers are
    structured and authoritative for the dist's own licence.

    Returns:
        ``License-Expression`` + every ``License ::`` trove classifier,
        lowercased, for substring classification.
    """
    meta = dist.metadata
    parts: list[str] = []
    expression = meta.get("License-Expression")
    if expression:
        parts.append(str(expression))
    parts.extend(
        classifier
        for classifier in (meta.get_all("Classifier") or [])
        if classifier.startswith("License ::")
    )
    return " ".join(parts).lower()


def _classify(blob: str) -> str:
    """Classify a licence blob into a copyleft family.

    Recognises both the SPDX short forms (``agpl`` / ``lgpl`` / ``gpl``)
    and the spelled-out trove-classifier names (``GNU Affero General
    Public License`` etc.), which carry no SPDX abbreviation. Order
    matters: every long form ends in ``general public license`` and the
    short forms ``agpl`` / ``lgpl`` both contain ``gpl``, so the more
    specific families are tested first.

    Returns:
        One of ``"agpl"``, ``"lgpl"``, ``"gpl"``, or ``"permissive"``.
    """
    if "agpl" in blob or "affero general public license" in blob:
        return "agpl"
    if "lgpl" in blob or "lesser general public license" in blob:
        return "lgpl"
    if "gpl" in blob or "general public license" in blob:
        return "gpl"
    return "permissive"


# ── NOTICE coverage ─────────────────────────────────────────────


def _notice_text(repo_root: Path) -> str:
    """Read the top-level NOTICE file lowercased.

    Returns:
        The lowercased NOTICE contents.

    Raises:
        SetupError: If NOTICE is missing or unreadable.
    """
    path = repo_root / "NOTICE"
    try:
        return path.read_text(encoding="utf-8").lower()
    except FileNotFoundError as exc:
        msg = "required file missing: NOTICE (LGPL attribution is mandatory)"
        raise SetupError(msg) from exc
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"could not read NOTICE: {type(exc).__name__}: {exc}"
        raise SetupError(msg) from exc


def _notice_covers(notice: str, dist_name: str) -> bool:
    """Whether NOTICE attributes ``dist_name``.

    Matches both the canonical hyphenated form and the underscore form so
    ``psycopg-pool`` is found whether NOTICE writes it as ``psycopg-pool``
    or ``psycopg_pool``.

    Returns:
        ``True`` when an attribution for the dist is present.
    """
    canonical = canonicalize_name(dist_name)
    return canonical in notice or canonical.replace("-", "_") in notice


# ── checks ──────────────────────────────────────────────────────


def _check_denylist(
    pyproject: dict[str, object],
    lock: dict[str, object],
) -> list[Violation]:
    """Fail if any hard-denied package is declared or resolved."""
    declared = {
        name
        for spec in _pyproject_dependency_specs(pyproject)
        if (name := _requirement_name(spec)) is not None
    }
    violations: list[Violation] = [
        Violation("pyproject.toml", f"hard-denied dependency declared: {hit}")
        for hit in sorted(declared & _HARD_DENYLIST)
    ]
    violations.extend(
        Violation("uv.lock", f"hard-denied package in resolved closure: {hit}")
        for hit in sorted(_uv_lock_package_names(lock) & _HARD_DENYLIST)
    )
    return violations


def _check_go_gpl(repo_root: Path) -> list[Violation]:
    """Fail if a GPL Go tool leaked into the CLI module graph."""
    violations: list[Violation] = []
    for rel in ("cli/go.mod", "cli/go.sum"):
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"could not read {rel}: {type(exc).__name__}: {exc}"
            raise SetupError(msg) from exc
        violations.extend(
            Violation(
                rel,
                f"GPL Go tool {tool!r} must stay an external binary,"
                " never a module dependency",
            )
            for tool in sorted(_GO_GPL_TOOLS)
            if tool in text
        )
    return violations


def _run_go_licenses(cli_dir: Path) -> str:
    """Run ``go-licenses csv ./...`` in ``cli_dir`` and return its stdout.

    ``go-licenses`` may exit non-zero when a package in the closure cannot
    be analysed (vendored C, a module with no ``LICENSE`` file) while still
    emitting valid CSV rows for everything it could classify. Those rows
    are the signal, so a non-zero exit is tolerated as long as stdout
    carries data; only a missing binary or a truly empty result is a
    SetupError.

    Returns:
        The captured CSV stdout.

    Raises:
        SetupError: If ``go-licenses`` is absent, times out, or produces
            no parseable output.
    """
    if shutil.which("go-licenses") is None:
        msg = (
            "go-licenses not on PATH; install it with "
            "`scripts/install_cli_tools.sh go-licenses` before "
            "running --scan-go-modules"
        )
        raise SetupError(msg)
    try:
        completed = subprocess.run(
            ["go-licenses", "csv", "./..."],
            cwd=cli_dir,
            capture_output=True,
            text=True,
            timeout=_GO_LICENSES_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        msg = f"go-licenses could not be executed: {exc}"
        raise SetupError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"go-licenses timed out after {_GO_LICENSES_TIMEOUT_SECONDS}s"
        raise SetupError(msg) from exc
    if not completed.stdout.strip():
        detail = completed.stderr.strip() or "no output"
        msg = f"go-licenses produced no licence rows (rc={completed.returncode}): {detail}"
        raise SetupError(msg)
    if completed.returncode != 0 and completed.stderr.strip():
        # go-licenses exited non-zero but still emitted rows: some modules were
        # skipped (e.g. no LICENSE file found). Surface them so an incomplete
        # closure does not pass silently as a clean scan.
        print(
            f"::warning::go-licenses skipped modules (rc={completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _go_notice_covers(notice: str, module: str) -> bool:
    """Whether NOTICE attributes a Go module by path, root, or leaf.

    ``_notice_covers`` canonicalises its argument as a Python distribution
    name, which mangles the dots in a Go import path (``github.com`` ->
    ``github-com``), so it cannot match a Go module path written verbatim in
    NOTICE. This does a direct, case-insensitive substring check against the
    full import path, the conventional module root (first three path
    components, e.g. ``github.com/org/repo``), and the leaf segment.

    Returns:
        ``True`` when any candidate form is attributed in NOTICE.
    """
    notice_lower = notice.lower()
    parts = module.split("/")
    module_root = (
        "/".join(parts[:_GO_MODULE_ROOT_SEGMENTS])
        if len(parts) >= _GO_MODULE_ROOT_SEGMENTS
        else module
    )
    candidates = {module, module_root, parts[-1]}
    return any(
        candidate.lower() in notice_lower for candidate in candidates if candidate
    )


def _check_go_licenses(repo_root: Path, notice: str, *, run: bool) -> list[Violation]:
    """Classify the CLI module closure's licences via ``go-licenses``.

    Off unless ``run`` is True (the ``--scan-go-modules`` opt-in): the scan
    needs the Go toolchain and network access, so it runs in a dedicated CI
    job rather than the fast pre-push gate. Each CSV row is
    ``<import path>,<licence URL>,<licence name>``; the licence name is
    classified with the same family logic as the Python and JS closures.
    AGPL / GPL (non-LGPL) is a hard failure; LGPL must be attributed in
    ``NOTICE``.
    """
    if not run:
        return []
    cli_dir = repo_root / "cli"
    if not (cli_dir / "go.mod").is_file():
        return []
    output = _run_go_licenses(cli_dir)
    violations: list[Violation] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(",")
        if len(fields) < _GO_LICENSES_CSV_MIN_FIELDS:
            continue
        module = fields[0].strip()
        license_name = fields[-1].strip()
        family = _classify(license_name.lower())
        if family in {"agpl", "gpl"}:
            violations.append(
                Violation(
                    "cli go module closure",
                    f"Go dependency {module!r} is {family.upper()}-licensed"
                    f" ({license_name}); strong copyleft is incompatible with"
                    " redistribution",
                )
            )
        elif family == "lgpl" and not _go_notice_covers(notice, module):
            violations.append(
                Violation(
                    "NOTICE",
                    f"LGPL Go dependency {module!r} ({license_name}) ships but"
                    " is not attributed in NOTICE",
                )
            )
    return violations


def _check_direct_copyleft(
    pyproject: dict[str, object],
    notice: str,
) -> list[Violation]:
    """Classify every DIRECT runtime+extras dependency by licence.

    A CORE dependency (``[project.dependencies]``) that cannot be resolved
    fails closed with a Violation: core deps are always installed in any
    working environment, so an unresolvable one would otherwise let a
    strong-copyleft package slip past classification. An unsynced EXTRA
    dependency (``fine-tune-*`` etc.) is skipped -- it is legitimately
    absent and still covered by the name denylist over the uv.lock closure
    plus the deterministic ``_KNOWN_LGPL``/NOTICE assertion.
    """
    violations: list[Violation] = []
    core = _pyproject_core_dependency_names(pyproject)
    direct = sorted(
        {
            name
            for spec in _pyproject_dependency_specs(pyproject)
            if (name := _requirement_name(spec)) is not None
        }
    )
    for name in direct:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            if name in core:
                violations.append(
                    Violation(
                        "dependencies",
                        f"core dependency {name!r} could not be resolved for"
                        " licence classification; sync the environment so the"
                        " copyleft gate cannot fail open",
                    )
                )
            # An unsynced EXTRA cannot be classified here; the denylist
            # (uv.lock) and _KNOWN_LGPL/NOTICE checks remain authoritative.
            continue
        family = _classify(_license_blob(dist))
        if family in {"agpl", "gpl"}:
            violations.append(
                Violation(
                    "dependencies",
                    f"direct dependency {name!r} is {family.upper()}-licensed;"
                    " strong copyleft is incompatible with redistribution",
                )
            )
        elif family == "lgpl" and not _notice_covers(notice, name):
            violations.append(
                Violation(
                    "NOTICE",
                    f"LGPL dependency {name!r} ships but is not attributed in NOTICE",
                )
            )
    return violations


def _web_package_license_blob(entry: dict[str, object]) -> str:
    """Lowercased licence text for one ``package-lock.json`` package entry.

    npm records the SPDX id in ``license`` (a string) or the legacy
    ``licenses`` array of ``{"type": ...}`` objects. Both are read so a
    dependency declaring either form is classified.

    Returns:
        The joined, lowercased licence identifiers (empty when the entry
        records none -- such packages cannot be classified here and are
        left to the name-based mechanisms).
    """
    parts: list[str] = []
    license_field = entry.get("license")
    if isinstance(license_field, str):
        parts.append(license_field)
    licenses_field = entry.get("licenses")
    if isinstance(licenses_field, list):
        parts.extend(
            str(item["type"])
            for item in licenses_field
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        )
    return " ".join(parts).lower()


def _check_web_copyleft(repo_root: Path) -> list[Violation]:
    """Classify every JS dependency in ``web/package-lock.json`` by licence.

    The lockfile (v2/v3) records the full resolved closure under
    ``packages`` with a per-package SPDX ``license`` field, so this is a
    transitive scan without needing ``node_modules`` on disk. A strong
    copyleft (AGPL/GPL non-LGPL) JS dependency is a hard failure; an entry
    with no recorded licence is skipped (it cannot be classified here).
    A missing lockfile is tolerated -- the web app is an optional surface.
    """
    path = repo_root / "web" / "package-lock.json"
    if not path.is_file():
        return []
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"could not read web/package-lock.json: {type(exc).__name__}: {exc}"
        raise SetupError(msg) from exc
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        msg = (
            "web/package-lock.json has no valid 'packages' map (expected a "
            "lockfile v2/v3); cannot run JS copyleft classification"
        )
        raise SetupError(msg)
    violations: list[Violation] = []
    for location, entry in sorted(packages.items()):
        if not location or not isinstance(entry, dict):
            # "" is the root project; skip it.
            continue
        family = _classify(_web_package_license_blob(entry))
        if family in {"agpl", "gpl"}:
            name = location.removeprefix("node_modules/")
            violations.append(
                Violation(
                    "web/package-lock.json",
                    f"JS dependency {name!r} is {family.upper()}-licensed;"
                    " strong copyleft is incompatible with redistribution",
                )
            )
    return violations


def _check_known_lgpl_notice(notice: str) -> list[Violation]:
    """Assert every known-LGPL dep is attributed in NOTICE.

    Deterministic counterpart to the closure scan: holds even when the
    relevant extra is not synced into the gate's venv. Asserts the full
    ``_KNOWN_LGPL`` set unconditionally -- ``psycopg-binary`` ships via
    the ``psycopg[binary]`` extra and so never appears as a direct
    requirement name, yet it must still be attributed.
    """
    return [
        Violation(
            "NOTICE",
            f"known LGPL dependency {name!r} is not attributed in NOTICE",
        )
        for name in sorted(_KNOWN_LGPL)
        if not _notice_covers(notice, name)
    ]


def run_checks(repo_root: Path, *, scan_go_modules: bool = False) -> list[Violation]:
    """Run every licence-compatibility check against the repo.

    Args:
        repo_root: Project root to anchor path resolution against.
        scan_go_modules: When True, also run the opt-in ``go-licenses``
            transitive scan of the CLI module closure (needs the Go
            toolchain + network; default off for the fast pre-push gate).

    Returns:
        All violations, in deterministic order.

    Raises:
        SetupError: If a required input file is missing or unparseable.
    """
    pyproject = _load_toml(repo_root / "pyproject.toml")
    lock = _load_toml(repo_root / "uv.lock")
    notice = _notice_text(repo_root)
    violations: list[Violation] = []
    violations.extend(_check_denylist(pyproject, lock))
    violations.extend(_check_go_gpl(repo_root))
    violations.extend(_check_go_licenses(repo_root, notice, run=scan_go_modules))
    violations.extend(_check_known_lgpl_notice(notice))
    violations.extend(_check_direct_copyleft(pyproject, notice))
    violations.extend(_check_web_copyleft(repo_root))
    return violations


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Project root to anchor path resolution against.",
    )
    parser.add_argument(
        "--scan-go-modules",
        action="store_true",
        help=(
            "Also run the go-licenses transitive scan of the CLI module"
            " closure (needs the Go toolchain + go-licenses on PATH; off by"
            " default so the pre-push gate stays fast and offline)."
        ),
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    try:
        violations = run_checks(repo_root, scan_go_modules=args.scan_go_modules)
    except SetupError as exc:
        print(f"license-compat: setup error: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("license-compat: incompatible licences detected:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation.render()}", file=sys.stderr)
        return 1
    print("license-compat: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
