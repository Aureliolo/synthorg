"""Unit tests for ``scripts/check_license_compat.py``.

Loads the gate as a module so its helpers are callable without spawning
subprocesses. The denylist / Go / NOTICE checks run against synthetic
fixture files in ``tmp_path``; the direct-dependency classifier is
exercised against the real installed venv (``psycopg`` is an LGPL dist
present via the postgres extra).
"""

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_license_compat.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_license_compat",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE: Any = cast("Any", _load_script_module())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── _classify ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        ("agpl-3.0-only", "agpl"),
        ("license :: osi approved :: gnu affero general public license v3", "agpl"),
        ("lgpl-3.0-only", "lgpl"),
        ("gnu lesser general public license v3 (lgplv3)", "lgpl"),
        ("gpl-3.0-or-later", "gpl"),
        ("license :: osi approved :: gnu general public license v2 (gplv2)", "gpl"),
        ("mit", "permissive"),
        ("license :: osi approved :: bsd license", "permissive"),
        ("apache-2.0", "permissive"),
    ],
)
def test_classify_orders_copyleft_families(blob: str, expected: str) -> None:
    assert _MODULE._classify(blob) == expected


# ── _notice_covers ──────────────────────────────────────────────


def test_notice_covers_hyphen_and_underscore() -> None:
    notice = "attribution for psycopg-pool here"
    assert _MODULE._notice_covers(notice, "psycopg_pool") is True
    assert _MODULE._notice_covers(notice, "psycopg-pool") is True


def test_notice_covers_absent() -> None:
    assert _MODULE._notice_covers("nothing relevant", "psycopg") is False


# ── _web_notice_covers ──────────────────────────────────────────


def test_web_notice_covers_scoped_full_name() -> None:
    notice = "attribution for @scope/widget here"
    assert _MODULE._web_notice_covers(notice, "@scope/widget") is True


def test_web_notice_covers_unscoped_basename() -> None:
    notice = "the lodash library is attributed here"
    assert _MODULE._web_notice_covers(notice, "lodash") is True


def test_web_notice_covers_basename_not_a_substring_false_positive() -> None:
    # A generic basename ("core") must not clear attribution by matching
    # inside a larger npm name ("core-js", "@types/core").
    notice = "core-js and @types/core are attributed here"
    assert _MODULE._web_notice_covers(notice, "core") is False
    assert _MODULE._web_notice_covers(notice, "@scope/core") is False


def test_web_notice_covers_absent() -> None:
    assert _MODULE._web_notice_covers("nothing relevant", "@scope/widget") is False


# ── denylist ────────────────────────────────────────────────────

_CLEAN_PYPROJECT = """
[project]
name = "demo"
dependencies = ["httpx==1.0.0"]

[project.optional-dependencies]
postgres = ["psycopg[binary]==3.3.4", "psycopg_pool==3.3.1"]
"""

_CLEAN_LOCK = """
[[package]]
name = "httpx"
version = "1.0.0"

[[package]]
name = "psycopg"
version = "3.3.4"
"""


def test_denylist_flags_pyproject_declaration() -> None:
    import tomllib

    pyproject = tomllib.loads(
        _CLEAN_PYPROJECT.replace('"httpx==1.0.0"', '"httpx==1.0.0", "pymupdf==1.0.0"')
    )
    lock = tomllib.loads(_CLEAN_LOCK)
    violations = _MODULE._check_denylist(pyproject, lock)
    assert any("pymupdf" in v.message for v in violations)


def test_denylist_flags_transitive_in_uv_lock() -> None:
    import tomllib

    pyproject = tomllib.loads(_CLEAN_PYPROJECT)
    fitz_pkg = '\n[[package]]\nname = "fitz"\nversion = "1.0"\n'
    lock = tomllib.loads(_CLEAN_LOCK + fitz_pkg)
    violations = _MODULE._check_denylist(pyproject, lock)
    assert any("fitz" in v.message for v in violations)


def test_denylist_clean_passes() -> None:
    import tomllib

    pyproject = tomllib.loads(_CLEAN_PYPROJECT)
    lock = tomllib.loads(_CLEAN_LOCK)
    assert _MODULE._check_denylist(pyproject, lock) == []


def test_denylist_ignores_comment_mention() -> None:
    # A prose comment naming the package must not trip the gate -- it is
    # parsed via tomllib, not substring-scanned.
    import tomllib

    pyproject = tomllib.loads(
        '[project]\nname = "demo"\n'
        "# pymupdf is deliberately excluded (AGPL)\n"
        'dependencies = ["httpx==1.0.0"]\n'
    )
    assert _MODULE._check_denylist(pyproject, tomllib.loads(_CLEAN_LOCK)) == []


# ── Go GPL exclusion ────────────────────────────────────────────


def test_go_gpl_flags_golangci_in_gomod(tmp_path: Path) -> None:
    _write(
        tmp_path / "cli" / "go.mod",
        "module x\n\nrequire github.com/golangci/golangci-lint v1.0.0\n",
    )
    violations = _MODULE._check_go_gpl(tmp_path)
    assert any("golangci-lint" in v.message for v in violations)


def test_go_gpl_clean_passes(tmp_path: Path) -> None:
    _write(tmp_path / "cli" / "go.mod", "module x\nrequire github.com/spf13/cobra v1\n")
    _write(tmp_path / "cli" / "go.sum", "github.com/spf13/cobra v1 h1:abc\n")
    assert _MODULE._check_go_gpl(tmp_path) == []


def test_go_gpl_absent_files_no_violation(tmp_path: Path) -> None:
    assert _MODULE._check_go_gpl(tmp_path) == []


# ── Go module-closure licence scan (go-licenses, opt-in) ────────


def _stub_go_licenses(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    stderr: str = "",
    returncode: int = 0,
    on_path: bool = True,
) -> None:
    """Patch the gate's ``shutil.which`` + ``subprocess.run`` for the scan."""

    def _fake_which(name: str) -> str | None:
        if on_path and name == "go-licenses":
            return "/usr/bin/go-licenses"
        return None

    monkeypatch.setattr(_MODULE.shutil, "which", _fake_which)

    def _fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run)


def test_go_licenses_skipped_when_not_run(tmp_path: Path) -> None:
    # run=False short-circuits before touching the toolchain.
    assert _MODULE._check_go_licenses(tmp_path, "", run=False) == []


def test_go_licenses_absent_gomod_no_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_go_licenses(monkeypatch, stdout="x,y,MIT\n")
    # No cli/go.mod => nothing to scan even when run=True.
    assert _MODULE._check_go_licenses(tmp_path, "", run=True) == []


def test_go_licenses_flags_gpl_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    _stub_go_licenses(
        monkeypatch,
        stdout=(
            "github.com/spf13/cobra,https://x/LICENSE.txt,Apache-2.0\n"
            "github.com/evil/gpl,https://x/COPYING,GPL-3.0\n"
        ),
    )
    violations = _MODULE._check_go_licenses(tmp_path, "", run=True)
    assert any(
        "github.com/evil/gpl" in v.message and "GPL" in v.message for v in violations
    )


def test_go_licenses_clean_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    _stub_go_licenses(
        monkeypatch,
        stdout=(
            "github.com/spf13/cobra,https://x/LICENSE.txt,Apache-2.0\n"
            "github.com/spf13/pflag,https://x/LICENSE,BSD-3-Clause\n"
        ),
    )
    assert _MODULE._check_go_licenses(tmp_path, "", run=True) == []


def test_go_licenses_lgpl_requires_notice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    _stub_go_licenses(
        monkeypatch,
        stdout="github.com/some/weaklib,https://x/COPYING.LESSER,LGPL-3.0\n",
    )
    # Unattributed LGPL is a violation; only a full-path / module-root
    # attribution clears it. A bare leaf segment in NOTICE prose must not,
    # since a generic leaf word could falsely clear attribution.
    assert _MODULE._check_go_licenses(tmp_path, "", run=True)
    assert _MODULE._check_go_licenses(tmp_path, "weaklib is attributed", run=True)
    assert (
        _MODULE._check_go_licenses(
            tmp_path, "github.com/some/weaklib is attributed", run=True
        )
        == []
    )


def test_go_licenses_missing_binary_setup_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    _stub_go_licenses(monkeypatch, stdout="", on_path=False)
    with pytest.raises(_MODULE.SetupError, match="go-licenses not on PATH"):
        _MODULE._check_go_licenses(tmp_path, "", run=True)


def test_go_licenses_empty_output_setup_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    _stub_go_licenses(monkeypatch, stdout="   ", stderr="boom", returncode=1)
    with pytest.raises(_MODULE.SetupError, match="no licence rows"):
        _MODULE._check_go_licenses(tmp_path, "", run=True)


# ── web JS copyleft scan ────────────────────────────────────────


def test_web_copyleft_flags_gpl_dependency(tmp_path: Path) -> None:
    _write(
        tmp_path / "web" / "package-lock.json",
        '{"packages": {"": {"name": "root"}, '
        '"node_modules/some-lib": {"version": "1.0.0", "license": "GPL-3.0-only"}}}',
    )
    violations = _MODULE._check_web_copyleft(tmp_path, "")
    assert any("some-lib" in v.message for v in violations)


def test_web_copyleft_flags_legacy_licenses_array(tmp_path: Path) -> None:
    _write(
        tmp_path / "web" / "package-lock.json",
        '{"packages": {"node_modules/agpl-lib": {"licenses": [{"type": "AGPL-3.0"}]}}}',
    )
    violations = _MODULE._check_web_copyleft(tmp_path, "")
    assert any("agpl-lib" in v.message for v in violations)


def test_web_copyleft_permissive_passes(tmp_path: Path) -> None:
    _write(
        tmp_path / "web" / "package-lock.json",
        '{"packages": {"node_modules/mit-lib": {"license": "MIT"}, '
        '"node_modules/no-license": {"version": "2.0.0"}}}',
    )
    assert _MODULE._check_web_copyleft(tmp_path, "") == []


def test_web_copyleft_absent_lockfile_no_violation(tmp_path: Path) -> None:
    assert _MODULE._check_web_copyleft(tmp_path, "") == []


def test_web_copyleft_missing_packages_map_fails_closed(tmp_path: Path) -> None:
    # A readable lockfile with no 'packages' map must not silently pass:
    # that would fail-open and let copyleft JS deps bypass enforcement.
    _write(tmp_path / "web" / "package-lock.json", '{"lockfileVersion": 1}')
    with pytest.raises(_MODULE.SetupError):
        _MODULE._check_web_copyleft(tmp_path, "")


def test_web_copyleft_lgpl_without_notice_flags(tmp_path: Path) -> None:
    _write(
        tmp_path / "web" / "package-lock.json",
        '{"packages": {"node_modules/lgpl-lib": {"license": "LGPL-3.0-only"}}}',
    )
    violations = _MODULE._check_web_copyleft(tmp_path, "")
    assert any("lgpl-lib" in v.message and v.location == "NOTICE" for v in violations)


def test_web_copyleft_lgpl_attributed_in_notice_passes(tmp_path: Path) -> None:
    _write(
        tmp_path / "web" / "package-lock.json",
        '{"packages": {"node_modules/lgpl-lib": {"license": "LGPL-3.0-only"}}}',
    )
    notice = "Third-party notices\n- lgpl-lib (LGPL-3.0)\n"
    assert _MODULE._check_web_copyleft(tmp_path, notice) == []


def test_web_copyleft_scoped_lgpl_attributed_by_basename_passes(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "web" / "package-lock.json",
        '{"packages": {"node_modules/@scope/lgpl-lib": '
        '{"license": "LGPL-2.1-or-later"}}}',
    )
    # Attribution by the unscoped basename counts.
    assert _MODULE._check_web_copyleft(tmp_path, "lgpl-lib") == []


# ── known-LGPL NOTICE coverage ──────────────────────────────────


def test_known_lgpl_requires_notice() -> None:
    violations = _MODULE._check_known_lgpl_notice("no attribution here")
    names = " ".join(v.message for v in violations)
    assert "psycopg" in names


def test_known_lgpl_requires_psycopg_binary_attribution() -> None:
    # ``psycopg-binary`` ships via the ``psycopg[binary]`` extra and never
    # appears as a direct requirement name, so a NOTICE that lists only
    # ``psycopg`` / ``psycopg-pool`` must still be flagged.
    notice = "attributes psycopg and psycopg-pool".lower()
    violations = _MODULE._check_known_lgpl_notice(notice)
    assert any("psycopg-binary" in v.message for v in violations)


def test_known_lgpl_satisfied_by_notice() -> None:
    notice = "attributes psycopg, psycopg-pool and psycopg-binary".lower()
    assert _MODULE._check_known_lgpl_notice(notice) == []


# ── SPDX disjunction resolution ─────────────────────────────────


def test_disjunction_elects_the_least_restrictive_arm() -> None:
    """An OR expression is an offer, so the arm a licensee would take wins."""
    assert _MODULE._classify("gpl-3.0-only or lgpl-2.1-or-later") == "lgpl"


def test_disjunction_with_a_permissive_arm_elects_it() -> None:
    assert _MODULE._classify("gpl-3.0-only or mit") == "permissive"


def test_the_real_tld_offer_clears_the_gate_on_its_weakest_arm() -> None:
    """The expression that motivated this handling, classified end to end.

    The family model knows only the GPL ladder, so an arm naming none of those
    reads as permissive. That is the correct direction here: the offer includes
    an arm carrying no GPL obligation, so nothing about it can fail the gate.
    NOTICE separately records which arm was actually elected, because the crude
    family is not a licence decision.
    """
    offer = "mpl-1.1 or gpl-2.0-only or lgpl-2.1-or-later"
    assert _MODULE._classify(offer) == "permissive"


def test_disjunction_of_strong_copyleft_stays_strong() -> None:
    """Nothing weaker is on offer, so there is no compatible arm to elect."""
    assert _MODULE._classify("agpl-3.0-only or gpl-3.0-only") == "gpl"


def test_or_inside_a_licence_identifier_is_not_a_disjunction() -> None:
    """``-or-later`` is part of one name; splitting it passes the strongest
    copyleft there is as permissive."""
    assert _MODULE._classify("gpl-3.0-or-later") == "gpl"
    assert _MODULE._classify("lgpl-2.1-or-later") == "lgpl"
    assert _MODULE._classify("agpl-3.0-or-later") == "agpl"


def test_disjunction_arms_split_on_the_operator_only() -> None:
    arms = _MODULE._disjunction_arms("mpl-1.1 or gpl-2.0-only or lgpl-2.1-or-later")
    assert arms == ["mpl-1.1", "gpl-2.0-only", "lgpl-2.1-or-later"]


def test_single_licence_is_one_arm() -> None:
    assert _MODULE._disjunction_arms("gpl-3.0-or-later") == ["gpl-3.0-or-later"]


# ── expression and classifiers are classified apart ─────────────


def test_a_trove_classifier_is_prose_not_an_spdx_disjunction() -> None:
    """The canonical LGPL classifier contains the word ``or``.

    ``GNU Library or Lesser General Public License (LGPL)`` is ONE licence
    name. Split on the SPDX operator it yields the arm ``gnu library``, which
    matches no family and so reads permissive, and the least-restrictive rule
    then elects it: an LGPL dependency classified permissive walks past the
    NOTICE-attribution requirement that exists for exactly that licence.
    """
    classifier = (
        "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)"
    )
    dist = SimpleNamespace(metadata=_FakeMeta("", (classifier,)))

    assert _MODULE._classify_dist(dist) == "lgpl"


def test_the_spdx_expression_wins_over_classifiers() -> None:
    """PEP 639 makes the expression authoritative; a dist may carry both."""
    dist = SimpleNamespace(
        metadata=_FakeMeta(
            "MIT",
            ("License :: OSI Approved :: MIT License",),
        )
    )

    assert _MODULE._classify_dist(dist) == "permissive"


def test_classifiers_are_classified_whole_so_the_strongest_wins() -> None:
    """Without an expression, several classifiers are not an offer of arms.

    A dist listing two licence classifiers is describing obligations that both
    apply, so the most restrictive governs; treating the list as a disjunction
    would elect the weaker and under-report.
    """
    dist = SimpleNamespace(
        metadata=_FakeMeta(
            "",
            (
                "License :: OSI Approved :: MIT License",
                "License :: OSI Approved :: GNU General Public License (GPL)",
            ),
        )
    )

    assert _MODULE._classify_dist(dist) == "gpl"


# ── elected transitive disjunctions ─────────────────────────────


def test_elected_disjunctive_requires_notice_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The elected arm is a licence obligation, so NOTICE must record it."""
    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"tld": "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1"}),
    )
    violations = _MODULE._check_elected_disjunctive("no attribution here")
    assert any("tld" in v.message and v.location == "NOTICE" for v in violations)


def test_elected_disjunctive_satisfied_by_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"tld": "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1"}),
    )
    assert _MODULE._check_elected_disjunctive("attributes tld here") == []


def test_elected_disjunctive_fails_when_the_arm_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression a name denylist cannot see.

    A version bump can drop the arm this project elected while the package name
    stays put, which leaves a dependency nobody may redistribute sitting behind
    a green gate.
    """
    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"tld": "GPL-2.0-only"}),
    )
    violations = _MODULE._check_elected_disjunctive("attributes tld here")
    assert any(
        "tld" in v.message and "no longer offers" in v.message for v in violations
    )


def test_elected_disjunctive_fails_when_only_a_weaker_arm_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blind spot in comparing restrictiveness instead of membership.

    An offer that drops to MPL-1.1 alone is LESS restrictive than the elected
    LGPL arm, so a rank test reads it as fine. But NOTICE states that this
    project elects LGPL-2.1-or-later, and that arm is no longer on offer: the
    attribution now describes an election nobody can make.
    """
    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"tld": "MPL-1.1"}),
    )

    violations = _MODULE._check_elected_disjunctive("attributes tld here")

    assert any(
        "tld" in v.message and "no longer offers" in v.message for v in violations
    )


def test_elected_disjunctive_reports_an_unresolvable_dist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent means the environment cannot answer, which is not a pass."""

    def _missing(name: str) -> object:
        raise _MODULE.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(_MODULE.metadata, "distribution", _missing)
    violations = _MODULE._check_elected_disjunctive("attributes tld here")
    assert any("could not be resolved" in v.message for v in violations)


def test_the_real_tld_still_offers_the_elected_arm() -> None:
    """Runs against the installed distribution, not a fake.

    This is the check the disjunction handling was written for; asserting it
    only against synthetic blobs would leave the real package unverified.
    """
    assert _MODULE._check_elected_disjunctive(_MODULE._notice_text(_REPO_ROOT)) == []


# ── direct copyleft scan (deterministic via monkeypatch) ────────


class _FakeMeta:
    """Minimal stand-in for ``importlib.metadata`` ``PackageMetadata``."""

    def __init__(self, expression: str, classifiers: tuple[str, ...] = ()) -> None:
        self._expression = expression
        self._classifiers = classifiers

    def get(self, key: str) -> str | None:
        return self._expression if key == "License-Expression" else None

    def get_all(self, key: str) -> list[str]:
        return list(self._classifiers) if key == "Classifier" else []


def _fake_distribution_factory(
    classifier: dict[str, str],
) -> Callable[[str], object]:
    """Build a ``metadata.distribution`` replacement keyed by name substring.

    ``classifier`` maps a name substring to the SPDX licence expression the
    fake dist should report. The first matching substring wins; an unmatched
    name raises ``PackageNotFoundError`` so the unsynced-extra path can be
    exercised deterministically.
    """

    def _distribution(name: str) -> object:
        for needle, expression in classifier.items():
            if needle in name:
                return SimpleNamespace(metadata=_FakeMeta(expression))
        raise _MODULE.metadata.PackageNotFoundError(name)

    return _distribution


def test_direct_copyleft_flags_lgpl_without_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # psycopg classified LGPL via the faked License-Expression; an empty
    # NOTICE must surface it as an attribution gap, independent of which
    # extras are installed in the running environment.
    import tomllib

    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"psycopg": "LGPL-3.0-only", "httpx": "MIT"}),
    )
    pyproject = tomllib.loads(_CLEAN_PYPROJECT)
    violations = _MODULE._check_direct_copyleft(pyproject, "")
    assert any("psycopg" in v.message for v in violations)


def test_direct_copyleft_clean_with_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tomllib

    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"psycopg": "LGPL-3.0-only", "httpx": "MIT"}),
    )
    pyproject = tomllib.loads(_CLEAN_PYPROJECT)
    notice = "psycopg psycopg-pool psycopg-binary"
    assert _MODULE._check_direct_copyleft(pyproject, notice) == []


def test_direct_copyleft_core_dep_unresolved_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # httpx is a CORE dependency; if it cannot be resolved the gate must
    # fail closed rather than silently skip classification.
    import tomllib

    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"psycopg": "LGPL-3.0-only"}),
    )
    pyproject = tomllib.loads(_CLEAN_PYPROJECT)
    violations = _MODULE._check_direct_copyleft(pyproject, "psycopg psycopg-pool")
    assert any(
        "core dependency" in v.message and "httpx" in v.message for v in violations
    )


def test_direct_copyleft_unsynced_extra_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An optional-extra dep (psycopg) absent from the venv is tolerated:
    # the deterministic _KNOWN_LGPL/NOTICE assertion and the uv.lock
    # denylist remain authoritative for extras.
    import tomllib

    monkeypatch.setattr(
        _MODULE.metadata,
        "distribution",
        _fake_distribution_factory({"httpx": "MIT"}),
    )
    pyproject = tomllib.loads(_CLEAN_PYPROJECT)
    assert _MODULE._check_direct_copyleft(pyproject, "") == []


# ── run_checks / main integration ───────────────────────────────


def _make_clean_repo(tmp_path: Path) -> Path:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\ndependencies = []\n')
    _write(tmp_path / "uv.lock", _CLEAN_LOCK)
    # The known-LGPL and elected-disjunction NOTICE assertions are both
    # unconditional, so a clean repo must attribute every dist they name even
    # though it declares no dependencies of its own.
    _write(
        tmp_path / "NOTICE",
        "SynthOrg NOTICE\npsycopg psycopg-pool psycopg-binary tld\n",
    )
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    return tmp_path


def test_run_checks_clean_repo_passes(tmp_path: Path) -> None:
    repo = _make_clean_repo(tmp_path)
    assert _MODULE.run_checks(repo) == []


def test_main_missing_notice_is_setup_error(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\ndependencies = []\n')
    _write(tmp_path / "uv.lock", _CLEAN_LOCK)
    _write(tmp_path / "cli" / "go.mod", "module x\n")
    # No NOTICE file -> setup error (exit code 2).
    assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2


def test_main_clean_repo_exit_zero(tmp_path: Path) -> None:
    repo = _make_clean_repo(tmp_path)
    assert _MODULE.main(["--repo-root", str(repo)]) == 0


def test_main_real_repo_passes() -> None:
    # The actual repository must satisfy the gate.
    assert _MODULE.main(["--repo-root", str(_REPO_ROOT)]) == 0
