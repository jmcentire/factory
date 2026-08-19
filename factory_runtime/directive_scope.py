"""One closed directive-scope grammar shared by runtime readers and supported writers."""

from __future__ import annotations

import re

DIRECTIVE_ROLES = frozenset({"coder", "tester", "validator", "orchestrator"})
SCOPE_KEY_ORDER = ("run", "generation", "role")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_GENERATION = re.compile(r"^[1-9][0-9]*$")
_MAX_SCOPE_BYTES = 16_384


class DirectiveScopeError(ValueError):
    """A directive scope is not in the one canonical grammar."""


def parse_directive_scope(scope: object) -> tuple[tuple[str, str], ...]:
    """Return canonical selectors, rejecting unknown, reordered, or malformed scope."""

    if (
        not isinstance(scope, str)
        or not scope.strip()
        or len(scope.encode("utf-8")) > _MAX_SCOPE_BYTES
    ):
        raise DirectiveScopeError("directive scope must be bounded non-empty text")
    if scope in {"global", "run"}:
        return ()
    selectors: dict[str, str] = {}
    parts = scope.split(";")
    for part in parts:
        key, separator, selected = part.partition("=")
        if not separator or not selected or key in selectors or key not in SCOPE_KEY_ORDER:
            raise DirectiveScopeError(f"unknown directive scope: {scope!r}")
        selectors[key] = selected
    canonical_keys = [key for key in SCOPE_KEY_ORDER if key in selectors]
    if [part.partition("=")[0] for part in parts] != canonical_keys:
        raise DirectiveScopeError(f"noncanonical directive scope: {scope!r}")
    if "run" in selectors and not _RUN_ID.fullmatch(selectors["run"]):
        raise DirectiveScopeError(f"invalid run directive scope: {scope!r}")
    if "generation" in selectors and not _GENERATION.fullmatch(selectors["generation"]):
        raise DirectiveScopeError(f"invalid generation directive scope: {scope!r}")
    if "role" in selectors and selectors["role"] not in DIRECTIVE_ROLES:
        raise DirectiveScopeError(f"invalid role directive scope: {scope!r}")
    return tuple((key, selectors[key]) for key in canonical_keys)


def directive_scope_applies(
    scope: object,
    *,
    run_id: str,
    generation: int,
    role: str,
) -> bool:
    """Resolve a validated scope against one concrete invocation."""

    selectors = dict(parse_directive_scope(scope))
    return (
        ("run" not in selectors or selectors["run"] == run_id)
        and (
            "generation" not in selectors
            or int(selectors["generation"]) == generation
        )
        and ("role" not in selectors or selectors["role"] == role)
    )


def valid_directive_run_id(value: str) -> bool:
    return bool(_RUN_ID.fullmatch(value))


__all__ = [
    "DIRECTIVE_ROLES",
    "DirectiveScopeError",
    "directive_scope_applies",
    "parse_directive_scope",
    "valid_directive_run_id",
]
