#!/usr/bin/env python3
"""Own the deterministic ordering of every generated Phase-A artifact region."""

# The executable must import sibling harness controls both as a script and in tests.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

from agreement_contract import (  # noqa: E402
    BEGIN as AGREEMENT_BEGIN,
)
from agreement_contract import (
    END as AGREEMENT_END,
)
from agreement_contract import (
    MAX_ARTIFACT_BYTES,
    AgreementContractError,
    _atomic_replace,
    _read_regular,
    update_strategy,
)
from agreement_contract import (
    verify_plan as verify_agreement,
)
from run_guidance import (  # noqa: E402
    MARKERS as GUIDANCE_MARKERS,
)
from run_guidance import (
    RunGuidanceError,
    update_artifacts,
)
from run_guidance import (
    verify_plan as verify_guidance,
)
from semantic_union import (  # noqa: E402
    BEGIN as SEMANTIC_BEGIN,
)
from semantic_union import (
    END as SEMANTIC_END,
)
from semantic_union import (
    SemanticUnionError,
    update_spec,
    verify_spec,
)


class PhaseCompilerError(RuntimeError):
    """Generated phase regions are missing, stale, or in a non-canonical order."""


def _canonicalize_regions(
    path: pathlib.Path,
    marker_pairs: tuple[tuple[str, str], ...],
) -> None:
    raw = _read_regular(path, ceiling=MAX_ARTIFACT_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseCompilerError(f"{path.name} is not UTF-8") from exc
    sections: list[str] = []
    remainder = text
    for begin, end in marker_pairs:
        counts = remainder.count(begin), remainder.count(end)
        if counts == (0, 0):
            continue
        if counts != (1, 1):
            raise PhaseCompilerError(f"{path.name} has malformed generated-region markers")
        start = remainder.index(begin)
        finish = remainder.index(end, start) + len(end)
        if finish < len(remainder) and remainder[finish] == "\n":
            finish += 1
        sections.append(remainder[start:finish].rstrip("\n"))
        remainder = remainder[:start] + remainder[finish:]
    canonical = remainder.rstrip("\n")
    if sections:
        canonical += "\n\n" + "\n\n".join(sections)
    new = (canonical + "\n").encode("utf-8")
    if new != raw:
        _atomic_replace(path, raw, new)


def _verify_order(artifacts: pathlib.Path) -> None:
    try:
        product = _read_regular(
            artifacts / "product-specification.md", ceiling=MAX_ARTIFACT_BYTES
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseCompilerError("product-specification.md is not UTF-8") from exc
    guidance_product = GUIDANCE_MARKERS["product-specification.md"][0]
    if guidance_product in product and product.index(SEMANTIC_BEGIN) > product.index(
        guidance_product
    ):
        raise PhaseCompilerError("Product generated regions are not semantic-union then guidance")
    try:
        testing = _read_regular(
            artifacts / "testing-strategy.md", ceiling=MAX_ARTIFACT_BYTES
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseCompilerError("testing-strategy.md is not UTF-8") from exc
    guidance_testing = GUIDANCE_MARKERS["testing-strategy.md"][0]
    if guidance_testing in testing:
        if AGREEMENT_BEGIN not in testing:
            raise PhaseCompilerError("Testing guidance has no following agreement register")
        if testing.index(guidance_testing) > testing.index(AGREEMENT_BEGIN):
            raise PhaseCompilerError("Testing generated regions are not guidance then agreement")


def update(root: pathlib.Path, artifacts: pathlib.Path) -> None:
    update_spec(artifacts, artifacts / "product-specification.md")
    update_artifacts(root, artifacts)
    _canonicalize_regions(
        artifacts / "product-specification.md",
        (
            (SEMANTIC_BEGIN, SEMANTIC_END),
            GUIDANCE_MARKERS["product-specification.md"],
        ),
    )
    _canonicalize_regions(
        artifacts / "architecture.md",
        (GUIDANCE_MARKERS["architecture.md"],),
    )
    # Derive agreement only after Product has its final canonical semantic and
    # guidance layout, so the register cannot be born stale against the bytes
    # it inventories.
    update_strategy(root, artifacts)
    _canonicalize_regions(
        artifacts / "testing-strategy.md",
        (
            GUIDANCE_MARKERS["testing-strategy.md"],
            (AGREEMENT_BEGIN, AGREEMENT_END),
        ),
    )
    verify(root, artifacts)


def verify(root: pathlib.Path, artifacts: pathlib.Path) -> None:
    verify_spec(artifacts, artifacts / "product-specification.md")
    verify_guidance(root, artifacts)
    verify_agreement(root, artifacts)
    _verify_order(artifacts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("update", "verify"))
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--artifacts", type=pathlib.Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "update":
            update(arguments.root, arguments.artifacts)
        else:
            verify(arguments.root, arguments.artifacts)
        print(
            json.dumps(
                {"schema_version": "factory-phase-compiler/1", "verified": True},
                sort_keys=True,
            )
        )
    except (
        AgreementContractError,
        OSError,
        PhaseCompilerError,
        RunGuidanceError,
        SemanticUnionError,
        UnicodeDecodeError,
    ) as exc:
        print(f"phase compiler refused: {exc}", file=sys.stderr)
        return 71
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
