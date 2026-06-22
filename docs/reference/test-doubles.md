---
title: Test-Double Ladder
description: Pick the narrowest test double that expresses a collaborator's contract, from Protocol fakes to typed mocks.
---

# Test-Double Ladder

When a test needs to stand in for a real collaborator, prefer the narrowest tool that still expresses the contract. This page is the canonical reference for the ladder; the broader testing conventions live in [Code Conventions](conventions.md).

## The ladder

Top to bottom:

1. **Protocol fake**: a hand-written class that satisfies a Protocol structurally, with deterministic state. Canonical example: `tests/_shared/fake_clock.py` (`FakeClock` satisfies `synthorg.core.clock.Clock`). Use this when the seam has more than one method, the test asserts on observed effects (sleeps recorded, time advanced), or virtual-time semantics matter.
2. **`create_autospec` / `mock_of[T]`**: a typed mock built from the real class. Use `mock_of[T](**overrides)` from `tests._shared` for the common case (autospec with `instance=True, spec_set=True`, plus optional kwarg-overrides); reach for raw `create_autospec(T, instance=True, spec_set=True)` when the call site needs the lower-level API. Missing methods raise `AttributeError`; renames in production fail tests immediately.
3. **`SimpleNamespace`**: a plain attribute bag for scratch data that never crosses a typed boundary. Use when the test only needs `obj.x = 1; obj.y = 2` semantics and does not care about method behaviour.
4. **Bare `MagicMock` (forbidden at a typed boundary)**: a `MagicMock()` with no `spec=` absorbs any attribute access. The `scripts/check_mock_spec.py` gate blocks substituting a bare mock for a typed parameter, fixture return, or annotated local. Bare mocks remain syntactically allowed for `.return_value =` chains and attribute-bag scratch (rungs 3 and below); the gate does not scan those.

## Picking a rung

| Need | Use |
| ---- | --- |
| Wall-clock / monotonic / sleep | `FakeClock` |
| Concrete service / repo at a constructor or fn argument | `mock_of[T](**overrides)` |
| Other Protocol with hand-rolled state | new Protocol fake under `tests/_shared/` |
| Throwaway namespace for `obj.x = 1` style | `types.SimpleNamespace(x=1, y=2)` |
| Inner mock for `parent.method.return_value = ...` chain | bare `MagicMock()` (not a typed boundary) |

The gate in `scripts/check_mock_spec.py` runs in zero-tolerance mode (no baseline file). A new bare `Mock()` substituted for a typed parameter fails pre-commit; the fix is one of the three upper rungs.
