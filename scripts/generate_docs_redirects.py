"""Generate meta-refresh redirect stubs for the docs site.

The docs site is built with ``zensical build``, which does not run the
``mkdocs-redirects`` plugin lifecycle hook. As a result the ``redirect_maps``
declared in ``mkdocs.yml`` never materialise as pages, and the old URLs return
HTTP 404 on the deployed site (breaking inbound links from issues, search
engines, and blog posts).

This script reads ``redirect_maps`` from ``mkdocs.yml`` (the single source of
truth) and writes a ``<meta http-equiv="refresh">`` + ``<link rel="canonical">``
stub at each old path inside the already-built ``site_dir`` so the old URLs
resolve to their current location. It runs in the Pages workflow after
``zensical build``.
"""

import html
import sys
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_MKDOCS_CONFIG: Final[Path] = _REPO_ROOT / "mkdocs.yml"

# Schemes a redirect target may carry. Anything else (notably ``javascript:``)
# is rejected before interpolation into the HTML stub.
_ALLOWED_TARGET_SCHEMES: Final[frozenset[str]] = frozenset({"", "http", "https"})

_STUB_TEMPLATE: Final[str] = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url={target}">
<link rel="canonical" href="{target}">
<title>Redirecting</title>
</head>
<body>
<p>This page has moved to <a href="{target}">{target}</a>.</p>
</body>
</html>
"""


class _IgnoreUnknownTags(yaml.SafeLoader):
    """SafeLoader that tolerates mkdocs' custom config tags.

    ``mkdocs.yml`` embeds custom tags (``!ENV``, ``!!python/name:`` in
    ``markdown_extensions``) that ``yaml.safe_load`` rejects. None of those
    values are needed here, so every unknown tag resolves to ``None`` and the
    plain mapping (``plugins`` / ``site_dir`` / ``site_url``) parses normally.
    """


_IgnoreUnknownTags.add_multi_constructor(  # type: ignore[no-untyped-call]  # PyYAML classmethod is untyped in stubs
    "",
    lambda _loader, _tag_suffix, _node: None,
)


def _load_config() -> dict[str, object]:
    """Parse ``mkdocs.yml`` into a plain dict.

    Raises:
        OSError: ``mkdocs.yml`` is missing or unreadable.
        yaml.YAMLError: the file is not valid YAML.
        TypeError: the document does not parse to a mapping.
    """
    text = _MKDOCS_CONFIG.read_text(encoding="utf-8")
    # _IgnoreUnknownTags subclasses yaml.SafeLoader and maps every unknown tag
    # to None, so no arbitrary object can be instantiated; this is as safe as
    # safe_load while tolerating mkdocs' !ENV / !!python/name: config tags.
    loaded = yaml.load(text, Loader=_IgnoreUnknownTags)  # noqa: S506 -- SafeLoader subclass
    if not isinstance(loaded, dict):
        msg = f"mkdocs.yml did not parse to a mapping (got {type(loaded)!r})"
        raise TypeError(msg)
    return loaded


def _redirect_maps(config: dict[str, object]) -> dict[str, str]:
    """Extract the ``redirects`` plugin's ``redirect_maps`` mapping."""
    plugins = config.get("plugins", [])
    if not isinstance(plugins, list):
        return {}
    for plugin in plugins:
        if isinstance(plugin, dict) and "redirects" in plugin:
            redirects = plugin["redirects"]
            if isinstance(redirects, dict):
                maps = redirects.get("redirect_maps", {})
                if isinstance(maps, dict):
                    return {str(k): str(v) for k, v in maps.items()}
    return {}


def _url_path_prefix(config: dict[str, object]) -> str:
    """Return the site_url path prefix (e.g. ``/docs``), without a trailing slash."""
    site_url = config.get("site_url")
    if not isinstance(site_url, str) or not site_url:
        return ""
    return urlsplit(site_url).path.rstrip("/")


def _doc_to_url(doc_path: str, prefix: str) -> str:
    """Map an mkdocs source path (``a/b.md``) to its directory URL (``/prefix/a/b/``)."""
    slug = doc_path.removesuffix(".md")
    if slug == "index":
        return f"{prefix}/"
    slug = slug.removesuffix("/index")
    return f"{prefix}/{slug}/"


def _doc_to_stub_dir(site_dir: Path, doc_path: str) -> Path:
    """Map an old mkdocs source path to the directory its stub index.html lives in.

    Raises:
        ValueError: the redirect key escapes ``site_dir`` (a ``../`` traversal),
            which would write the stub outside the built site tree.
    """
    slug = doc_path.removesuffix(".md")
    slug = slug.removesuffix("/index")
    stub_dir = (site_dir / slug).resolve()
    # A malformed redirect_maps key (e.g. ``../../escape.md``) must not write
    # outside the built site; relative_to raises ValueError when it escapes.
    stub_dir.relative_to(site_dir.resolve())
    return stub_dir


def _safe_target(target: str) -> str:
    """Validate a redirect target's scheme and HTML-escape it for the stub.

    Raises:
        ValueError: the target carries a disallowed URL scheme (e.g.
            ``javascript:``), which must never reach the meta-refresh stub.
    """
    scheme = urlsplit(target).scheme
    if scheme not in _ALLOWED_TARGET_SCHEMES:
        msg = f"redirect target has disallowed scheme: {target!r}"
        raise ValueError(msg)
    return html.escape(target, quote=True)


def main() -> int:
    """Write a redirect stub for every ``redirect_maps`` entry into ``site_dir``."""
    try:
        config = _load_config()
    except (OSError, TypeError) as exc:
        print(
            f"error: could not load mkdocs.yml: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    except yaml.YAMLError as exc:
        print(f"error: mkdocs.yml is not valid YAML: {exc}", file=sys.stderr)
        return 1

    maps = _redirect_maps(config)
    if not maps:
        print("no redirect_maps in mkdocs.yml; nothing to do")
        return 0

    site_dir_value = config.get("site_dir", "_site/docs")
    site_dir = _REPO_ROOT / str(site_dir_value)
    if not site_dir.is_dir():
        print(
            f"error: site_dir {site_dir} does not exist (run the docs build first)",
            file=sys.stderr,
        )
        return 1

    prefix = _url_path_prefix(config)
    written = 0
    for old_path, new_path in maps.items():
        try:
            target = _safe_target(_doc_to_url(new_path, prefix))
            stub_dir = _doc_to_stub_dir(site_dir, old_path)
            stub_dir.mkdir(parents=True, exist_ok=True)
            (stub_dir / "index.html").write_text(
                _STUB_TEMPLATE.format(target=target), encoding="utf-8"
            )
        except (OSError, ValueError) as exc:
            print(
                f"error: could not write redirect stub for {old_path!r}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        written += 1
        print(f"wrote redirect stub: {old_path} -> {target}")

    print(f"generated {written} redirect stub(s) under {site_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
