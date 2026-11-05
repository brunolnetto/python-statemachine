"""Internal helpers for hierarchy-aware state queries used by guard expressions.

State IDs are local names.  They are not unique in a hierarchical chart, so active
configuration checks in this module use the canonical ``State.value`` identity and
references are resolved through an exact hierarchical path or a unique local ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .exceptions import InvalidDefinition


@dataclass(frozen=True)
class StatePath:
    """Explicitly qualified state path used inside native ``In()`` expressions.

    The wrapper keeps path syntax distinct from legacy ``In("local-id")`` values,
    including local IDs that contain dots.
    """

    value: str


@dataclass(frozen=True)
class _StateIndex:
    by_value: Mapping[Any, Any]
    by_path: Mapping[str, Any]
    by_id: Mapping[str, tuple[Any, ...]]
    path_by_value: Mapping[Any, str]


def _escape_segment(segment: str) -> str:
    return segment.replace("\\", "\\\\").replace(".", "\\.")


def _format_path(segments: tuple[str, ...]) -> str:
    return ".".join(_escape_segment(segment) for segment in segments)


def _split_path(reference: str) -> tuple[str, ...]:
    """Split a dotted path. A literal dot/backslash in a segment must be escaped."""
    if not isinstance(reference, str) or not reference:
        raise InvalidDefinition("A state reference must be a non-empty string.")

    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in reference:
        if escaped:
            if char not in (".", "\\"):
                raise InvalidDefinition(
                    f"Invalid escape sequence \\{char} in state reference {reference!r}."
                )
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            if not current:
                raise InvalidDefinition(f"Empty path segment in state reference {reference!r}.")
            parts.append("".join(current))
            current = []
        else:
            current.append(char)

    if escaped:
        raise InvalidDefinition(f"Trailing escape in state reference {reference!r}.")
    if not current:
        raise InvalidDefinition(f"Empty path segment in state reference {reference!r}.")
    parts.append("".join(current))
    return tuple(parts)


def _state_segments(state: Any) -> tuple[str, ...]:
    segments: list[str] = []
    current = state
    while current is not None:
        segments.append(current.id)
        current = current.parent
    return tuple(reversed(segments))


@lru_cache(maxsize=256)
def _index(machine_cls: type) -> _StateIndex:
    """Index chart states by canonical value, qualified path, and local ID."""
    by_value: dict[Any, Any] = {}
    by_path: dict[str, Any] = {}
    by_id: dict[str, list[Any]] = {}
    path_by_value: dict[Any, str] = {}

    for state in machine_cls.states_map.values():
        value = state.value
        path = _format_path(_state_segments(state))
        if value in by_value and by_value[value] is not state:
            raise InvalidDefinition(f"Duplicate canonical State.value {value!r}.")
        if path in by_path and by_path[path] is not state:
            raise InvalidDefinition(f"Duplicate hierarchical state path {path!r}.")
        by_value[value] = state
        by_path[path] = state
        path_by_value[value] = path
        by_id.setdefault(state.id, []).append(state)

    return _StateIndex(
        by_value=by_value,
        by_path=by_path,
        by_id={key: tuple(states) for key, states in by_id.items()},
        path_by_value=path_by_value,
    )


def resolve_state(machine: Any, reference: str) -> Any:
    """Resolve an unqualified unique ID or an exact qualified path to one State.

    A bare ID is accepted only when it is unique in the chart. Qualified paths are
    exact and use escaped dotted segments.
    """
    segments = _split_path(reference)
    index = _index(type(machine))

    if len(segments) == 1:
        matches = index.by_id.get(segments[0], ())
        if not matches:
            raise InvalidDefinition(
                f"Unknown state reference {reference!r}. Known paths: "
                f"{', '.join(sorted(index.by_path))}."
            )
        if len(matches) > 1:
            paths = sorted(index.path_by_value[state.value] for state in matches)
            raise InvalidDefinition(
                f"Ambiguous state ID {reference!r}; use one of: {', '.join(paths)}."
            )
        return matches[0]

    path = _format_path(segments)
    try:
        return index.by_path[path]
    except KeyError:
        raise InvalidDefinition(
            f"Unknown state path {reference!r}. Known paths: {', '.join(sorted(index.by_path))}."
        ) from None


def resolve_qualified_state(machine: Any, reference: str) -> Any:
    """Resolve an exact path, without falling back to a local-ID lookup."""
    segments = _split_path(reference)
    path = _format_path(segments)
    try:
        return _index(type(machine)).by_path[path]
    except KeyError:
        raise InvalidDefinition(f"Unknown qualified state path {reference!r}.") from None


def make_state_path(reference: str) -> StatePath:
    """Create the explicit path marker accepted by native ``In()``."""
    _split_path(reference)  # validate syntax now, not while the guard is executing
    return StatePath(reference)


def is_state_active(machine: Any, reference: str | StatePath) -> bool:
    """Test exact path membership or legacy local-ID membership.

    Bare strings preserve native ``In()`` semantics: any active state with that local
    ID satisfies the membership query. ``path("...")`` selects exactly one node.
    """
    configuration_values = getattr(machine, "configuration_values", None)

    if configuration_values is None:
        # The restricted SCXML evaluator also uses In(), with a lightweight machine
        # exposing only configuration and each state's local ID.
        if isinstance(reference, StatePath):
            raise InvalidDefinition("Qualified state paths require a StateChart machine.")
        if not isinstance(reference, str):
            return False
        return any(state.id == reference for state in machine.configuration)

    active_values = set(configuration_values)
    index = _index(type(machine))

    if isinstance(reference, StatePath):
        state = resolve_qualified_state(machine, reference.value)
        return state.value in active_values

    if not isinstance(reference, str):
        # Before path references existed, non-string constants simply did not match
        # any State.id. Preserve that behavior for native In().
        return False

    # Legacy local-ID semantics: unknown names are simply not members, and repeated
    # local IDs match when any corresponding state is active.
    return any(state.value in active_values for state in index.by_id.get(reference, ()))


def is_in_final_state(state: Any, active_values: Any) -> bool:
    """The single structural-completion rule shared by guards and the engine.

    Parallel states complete when all direct regions recursively complete. Compound
    states complete when a direct final child is active. Atomic states never complete.
    """
    active = active_values if isinstance(active_values, set) else set(active_values)
    return _is_in_final_state(state, active)


def _is_in_final_state(state: Any, active_values: set[Any]) -> bool:
    if state.parallel:
        return all(_is_in_final_state(region, active_values) for region in state.states)
    return any(child.final and child.value in active_values for child in state.states)


def active_child(state: Any, active_values: Any) -> str | None:
    """Return the active direct child of a compound state, or ``None`` if inactive."""
    if not state.states or state.parallel:
        raise InvalidDefinition(
            f"child() requires a compound state; {state.id!r} is "
            f"{'parallel' if state.parallel else 'atomic'}."
        )

    active = active_values if isinstance(active_values, set) else set(active_values)
    children = [child for child in state.states if child.value in active]
    if len(children) > 1:
        raise InvalidDefinition(
            f"Invalid configuration: compound state {state.id!r} has multiple active "
            "direct children."
        )
    return children[0].id if children else None


def build_is_done_call(*references: Any):
    if len(references) != 1 or not isinstance(references[0], str):
        raise InvalidDefinition("is_done() expects exactly one string state reference.")
    reference = references[0]

    def guard(*args, **kwargs):
        machine = kwargs["machine"]
        state = resolve_state(machine, reference)
        return is_in_final_state(state, machine.configuration_values)

    guard.__name__ = f"is_done({reference!r})"
    guard.unique_key = guard.__name__  # type: ignore[attr-defined]
    return guard


def build_child_call(*references: Any):
    if len(references) != 1 or not isinstance(references[0], str):
        raise InvalidDefinition("child() expects exactly one string state reference.")
    reference = references[0]

    def query(*args, **kwargs):
        machine = kwargs["machine"]
        state = resolve_state(machine, reference)
        return active_child(state, machine.configuration_values)

    query.__name__ = f"child({reference!r})"
    query.unique_key = query.__name__  # type: ignore[attr-defined]
    return query
