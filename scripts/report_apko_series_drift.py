"""Report apko package pins whose upstream series has been superseded.

A pinned name such as ``nodejs-24`` or ``postgresql-18-client`` is a SERIES
pin, not a version pin: apko keeps resolving it for as long as Wolfi ships the
series, so the day ``nodejs-26`` lands nothing anywhere changes and nothing
says so. The lockfile cannot notice (it faithfully records the series that was
asked for), Renovate cannot notice (no manager reads ``apko.yaml``), and the
gate cannot notice (the pin is exactly what it demands). The only place the
fact exists is the upstream index, so that is what this reads.

This is a REPORT, deliberately not a ``check_*`` gate: a newer series existing
is news, not a defect, and blocking a push on somebody else's release cadence
would make the pins the gate demands into a liability.

Exit codes: ``0`` no drift, ``1`` drift found (the Markdown report is on
stdout), ``2`` the scan could not be trusted.
"""

import argparse
import io
import re
import sys
import tarfile
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _apko_lock_lib import (  # type: ignore[import-not-found]
        DOCKER_SUBDIR,
        Findings,
        bare_name,
        declared_contents,
        rel,
    )
else:
    from scripts._apko_lock_lib import (
        DOCKER_SUBDIR,
        Findings,
        bare_name,
        declared_contents,
        rel,
    )

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_EXIT_OK: Final[int] = 0
_EXIT_DRIFT: Final[int] = 1
_EXIT_CONFIG_ERROR: Final[int] = 2

_WOLFI_REPOSITORY: Final[str] = "https://packages.wolfi.dev/os"
_INDEX_URL: Final[str] = f"{_WOLFI_REPOSITORY}/{{arch}}/APKINDEX.tar.gz"
_INDEX_MEMBER: Final[str] = "APKINDEX"
_NAME_FIELD: Final[str] = "P:"
_DEFAULT_ARCH: Final[str] = "x86_64"
_TIMEOUT_SECONDS: Final[int] = 60
# apk indices run to tens of megabytes; anything far past that is not an index.
_MAX_INDEX_BYTES: Final[int] = 64 * 1024 * 1024

# A series token is a run of digits, optionally dotted: `24`, `2.43`, `3.14`.
_SERIES_TOKEN: Final[re.Pattern[str]] = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_TOKEN_PLACEHOLDER: Final[str] = "\x00"  # noqa: S105 -- a substitution marker, not a secret

Series = tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class Pin:
    """A series-carrying package name and where the tree asks for it."""

    name: str
    manifests: tuple[str, ...]


@dataclass(frozen=True)
class Drift:
    """A pin the index has moved past."""

    pin: Pin
    successors: tuple[str, ...]


def _shape(name: str) -> tuple[str, Series]:
    """Return a package name's skeleton and its series tokens.

    ``postgresql-18-client`` becomes a skeleton with the ``18`` replaced by a
    marker, plus ``((18,),)``, so a candidate is comparable only when it spells
    the same thing around the same number of series positions.
    """
    tokens = tuple(
        tuple(int(part) for part in match.split("."))
        for match in _SERIES_TOKEN.findall(name)
    )
    return _SERIES_TOKEN.sub(_TOKEN_PLACEHOLDER, name), tokens


def _supersedes(pinned: Series, candidate: Series) -> bool:
    """Report whether ``candidate`` is a later release of the same series shape.

    Arity is checked at both levels because a series bump keeps the shape of
    the version string. Without it ``py3-pip`` would read ``py3.11-pip`` as a
    successor, which is a different interpreter line rather than a newer one.
    """
    if len(pinned) != len(candidate):
        return False
    if any(len(a) != len(b) for a, b in zip(pinned, candidate, strict=True)):
        return False
    return candidate > pinned


def _iter_manifests(root: Path) -> Iterator[Path]:
    """Yield every apko manifest under ``docker/``, sorted."""
    docker_root = root / DOCKER_SUBDIR
    if not docker_root.is_dir():
        return
    for path in sorted(docker_root.glob("*/apko.yaml")):
        if path.is_file():
            yield path


def _collect_pins(root: Path, findings: Findings) -> list[Pin]:
    """Return every series-carrying package the Wolfi-backed manifests pin."""
    sites: dict[str, list[str]] = {}
    for manifest in _iter_manifests(root):
        contents = declared_contents(root, manifest, findings)
        if contents is None:
            continue
        repositories = contents.get("repositories")
        if not isinstance(repositories, list):
            findings.errors.append(
                f"{rel(root, manifest)}: `contents.repositories` is not a list"
            )
            continue
        if _WOLFI_REPOSITORY not in repositories:
            continue
        packages = contents.get("packages")
        if not isinstance(packages, list):
            findings.errors.append(
                f"{rel(root, manifest)}: `contents.packages` is not a list"
            )
            continue
        for entry in packages:
            if not isinstance(entry, str):
                continue
            name = bare_name(entry)
            if not _SERIES_TOKEN.search(name):
                continue
            sites.setdefault(name, []).append(rel(root, manifest))
    return [Pin(name, tuple(paths)) for name, paths in sorted(sites.items())]


def _index_names(arch: str, findings: Findings) -> frozenset[str]:
    """Return every package name the Wolfi index for ``arch`` publishes."""
    url = _INDEX_URL.format(arch=arch)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310 -- URL is the https Wolfi constant above, not caller input
            raw = response.read(_MAX_INDEX_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        findings.errors.append(f"{url}: could not be fetched ({exc})")
        return frozenset()
    if len(raw) > _MAX_INDEX_BYTES:
        findings.errors.append(f"{url}: index exceeds {_MAX_INDEX_BYTES} bytes")
        return frozenset()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            member = archive.extractfile(_INDEX_MEMBER)
            if member is None:
                findings.errors.append(f"{url}: archive holds no `{_INDEX_MEMBER}`")
                return frozenset()
            text = member.read().decode("utf-8", errors="replace")
    except (tarfile.TarError, KeyError, OSError) as exc:
        findings.errors.append(f"{url}: index would not unpack ({exc})")
        return frozenset()
    names = frozenset(
        line[len(_NAME_FIELD) :]
        for line in text.splitlines()
        if line.startswith(_NAME_FIELD)
    )
    if not names:
        findings.errors.append(f"{url}: index published no package names")
    return names


def _drifted(pins: Sequence[Pin], names: frozenset[str]) -> list[Drift]:
    """Return each pin the index has a later series for."""
    shapes = [(name, *_shape(name)) for name in sorted(names)]
    drifts: list[Drift] = []
    for pin in pins:
        skeleton, series = _shape(pin.name)
        successors = tuple(
            candidate
            for candidate, candidate_skeleton, candidate_series in shapes
            if candidate_skeleton == skeleton and _supersedes(series, candidate_series)
        )
        if successors:
            drifts.append(Drift(pin, successors))
    return drifts


def _report(drifts: Sequence[Drift], arch: str) -> str:
    """Return the Markdown body naming every superseded pin."""
    preamble = (
        "Wolfi publishes a newer series for package pins this tree still asks "
        "for by name. A series pin keeps resolving for as long as the series "
        "ships, so nothing else in the pipeline can raise this: the lockfile "
        "records what was asked for, and no Renovate manager reads `apko.yaml`."
    )
    remedy = (
        "Moving a pin is a deliberate upgrade, not a rubber stamp: edit the "
        "manifest, let the weekly `apko lock` run regenerate the lockfile, and "
        "check whatever reads the package (`RUNTIME_PROGRAMS` in "
        "`src/synthorg/tools/mcp/runtime_provision.py` and the boot preflight "
        "in `src/synthorg/api/lifecycle_helpers/binary_preflight.py` both name "
        "packages by their pinned spelling, and `check_apko_lock_applied.py` "
        "holds them to it)."
    )
    lines = [
        preamble,
        "",
        f"Index: `{_INDEX_URL.format(arch=arch)}`",
        "",
        "| Pinned | Newer series | Manifests |",
        "| --- | --- | --- |",
    ]
    for drift in drifts:
        successors = ", ".join(f"`{name}`" for name in drift.successors)
        manifests = ", ".join(f"`{path}`" for path in drift.pin.manifests)
        lines.append(f"| `{drift.pin.name}` | {successors} | {manifests} |")
    lines.extend(("", remedy))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the report.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` no drift, ``1`` drift found, ``2`` when the scan cannot be
        trusted.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arch",
        default=_DEFAULT_ARCH,
        help=f"apk architecture index to read (default {_DEFAULT_ARCH}).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root to scan (defaults to this checkout).",
    )
    args = parser.parse_args(argv)
    root: Path = args.repo_root.resolve()
    findings = Findings()

    pins = _collect_pins(root, findings)
    if not findings.errors and not pins:
        findings.errors.append(
            f"{rel(root, root / DOCKER_SUBDIR)}: no Wolfi-backed manifest pins a "
            f"series, which means the scan found nothing to watch rather than "
            f"nothing to report."
        )
    names = _index_names(args.arch, findings) if not findings.errors else frozenset()

    if findings.errors:
        for error in findings.errors:
            print(f"report_apko_series_drift: {error}", file=sys.stderr)
        return _EXIT_CONFIG_ERROR

    drifts = _drifted(pins, names)
    if not drifts:
        return _EXIT_OK
    print(_report(drifts, args.arch))
    return _EXIT_DRIFT


if __name__ == "__main__":
    sys.exit(main())
