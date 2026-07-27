"""Content-addressed evidence primitives shared by factory controls.

An evidence record is not trusted because it exists. Its claimed address must re-derive from
its canonical body, and the body must bind the exact subject a control is deciding. This
module owns that small, reusable mechanical check; authority over who may produce an evidence
record remains outside the core.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from factory_core.manifest import verify_digest


@dataclass(frozen=True)
class EvidenceIntegrity:
    """A content-addressed evidence artifact and the address claimed for it."""

    body: Mapping[str, Any] | None = None
    claimed_digest: str = ""

    @property
    def present(self) -> bool:
        """Return whether both an evidence body and claimed address were supplied."""

        return self.body is not None and bool(self.claimed_digest)

    def verify(self) -> bool:
        """Re-derive the evidence address. Absence and tamper both return ``False``."""

        if not self.present:
            return False
        return verify_digest(dict(self.body or {}), self.claimed_digest)

    def verifies_binding(self, expected: Mapping[str, Any]) -> bool:
        """Verify integrity plus the exact subject fields required by a control.

        Additional evidence fields are permitted, but an artifact about another candidate,
        item, scope, result, or decision cannot be replayed here.
        """

        if not self.verify() or self.body is None:
            return False
        return all(self.body.get(key) == value for key, value in expected.items())

    def to_dict(self) -> dict[str, Any]:
        """Return the wire representation used in manifests."""

        return {
            "body": dict(self.body) if self.body is not None else None,
            "claimed_digest": self.claimed_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> EvidenceIntegrity | None:
        """Load an evidence record without guessing malformed body shapes."""

        if raw is None:
            return None
        body = raw.get("body")
        return cls(
            body=body if isinstance(body, Mapping) else None,
            claimed_digest=str(raw.get("claimed_digest", "")),
        )
