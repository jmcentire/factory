"""factory_core.manifest — the content-addressed, hash-chained evidence ledger.

This is the tamper-evident spine of the factory: an append-only ledger of lifecycle
transitions, each entry content-addressed (SHA-256) and chained to the prior entry's
address, and each entry fail-closed on segregation of duties (implementer != verifier !=
approver). It is a projection-free source of truth — the board is a projection of the
ledger, never the reverse.

Design lineage: this is the domain-agnostic generalization of the proven single-manifest
ledger pattern (content-addressing + write-time SoD refusal + tamper-evidence). The
generalization adds the append-only hash-chain so a *sequence* of transitions is
independently verifiable, and it carries nothing about any particular target — every field
is a digest, a structured result, a recorded identity, or opaque payload data.

Guarantees:
  * Content-addressed — an entry's identity IS the SHA-256 of its canonical body; any edit
    changes the address.
  * Hash-chained — every entry records the prior entry's address; tampering with any entry
    breaks the chain at the next link, so the whole history is verifiable, not just a leaf.
  * Segregation of duties — implementer, verifier, and approver must be three distinct
    identities; a write with any two-role overlap is refused (fail closed). When an optional
    policy is supplied, the approver must additionally resolve to an enrolled human.
  * Stdlib only (hashlib + json) — it runs anywhere Python runs, with no third-party surface.

There is deliberately no clock and no disk-reading identity resolution in this module: the
caller stamps ``created_at`` and supplies any ``SegregationPolicy``. Impurity (git, IdP,
files) lives behind the adapter seams, never here.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "factory-manifest/1"


class SegregationError(ValueError):
    """Raised when a ledger append is refused because segregation of duties is violated.

    Fail-closed: the ledger never appends an entry whose implementer/verifier/approver
    identities overlap (or, under a policy, whose approver is not an enrolled human)."""


# --------------------------------------------------------------------------- #
# Content addressing (SHA-256)
# --------------------------------------------------------------------------- #

def digest_bytes(data: bytes) -> str:
    """Content address: ``sha256:<hex>`` of raw bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_file(path: str) -> str:
    """Content address of a file's bytes."""
    with open(path, "rb") as fh:
        return digest_bytes(fh.read())


def digest_obj(obj: Any) -> str:
    """Content address of a JSON-serializable object via a canonical (sorted, compact)
    encoding, so identical logical content always yields the same address regardless of
    key order or whitespace."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest_bytes(canonical)


# --------------------------------------------------------------------------- #
# Segregation-of-duties policy (identity resolution) — domain-agnostic
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SegregationPolicy:
    """The SHAPE of the segregation-of-duties policy in force.

    A plain stdlib value object: the ledger never reads disk and never runs git. The impure
    resolver (an IdP / VCS seam) BUILDS this from enrollment data and passes it to the ledger;
    the ledger only consumes it. That preserves the seam — the ledger is pure, fail-closed
    enforcement; identity resolution is somebody else's job.

    Identity resolution rules:
      * ``human_aliases`` maps any enrolled alias (email, SSO subject, the id itself),
        lowercased, to the canonical human id. Enrollment is POSITIVE: an identity is a human
        IFF it canonicalizes to an enrolled ``human_ids`` member.
      * ``excluded_service_identities`` is a DENYLIST of exact ids or fnmatch globs.
        DENY ALWAYS WINS — a match here can never resolve to a human, even if (mis)enrolled.
    """

    human_ids: frozenset[str] = frozenset()
    human_aliases: dict[str, str] = field(default_factory=dict)  # lowercased alias -> canonical id
    excluded_service_identities: frozenset[str] = frozenset()  # exact ids OR fnmatch globs
    require_signature: bool = False
    allowlist_digest: str = ""

    def canonical(self, identity: str) -> str:
        """Map an identity to its canonical human id if enrolled, else return it stripped, so
        two aliases of the same principal cannot slip past a distinctness check as 'distinct'."""
        if not identity:
            return identity
        return self.human_aliases.get(identity.strip().lower(), identity.strip())

    def is_excluded(self, identity: str) -> bool:
        """True if the identity matches any denylist entry/glob (DENY ALWAYS WINS)."""
        if not identity:
            return False
        cand = identity.strip()
        low = cand.lower()
        for pat in self.excluded_service_identities:
            if fnmatch.fnmatch(low, pat.lower()) or fnmatch.fnmatch(cand, pat):
                return True
        return False

    def resolve_human(self, identity: str) -> str | None:
        """Resolve an identity to a canonical enrolled human, or None. An excluded
        service/agent identity NEVER resolves (deny wins), even before enrollment is checked."""
        if not identity or self.is_excluded(identity):
            return None
        canon = self.canonical(identity)
        if self.is_excluded(canon):
            return None
        return canon if canon in self.human_ids else None


# --------------------------------------------------------------------------- #
# The ledger entry (one lifecycle transition)
# --------------------------------------------------------------------------- #

@dataclass
class LedgerEntry:
    """One append-only, content-addressed transition record. Every field is a digest, a
    structured result, a recorded identity, or opaque payload data — never prose-as-truth.

    The three SoD identities carry the load: ``implementer`` built it, ``verifier`` proved it,
    ``approver`` accepted the risk. Any two overlapping is refused at append time. For
    transitions that involve fewer than three roles (e.g. a draft edit), leave the unused
    identities empty; distinctness is enforced only among the identities actually present."""

    # --- chain linkage (set by the ledger at append time) ---
    seq: int = 0
    prev_hash: str = ""  # the prior entry's content address ("" for the genesis entry)

    # --- the transition this entry records (generic; state vocabulary is fed-in) ---
    capability_id: str = ""
    from_state: str = ""
    to_state: str = ""

    # --- segregation of duties: three distinct signing identities ---
    implementer_identity: str = ""
    verifier_identity: str = ""
    approver_identity: str = ""
    # optional provenance binding for the implementer (e.g. verified VCS authorship); when
    # present it is checked, but the ledger itself never resolves it (that is a seam's job).
    implementer_provenance: dict[str, Any] = field(default_factory=dict)

    # --- what was built / how it was verified / the gate's verdict (all data) ---
    artifact_digests: dict[str, Any] = field(default_factory=dict)
    gate_verdict: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    # --- provenance of this record ---
    schema_version: str = SCHEMA_VERSION
    actor: str = ""  # descriptive who/what triggered the transition (NOT an SoD identity)
    created_at: str = ""  # caller-stamped; no nondeterministic clock in the core

    def body(self) -> dict[str, Any]:
        """The content that gets addressed (everything except the self-referential digest)."""
        return asdict(self)

    def content_digest(self) -> str:
        """This entry's own content address — its tamper-evident identity."""
        return digest_obj(self.body())

    def validate_sod(self, policy: SegregationPolicy | None = None) -> list[str]:
        """Return a list of segregation-of-duties violations; empty means SoD holds.

        Always enforced (no policy needed):
          * distinctness — implementer, verifier, and approver must be three distinct
            identities; any two present-and-equal is a violation;
          * bound implementer — if ``implementer_provenance`` is supplied, its ``source`` and
            bound author must agree with ``implementer_identity`` (a self-asserted label with a
            contradicting binding is refused).

        Enforced only when a ``policy`` is supplied:
          * approver-is-human — the approver must resolve to an enrolled human (an excluded
            service/agent identity or an un-enrolled string can never approve);
          * signature — if ``policy.require_signature`` and provenance is present, the binding
            must be signature-verified.
        """
        v: list[str] = []

        def canon(x: str) -> str:
            return policy.canonical(x) if (policy and x) else x

        roles = {
            "implementer": self.implementer_identity,
            "verifier": self.verifier_identity,
            "approver": self.approver_identity,
        }
        present = [(name, canon(ident)) for name, ident in roles.items() if ident]
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                name_a, id_a = present[i]
                name_b, id_b = present[j]
                if id_a == id_b:
                    v.append(
                        f"{name_a} == {name_b} ({id_a}): implementer, verifier, and approver "
                        "must be three distinct identities (no role verifies/approves its own work)"
                    )

        prov = self.implementer_provenance or {}
        if prov:
            if prov.get("source") not in ("git", "vcs"):
                v.append(
                    f"implementer provenance source is {prov.get('source')!r}, not a verified "
                    "VCS binding: an implementer binding must be provenance-backed"
                )
            author = prov.get("author_identity", "")
            if self.implementer_identity and author and self.implementer_identity != author:
                v.append(
                    f"implementer_identity ({self.implementer_identity!r}) does not match the "
                    f"bound author ({author!r}): the implementer must BE the verified author"
                )
            if policy and policy.require_signature and not prov.get("signature_verified"):
                v.append(
                    "signature policy: the active policy requires a signature-verified binding, "
                    "but implementer_provenance.signature_verified is not True"
                )

        if policy is not None and self.approver_identity:
            if policy.resolve_human(self.approver_identity) is None:
                v.append(
                    f"approver {self.approver_identity!r} does not resolve to an enrolled human "
                    "(it is an excluded service/agent identity or is not on the human allowlist): "
                    "an agent or un-enrolled identity can never approve"
                )
        return v


# --------------------------------------------------------------------------- #
# The append-only, hash-chained ledger
# --------------------------------------------------------------------------- #

class Ledger:
    """An append-only, content-addressed, hash-chained ledger persisted as JSONL.

    Each line is ``{"entry_hash": <addr>, **entry.body()}``. The ledger is the source of
    truth; any projection (a board, a report) is derived from it. Appends are fail-closed on
    segregation of duties, and ``verify_chain`` re-derives every address and every prior-link
    to prove the whole history is untampered.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def _records(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records: list[dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def __len__(self) -> int:
        return len(self._records())

    def head_hash(self) -> str:
        """The content address of the most recent entry, or "" if the ledger is empty."""
        records = self._records()
        return records[-1]["entry_hash"] if records else ""

    def append(self, entry: LedgerEntry, policy: SegregationPolicy | None = None) -> str:
        """Append a transition entry, chaining it to the current head. Returns the new entry's
        content address. Refuses (raises ``SegregationError``) if SoD is violated — fail closed."""
        records = self._records()
        entry.seq = len(records)
        entry.prev_hash = records[-1]["entry_hash"] if records else ""

        violations = entry.validate_sod(policy)
        if violations:
            raise SegregationError(
                "segregation-of-duties violation; ledger append refused:\n  "
                + "\n  ".join(violations)
            )

        addr = entry.content_digest()
        record = {"entry_hash": addr, **entry.body()}
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return addr

    def entries(self) -> list[dict[str, Any]]:
        """All persisted entry records (including their ``entry_hash``), in order."""
        return self._records()

    def verify_chain(self) -> tuple[bool, str]:
        """Walk the whole chain and prove it is untampered. Checks, per entry: the stored
        address re-derives from the body (content-address integrity), the sequence increments
        by one, and the recorded ``prev_hash`` equals the prior entry's address (chain
        linkage). Returns ``(ok, detail)``."""
        prev = ""
        for i, record in enumerate(self._records()):
            stored = record.get("entry_hash", "")
            body = {k: val for k, val in record.items() if k != "entry_hash"}
            recomputed = digest_obj(body)
            if recomputed != stored:
                return False, (
                    f"entry {i}: content-address mismatch (tampered body); "
                    f"{recomputed} != {stored}"
                )
            if body.get("seq") != i:
                return False, f"entry {i}: sequence mismatch (expected {i}, got {body.get('seq')})"
            if body.get("prev_hash", "") != prev:
                return False, (
                    f"entry {i}: broken hash-chain link (prev_hash does not match prior entry)"
                )
            prev = stored
        return True, "chain intact"


def verify_ledger(path: str) -> tuple[bool, str]:
    """Convenience wrapper: verify the hash-chain of the ledger at ``path``."""
    return Ledger(path).verify_chain()
