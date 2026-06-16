"""Unit tests for scripts/check_dead_api_endpoints.py.

Exercises the dead-API gate end-to-end: backend AST walker, frontend
TS scanner, comparator, baseline I/O, and the seven verification
scenarios that motivate the gate (frontend-only call, backend orphan,
matched pair, conditionally-registered controller, websocket call,
router-prefix match, path-param normalisation).

The script is loaded via :func:`importlib.util.spec_from_file_location`
rather than a subprocess so private helpers (``_scan_file``,
``compare``, ...) are callable directly. All tests build a synthetic
project tree under ``tmp_path`` so the real repository state stays
out of the assertions.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_dead_api_endpoints.py"


def _load_script_module() -> object:
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_dead_api_endpoints",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


# ── Synthetic-project helpers ─────────────────────────────────


def _make_fake_repo(  # noqa: PLR0913 -- declarative fixture builder
    tmp_path: Path,
    *,
    controllers: dict[str, str] | None = None,
    init_body: str | None = None,
    app_body: str | None = None,
    ws_body: str | None = None,
    ts_files: dict[str, str] | None = None,
) -> Path:
    """Lay out a minimal SynthOrg-shaped tree under *tmp_path*.

    Args:
        controllers: ``{module_name: file_text}`` for files placed
            under ``src/synthorg/api/controllers/``. The module name
            is the bare stem (no extension).
        init_body: Verbatim text for ``controllers/__init__.py``;
            defaults to a minimal empty-tuple shape that registers
            no controllers.
        app_body: Verbatim text for ``api/app.py``; defaults to a
            minimal stub with no A2A registrations.
        ws_body: Verbatim text for ``controllers/ws.py``; defaults
            to a stub without any ``@websocket(...)`` handler.
        ts_files: ``{relative_path: file_text}`` for files placed
            under ``web/src/`` (relative paths use ``/`` separators).

    Returns the resolved project root.
    """
    src = tmp_path / "src" / "synthorg" / "api"
    controllers_dir = src / "controllers"
    controllers_dir.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")

    for name, body in (controllers or {}).items():
        (controllers_dir / f"{name}.py").write_text(body, encoding="utf-8")

    (controllers_dir / "__init__.py").write_text(
        init_body if init_body is not None else _DEFAULT_INIT_BODY,
        encoding="utf-8",
    )
    (src / "app.py").write_text(
        app_body if app_body is not None else _DEFAULT_APP_BODY,
        encoding="utf-8",
    )
    (controllers_dir / "ws.py").write_text(
        ws_body if ws_body is not None else _DEFAULT_WS_BODY,
        encoding="utf-8",
    )

    if ts_files:
        web_src = tmp_path / "web" / "src"
        for rel, body in ts_files.items():
            target = web_src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

    return tmp_path


_DEFAULT_INIT_BODY = (
    "from litestar import Controller\n"
    "BASE_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
    "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
    "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
)
_DEFAULT_APP_BODY = "# stub app.py\n"
_DEFAULT_WS_BODY = "# stub ws.py with no handler\n"


# ── 1. frontend-only call flagged HIGH ────────────────────────


def test_frontend_only_call_flagged_high(tmp_path: Path) -> None:
    """A frontend call with no matching backend route raises a HIGH violation."""
    repo = _make_fake_repo(
        tmp_path,
        ts_files={
            "api/endpoints/foo.ts": (
                "import { apiClient } from '../client'\n"
                "export async function getFoo() {\n"
                "  return apiClient.get('/this-does-not-exist')\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, info = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert len(high) == 1
    assert high[0].method == "GET"
    assert high[0].path == "/this-does-not-exist"
    assert high[0].severity == "high"
    assert info == []


# ── 2. backend-only endpoint informational ────────────────────


def test_backend_only_endpoint_informational(tmp_path: Path) -> None:
    """A backend route with no frontend caller is INFO-only, never blocks."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get()\n"
                "    async def list_agents(self): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, info = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []
    assert len(info) == 1
    assert info[0].severity == "info"
    assert info[0].path == "/agents"


# ── 3. matched pair passes ────────────────────────────────────


def test_matched_pair_passes(tmp_path: Path) -> None:
    """Backend route + matching frontend call -> zero violations."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get()\n"
                "    async def list_agents(self): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/agents.ts": (
                "import { apiClient } from '../client'\n"
                "export async function listAgents() {\n"
                "  return apiClient.get('/agents')\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, info = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []
    assert info == []


# ── 4. conditionally-registered controller recognised ────────


def test_conditionally_registered_controller_recognised(tmp_path: Path) -> None:
    """OPTIONAL_CONTROLLERS entries are treated as registered routes."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "simulations": (
                "from litestar import Controller, get\n"
                "class SimulationController(Controller):\n"
                "    path = '/simulations'\n"
                "    @get()\n"
                "    async def list_sims(self): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.simulations import SimulationController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = (\n"
            "    (SimulationController, 'has_client_simulation_state'),\n"
            ")\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/sims.ts": (
                "import { apiClient } from '../client'\n"
                "export async function listSims() {\n"
                "  return apiClient.get('/simulations')\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []


# ── 5. websocket handler recognised ──────────────────────────


def test_websocket_call_recognised(tmp_path: Path) -> None:
    """Backend ``@websocket('/ws')`` matches a frontend ``new WebSocket(...)`` URL."""
    repo = _make_fake_repo(
        tmp_path,
        ws_body=(
            "from litestar.handlers import websocket\n"
            "@websocket('/ws')\n"
            "async def ws_handler(socket): ...\n"
        ),
        ts_files={
            "stores/websocket.ts": (
                "function getWsUrl() {\n"
                "  const protocol = 'wss:'\n"
                "  const host = 'localhost'\n"
                "  return `${protocol}//${host}/api/v1/ws`\n"
                "}\n"
                "export function connect() {\n"
                "  return new WebSocket(getWsUrl())\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    ws_routes = [r for r in routes if r.method == "WS"]
    assert len(ws_routes) == 1
    assert ws_routes[0].path == "/ws"
    # The frontend ``new WebSocket(...)`` shape is intentionally not
    # matched by the call-site scanner (the codebase has only one
    # WS call site and it lives behind ``getWsUrl()``); the scanner
    # tracks ``apiClient.METHOD`` and ``fetch(...)`` only. This test
    # asserts the BACKEND walker recognises the ``@websocket(...)``
    # decorator -- the frontend side is covered by the matched-pair
    # gate via the ``// lint-allow:`` opt-out for the single call.


# ── 6. router-prefix match ───────────────────────────────────


def test_router_prefix_match(tmp_path: Path) -> None:
    """Backend ``/api/v1`` prefix is stripped before comparing to frontend URLs."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get()\n"
                "    async def list_agents(self): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/agents.ts": (
                "import { apiClient } from '../client'\n"
                "export async function listAgents() {\n"
                "  return apiClient.get('/agents')\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo, api_prefix="/api/v1")  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    # Backend should be ``/agents`` after prefix strip; frontend is
    # also ``/agents``; comparator emits zero high violations.
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []
    assert any(r.path == "/agents" for r in routes)


# ── 7. path-parameter normalisation ──────────────────────────


def test_path_param_normalisation(tmp_path: Path) -> None:
    """Backend ``{name:str}`` and frontend ``${var}`` collapse to ``{*}``."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get('/{agent_name:str}')\n"
                "    async def get_agent(self, agent_name): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/agents.ts": (
                "import { apiClient } from '../client'\n"
                "export async function getAgent(name: string) {\n"
                "  return apiClient.get(`/agents/${encodeURIComponent(name)}`)\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []
    # Both sides should have collapsed the placeholder.
    assert any(r.path == "/agents/{*}" for r in routes)
    assert any(c.path == "/agents/{*}" for c in calls)


# ── 8. BASE-constant resolution ─────────────────────────────


def test_base_constant_resolution(tmp_path: Path) -> None:
    """Frontend ``${BASE}`` const prefix resolves to its literal value."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "escalations": (
                "from litestar import Controller, get\n"
                "class EscalationsController(Controller):\n"
                "    path = '/conflicts/escalations'\n"
                "    @get('/{id:str}')\n"
                "    async def get_escalation(self, id): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.escalations import "
            "EscalationsController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = "
            "(EscalationsController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/escalations.ts": (
                "import { apiClient } from '../client'\n"
                "const BASE = '/conflicts/escalations'\n"
                "export async function getEscalation(id: string) {\n"
                "  return apiClient.get(`${BASE}/${encodeURIComponent(id)}`)\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []
    assert any(c.path == "/conflicts/escalations/{*}" for c in calls)


# ── 9. lint-allow suppression ────────────────────────────────


def test_lint_allow_suppresses(tmp_path: Path) -> None:
    """A trailing ``// lint-allow:`` marker hides a frontend-only call."""
    repo = _make_fake_repo(
        tmp_path,
        ts_files={
            "api/endpoints/foo.ts": (
                "import { apiClient } from '../client'\n"
                "export async function ext() {\n"
                "  return apiClient.get('/external/api')  "
                "// lint-allow: dead-api-endpoints -- third-party REST\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []
    # The call site was scanned; suppression is tracked on the record.
    assert any(c.has_suppression for c in calls)


def test_lint_allow_requires_justification(tmp_path: Path) -> None:
    """A bare marker with no ``-- <reason>`` does NOT suppress."""
    repo = _make_fake_repo(
        tmp_path,
        ts_files={
            "api/endpoints/foo.ts": (
                "import { apiClient } from '../client'\n"
                "export async function ext() {\n"
                "  return apiClient.get('/external/api')"
                "  // lint-allow: dead-api-endpoints\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert len(high) == 1


# ── 10. baseline shrinkage only ─────────────────────────────


def test_baseline_shrinkage_only(tmp_path: Path) -> None:
    """A baselined violation passes; a NEW one fails; stale entries warn."""
    repo = _make_fake_repo(
        tmp_path,
        ts_files={
            "api/endpoints/foo.ts": (
                "import { apiClient } from '../client'\n"
                "export async function ext() {\n"
                "  return apiClient.get('/baselined-dead-route')\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert len(high) == 1

    # Write a baseline that already contains this finding + one stale
    # entry that does NOT correspond to any current violation.
    baseline_path = tmp_path / "scripts" / "baseline.txt"
    baseline_path.parent.mkdir(parents=True)
    _MODULE.write_baseline(  # type: ignore[attr-defined]
        [
            *high,
            # Manufactured stale entry: not in current run.
            type(high[0])(
                severity="high",
                method="GET",
                path="/old-fixed-route",
                source_file="web/src/api/endpoints/legacy.ts",
                source_line=1,
                source_col=0,
                reason="(stale baseline entry)",
            ),
        ],
        baseline_path,
    )
    unbaselined, stale = _MODULE.filter_against_baseline(high, baseline_path)  # type: ignore[attr-defined]
    assert unbaselined == []
    assert any("/old-fixed-route" in entry for entry in stale)


def test_baseline_new_violation_fails(tmp_path: Path) -> None:
    """A finding NOT in the baseline produces an unbaselined entry."""
    baseline_path = tmp_path / "scripts" / "baseline.txt"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(
        "web/src/old.ts:1:0:GET:/old-route\n",
        encoding="utf-8",
    )
    repo = _make_fake_repo(
        tmp_path,
        ts_files={
            "api/endpoints/foo.ts": (
                "import { apiClient } from '../client'\n"
                "export async function ext() {\n"
                "  return apiClient.get('/brand-new-dead-route')\n"
                "}\n"
            ),
        },
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    unbaselined, _stale = _MODULE.filter_against_baseline(high, baseline_path)  # type: ignore[attr-defined]
    assert len(unbaselined) == 1
    assert unbaselined[0].path == "/brand-new-dead-route"


# ── 11. update-baseline regenerates ─────────────────────────


def test_update_baseline_regenerates(tmp_path: Path) -> None:
    """``write_baseline`` round-trips the violations + header correctly."""
    baseline_path = tmp_path / "baseline.txt"
    violation_cls = _MODULE.Violation  # type: ignore[attr-defined]
    violations = [
        violation_cls(
            severity="high",
            method="GET",
            path="/x",
            source_file="web/src/x.ts",
            source_line=1,
            source_col=0,
            reason="r",
        ),
    ]
    _MODULE.write_baseline(violations, baseline_path)  # type: ignore[attr-defined]
    text = baseline_path.read_text(encoding="utf-8")
    assert "Frozen baseline" in text  # header present
    assert "web/src/x.ts:1:0:GET:/x" in text
    # Re-loading must produce the same key.
    keys = _MODULE.load_baseline(baseline_path)  # type: ignore[attr-defined]
    assert keys == {"web/src/x.ts:1:0:GET:/x"}


# ── path-param normalisation utilities ──────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/agents", "/agents"),
        ("/agents/{name:str}", "/agents/{*}"),
        ("/agents/{agent_id:str}/health", "/agents/{*}/health"),
        ("/x/{a:str}/y/{b:int}", "/x/{*}/y/{*}"),
        ("/x/{a}/y/{b}", "/x/{*}/y/{*}"),
        ("/x/", "/x"),
        ("/", "/"),
    ],
)
def test_normalise_path(raw: str, expected: str) -> None:
    """``normalise_path`` collapses every Litestar / template placeholder."""
    from scripts._dead_api_endpoints_models import normalise_path

    assert normalise_path(raw) == expected


def test_find_template_end_handles_escaped_backtick() -> None:
    """Backslash-escaped backticks inside a nested template literal in
    a ``${...}`` substitution don't prematurely exit the in_backtick
    state. Without escape handling, ``\\\\``` would unset the in_backtick
    flag, then the next ``}`` could be mistaken for the substitution's
    closing brace.
    """
    from scripts._dead_api_endpoints_frontend import _find_template_end

    # ``${`a\`b` + 1}`` -- a nested template literal containing one
    # escaped backtick, followed by ``+ 1`` and the closing ``}``.
    body = "`a\\`b` + 1}"
    end = _find_template_end(body, 0)
    assert body[end] == "}"
    assert end == len(body) - 1


# ── regression: silent-failure surfacing ────────────────────


def test_malformed_controller_init_raises(tmp_path: Path) -> None:
    """A SyntaxError in controllers/__init__.py raises rather than silently
    returning zero routes (which would inflate every frontend call to a
    HIGH violation)."""
    repo = _make_fake_repo(tmp_path)
    # Overwrite with malformed Python.
    init_path = repo / "src" / "synthorg" / "api" / "controllers" / "__init__.py"
    init_path.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read controller registration"):
        _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]


def test_malformed_controller_module_skipped_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A SyntaxError in a single controller module emits a stderr warning
    and skips that module without aborting the run."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get()\n"
                "    async def list_agents(self): ...\n"
            ),
            "broken": "def malformed(:\n",
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "from synthorg.api.controllers.broken import BrokenController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (\n"
            "    AgentController, BrokenController,\n"
            ")\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    captured = capsys.readouterr()
    # The valid AgentController route is still collected.
    assert any(r.path == "/agents" for r in routes)
    # The malformed file produces a stderr warning.
    assert "cannot parse" in captured.err
    assert "broken.py" in captured.err


def test_missing_controller_import_handled(tmp_path: Path) -> None:
    """A controller class referenced in a registration tuple but never
    imported is skipped silently (no class to walk; no routes added)."""
    repo = _make_fake_repo(
        tmp_path,
        init_body=(
            "from litestar import Controller\n"
            "# AgentController is referenced but never imported -- typo case\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    # No imported controllers means zero routes; the tuple-walker silently
    # skips names with no import-map entry.
    assert routes == []


# ── regression: baseline malformed entries ──────────────────


@pytest.mark.parametrize(
    "body",
    [
        # Empty col field.
        "web/src/x.ts::0:GET:/x\n",
        # Missing path field.
        "web/src/x.ts:1:0:GET\n",
    ],
)
def test_baseline_malformed_entries_raise(tmp_path: Path, body: str) -> None:
    """Malformed baseline lines raise ValueError loudly rather than silently
    dropping suppressions."""
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="malformed baseline entry"):
        _MODULE.load_baseline(baseline_path)  # type: ignore[attr-defined]


def test_empty_baseline_loads_as_empty_set(tmp_path: Path) -> None:
    """A baseline with only comments / blank lines loads as an empty set."""
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(
        "# comment\n\n# another comment\n",
        encoding="utf-8",
    )
    keys = _MODULE.load_baseline(baseline_path)  # type: ignore[attr-defined]
    assert keys == set()


# ── regression: frontend scanner coverage gaps ──────────────


def test_frontend_nested_generics(tmp_path: Path) -> None:
    """``apiClient.get<PaginatedResponse<AgentConfig>>('/agents')`` extracts
    the URL despite nested ``<>`` levels."""
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get()\n"
                "    async def list_agents(self): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/agents.ts": (
                "import { apiClient } from '../client'\n"
                "export async function listAgents() {\n"
                "  return apiClient.get<PaginatedResponse<AgentConfig>>('/agents')\n"
                "}\n"
            ),
        },
    )
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    assert any(c.method == "GET" and c.path == "/agents" for c in calls)
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []


def test_frontend_multi_segment_base_token(tmp_path: Path) -> None:
    """Multi-segment Axios-base tokens followed by a method chain
    (``${apiClient.defaults.baseURL.replace(...)}``,
    ``${import.meta.env.VITE_API_BASE_URL.replace(...)}``) resolve to
    an empty base prefix instead of falling through to ``{*}``.
    """
    repo = _make_fake_repo(
        tmp_path,
        controllers={
            "agents": (
                "from litestar import Controller, get\n"
                "class AgentController(Controller):\n"
                "    path = '/agents'\n"
                "    @get()\n"
                "    async def list_agents(self): ...\n"
            ),
        },
        init_body=(
            "from litestar import Controller\n"
            "from synthorg.api.controllers.agents import AgentController\n"
            "BASE_CONTROLLERS: tuple[type[Controller], ...] = (AgentController,)\n"
            "OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = ()\n"
            "INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = ()\n"
        ),
        ts_files={
            "api/endpoints/agents_chain.ts": (
                "import { apiClient } from '../client'\n"
                "export async function listAgentsViaDefaults() {\n"
                "  return apiClient.get("
                "`${apiClient.defaults.baseURL.replace("
                "/\\\\/api\\\\/v1$/, '')}/agents`)\n"
                "}\n"
            ),
            "api/endpoints/agents_env.ts": (
                "import { apiClient } from '../client'\n"
                "export async function listAgentsViaEnv() {\n"
                "  return apiClient.get("
                "`${import.meta.env.VITE_API_BASE_URL.replace("
                "/\\\\/api\\\\/v1$/, '')}/agents`)\n"
                "}\n"
            ),
        },
    )
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    paths = [c.path for c in calls]
    assert paths.count("/agents") == 2, paths
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    high, _ = _MODULE.compare(routes, calls)  # type: ignore[attr-defined]
    assert high == []


def test_a2a_well_known_root_mount(tmp_path: Path) -> None:
    """``WellKnownAgentCardController`` is discovered via its feature manifest
    and mounted at the app root, not under the ``/api/v1`` prefix; the
    ``/.well-known/...`` path stays verbatim."""
    repo = _make_fake_repo(tmp_path)
    # Lay out the synthetic A2A module plus its feature manifest. The backend
    # walker discovers root-mounted controllers via the manifest's
    # ``ControllerRegistration(..., mount="root")`` call, not the app.py imports.
    a2a_dir = repo / "src" / "synthorg" / "a2a"
    a2a_dir.mkdir(parents=True)
    (a2a_dir / "__init__.py").write_text("", encoding="utf-8")
    (a2a_dir / "well_known.py").write_text(
        "from litestar import Controller, get\n"
        "class WellKnownAgentCardController(Controller):\n"
        "    path = '/.well-known'\n"
        "    @get('/agent-card.json')\n"
        "    async def get_card(self): ...\n",
        encoding="utf-8",
    )
    (a2a_dir / "feature.py").write_text(
        "from synthorg._core.features import ControllerRegistration\n"
        "from synthorg.a2a.well_known import WellKnownAgentCardController\n"
        "FEATURE = (\n"
        "    ControllerRegistration(\n"
        "        controller=WellKnownAgentCardController, mount='root'\n"
        "    ),\n"
        ")\n",
        encoding="utf-8",
    )
    routes = _MODULE.collect_backend_routes(repo)  # type: ignore[attr-defined]
    well_known = [r for r in routes if "well-known" in r.path]
    assert any(r.path == "/.well-known/agent-card.json" for r in well_known)


def test_suppression_does_not_leak_to_other_calls(tmp_path: Path) -> None:
    """A ``// lint-allow:`` marker on one call site does not flag adjacent
    calls as suppressed."""
    repo = _make_fake_repo(
        tmp_path,
        ts_files={
            "api/endpoints/foo.ts": (
                "import { apiClient } from '../client'\n"
                "export async function f() {\n"
                "  apiClient.get('/call-one')\n"
                "  apiClient.get('/call-two')"
                "  // lint-allow: dead-api-endpoints -- intentional\n"
                "  apiClient.get('/call-three')\n"
                "}\n"
            ),
        },
    )
    calls = _MODULE.collect_frontend_call_sites(repo)  # type: ignore[attr-defined]
    by_path = {c.path: c for c in calls}
    assert by_path["/call-one"].has_suppression is False
    assert by_path["/call-two"].has_suppression is True
    assert by_path["/call-three"].has_suppression is False
