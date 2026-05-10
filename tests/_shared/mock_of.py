"""``mock_of[T](**overrides)`` factory for typed-boundary substitutions.

Use this when a test needs to stand in for a concrete dependency at
a typed boundary (constructor argument, function parameter, fixture
return). The result is a ``unittest.mock.create_autospec`` instance
of ``T`` with ``spec_set=True``: missing methods raise
``AttributeError`` instead of returning yet another mock, so a
production rename surfaces immediately as a test failure.

Reach for ``FakeClock`` (``tests._shared.fake_clock``) for the Clock
seam, ``mock_of`` for other typed boundaries, ``SimpleNamespace`` for
attribute-bag scratch objects, and never bare ``MagicMock`` at a
typed boundary. See ``docs/reference/conventions.md`` section 12.1
for the full ladder.

Example:

    from tests._shared import mock_of

    repo = mock_of[AgentRepo](
        save=AsyncMock(spec=AgentRepo.save),
        get=AsyncMock(spec=AgentRepo.get, return_value=None),
    )
"""

from typing import Any, TypeVar
from unittest.mock import create_autospec

T = TypeVar("T")


class _MockOfFactory[T]:
    """Callable factory bound to a concrete spec type.

    Built by ``mock_of[T]`` via the metaclass ``__getitem__``; users
    do not instantiate this directly.
    """

    def __init__(self, spec: type[T]) -> None:
        self._spec = spec

    def __call__(self, **overrides: Any) -> T:
        """Return an autospec'd instance of ``T`` with overrides applied.

        Each override key must name an attribute that exists on the
        spec type. Unknown keys raise ``AttributeError`` naming the
        spec, so a typo at the call site fails loudly instead of
        silently absorbing the mistake into an attribute-bag mock.

        Args:
            **overrides: Attribute names mapped to replacement values
                (typically pre-configured ``Mock`` / ``AsyncMock``
                instances). Each name must exist on the spec type.

        Returns:
            An ``unittest.mock.create_autospec`` instance built with
            ``instance=True, spec_set=True``, typed as ``T`` for
            mypy strict mode.
        """
        instance = create_autospec(self._spec, instance=True, spec_set=True)
        for name, value in overrides.items():
            try:
                setattr(instance, name, value)
            except AttributeError as exc:
                msg = (
                    f"mock_of[{self._spec.__name__}]: cannot override "
                    f"{name!r}; attribute not present on spec type."
                )
                raise AttributeError(msg) from exc
        return instance  # type: ignore[no-any-return]


class _MockOfMeta(type):
    """Metaclass that makes ``mock_of[T]`` a typed factory expression.

    Implementing the subscript via a metaclass (not
    ``__class_getitem__``) keeps mypy strict happy: mypy treats
    ``mock_of[T]`` as ``_MockOfMeta.__getitem__(mock_of, T)``, which
    has a precise return-type annotation, instead of trying to
    interpret ``mock_of`` itself as a generic class.
    """

    def __getitem__(cls, spec: type[T]) -> _MockOfFactory[T]:
        return _MockOfFactory(spec)


class mock_of(metaclass=_MockOfMeta):  # noqa: N801 -- intentional lowercase factory name
    """Subscript-only factory: ``mock_of[ConcreteService](**overrides)``.

    Calling without the subscript (``mock_of(Service)``) raises
    ``TypeError`` because ``mock_of`` is a class with no
    constructor arguments. The generic-subscript form mirrors
    ``parse_typed[T]`` from ``synthorg.api.boundary`` so the
    typed-boundary metaphor is consistent across production and test
    code.
    """
