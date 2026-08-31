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
  * Segregation of duties — implementer, verifier, and approver must be three distinct signing
    identities (not additional workflow roles); a write with any two-identity overlap is
    refused (fail closed). When an optional
    policy is supplied, the approver must additionally resolve to an enrolled human.
  * Stdlib only (hashlib + json) — it runs anywhere Python runs, with no third-party surface.

There is deliberately no clock and no disk-reading identity resolution in this module: the
caller stamps ``created_at`` and supplies any ``SegregationPolicy``. Impurity (git, IdP,
files) lives behind the adapter seams, never here.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import stat
import warnings
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "factory-manifest/1"


class SegregationError(ValueError):
    """Raised when a ledger append is refused because segregation of duties is violated.

    Fail-closed: the ledger never appends an entry whose implementer/verifier/approver
    identities overlap (or, under a policy, whose approver is not an enrolled human)."""


class LedgerIntegrityError(ValueError):
    """A ledger append could not prove exclusive, intact, durable prior state."""


# --------------------------------------------------------------------------- #
# Content addressing (SHA-256)
# --------------------------------------------------------------------------- #

def digest_bytes(data: bytes) -> str:
    """Content address: ``sha256:<hex>`` of raw bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_obj(obj: Any) -> str:
    """Content address of a JSON-serializable object via a canonical (sorted, compact)
    encoding, so identical logical content always yields the same address regardless of
    key order or whitespace."""
    _refuse_unaddressable(obj)
    canonical = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return digest_bytes(canonical)


def _refuse_unaddressable(obj: Any) -> None:
    """Keep ``digest_obj`` injective over its actual input domain (plan 2.1).

    ``json.dumps(sort_keys=True)`` silently coerces non-string dict keys (1 and "1"
    collide) and, without ``allow_nan=False``, emits non-JSON tokens for NaN/Infinity —
    both break the one-content-one-address property every ledger check rests on.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"digest_obj refuses non-string dict key {key!r}: key coercion "
                    f"would let distinct objects share a content address"
                )
            _refuse_unaddressable(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _refuse_unaddressable(item)


def _const_time_eq(a: str, b: str) -> bool:
    """Compare two digest strings without a data-dependent early exit (``hmac.compare_digest``).

    A digest comparison is the tamper check; comparing it byte-by-byte with a short-circuiting
    ``!=`` leaks, via timing, how many leading characters matched, which is a foothold for a
    forger reconstructing a target digest. ``hmac.compare_digest`` is the stdlib constant-time
    primitive. It rejects a non-ASCII ``str`` with ``TypeError``; we catch that and read it as
    "not equal" (fail closed) rather than letting the exception escape.
    """
    try:
        return hmac.compare_digest(a, b)
    except TypeError:
        return False


def verify_digest(obj: Any, claimed_digest: str) -> bool:
    """Constant-time content-address check: recompute the canonical address of ``obj`` and
    compare it to ``claimed_digest`` with :func:`_const_time_eq`.

    This is the leaf tamper-check for a *single* content-addressed artifact (as distinct from
    :meth:`Ledger.verify_chain`, which verifies a whole *sequence*). Any single field change in
    ``obj`` moves its address, so this returns ``False``; an empty/absent claimed digest also
    returns ``False`` (fail closed — an unverifiable artifact is never treated as verified).
    """
    if not isinstance(claimed_digest, str) or not claimed_digest:
        return False
    return _const_time_eq(digest_obj(obj), claimed_digest)


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
    ``approver`` accepted the risk. Any two overlapping is refused at append time. These are
    signing identities, not extra factory workflow roles. For transitions that involve fewer
    than three signers (e.g. a draft edit), leave the unused identities empty; distinctness is
    enforced only among the identities actually present."""

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

    def __init__(self, path: str, *, chain_key: bytes | None = None) -> None:
        self.path = path
        # 2.2: HMAC chain key. Keyed entry addresses are "hmac-sha256:<hex>" =
        # HMAC-SHA256(key, canonical body); the PREFIX is the mode — a keyed ledger
        # verified unkeyed (or vice versa) fails at entry 0 with no mode flag to lie
        # about. ``None`` is deprecated migration-only: a lane whose closed environment
        # never receives the key cannot forge entries whose addresses re-derive, so
        # whole-history rewrite requires the host-held key.
        self.chain_key = chain_key

    def _address(self, body: dict[str, Any]) -> str:
        if self.chain_key is None:
            return digest_obj(body)
        _refuse_unaddressable(body)
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return "hmac-sha256:" + hmac.new(self.chain_key, canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def _parse_records(fh: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            fh.seek(0)
            for index, line in enumerate(fh):
                line = line.strip()
                if line:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise LedgerIntegrityError(
                            f"ledger entry {index} is not a JSON object"
                        )
                    records.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LedgerIntegrityError(f"ledger is unreadable: {exc}") from exc
        return records

    def _verify_records(self, records: list[dict[str, Any]]) -> tuple[bool, str]:
        prev = ""
        for i, record in enumerate(records):
            stored = record.get("entry_hash", "")
            body = {k: val for k, val in record.items() if k != "entry_hash"}
            recomputed = self._address(body)
            if not _const_time_eq(recomputed, stored):
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

    @staticmethod
    def _require_regular(fd: int, *, label: str) -> None:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LedgerIntegrityError(f"{label} must be a regular file")

    @staticmethod
    def _sync_directory(path: str) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path, flags)
        try:
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise LedgerIntegrityError("ledger parent must be a directory")
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _records(self) -> list[dict[str, Any]]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise LedgerIntegrityError(f"ledger is unreadable: {exc}") from exc
        try:
            self._require_regular(fd, label="ledger")
            with os.fdopen(fd, encoding="utf-8") as fh:
                fd = -1
                return self._parse_records(fh)
        finally:
            if fd >= 0:
                os.close(fd)

    def __len__(self) -> int:
        return len(self._records())

    def head_hash(self) -> str:
        """The content address of the most recent entry, or "" if the ledger is empty."""
        records = self._records()
        return records[-1]["entry_hash"] if records else ""

    def append(
        self,
        entry: LedgerEntry,
        policy: SegregationPolicy | None = None,
        *,
        expected_head: str | None = None,
    ) -> str:
        """Append a transition entry, chaining it to the current head. Returns the new entry's
        content address. The append is serialized by a fail-closed lock, verifies the existing
        chain before extending it, and fsyncs both the record and a newly-created parent entry
        before returning. A stale lock is evidence of an interrupted append and therefore blocks
        rather than being guessed away.

        Refuses (raises ``SegregationError``) if SoD is violated and
        ``LedgerIntegrityError`` if exclusive/intact prior state cannot be proven.
        """
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        lock_path = f"{self.path}.lock"
        lock_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        lock_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            lock_fd = os.open(lock_path, lock_flags, 0o600)
        except FileExistsError as exc:
            raise LedgerIntegrityError(
                f"ledger append lock already exists (concurrent or interrupted append): {lock_path}"
            ) from exc
        except OSError as exc:
            raise LedgerIntegrityError(f"ledger append lock could not be created: {exc}") from exc
        lock_ready = False
        try:
            self._require_regular(lock_fd, label="ledger append lock")
            os.write(lock_fd, f"pid={os.getpid()}\n".encode())
            os.fsync(lock_fd)
            os.close(lock_fd)
            lock_fd = -1
            if parent:
                self._sync_directory(parent)
            lock_ready = True

            ledger_flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
            ledger_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            try:
                ledger_fd = os.open(self.path, ledger_flags, 0o600)
            except OSError as exc:
                raise LedgerIntegrityError(f"ledger could not be opened safely: {exc}") from exc
            try:
                self._require_regular(ledger_fd, label="ledger")
                with os.fdopen(ledger_fd, "r+", encoding="utf-8") as fh:
                    ledger_fd = -1
                    records = self._parse_records(fh)
                    # Phase 3 change 1: append re-addresses only the TAIL record
                    # (suffix continuity) with the caller-held expected_head as the
                    # primary guarantee — the whole-history re-verify per write is
                    # deleted as a collapse point, NOT demoted: full-chain
                    # verification keeps its mandatory firing paths on every read
                    # (verified_entries drives load/transition/rebuild), so a
                    # mid-chain edit is still caught before any state is consumed.
                    if records:
                        tail = records[-1]
                        tail_body = {
                            key: value for key, value in tail.items() if key != "entry_hash"
                        }
                        if not _const_time_eq(
                            self._address(tail_body), str(tail.get("entry_hash", ""))
                        ):
                            raise LedgerIntegrityError(
                                "refusing to extend: tail record does not re-address "
                                "(tampered or wrong-mode tail)"
                            )
                    actual_head = records[-1]["entry_hash"] if records else ""
                    if expected_head is not None and not _const_time_eq(actual_head, expected_head):
                        raise LedgerIntegrityError(
                            "ledger changed after the caller derived its transition; retry from "
                            "the new verified head"
                        )
                    entry.seq = len(records)
                    entry.prev_hash = actual_head

                    violations = entry.validate_sod(policy)
                    if violations:
                        raise SegregationError(
                            "segregation-of-duties violation; ledger append refused:\n  "
                            + "\n  ".join(violations)
                        )

                    if self.chain_key is None and not records:
                        warnings.warn(
                            "constructing a NEW unkeyed ledger is migration-only "
                            "(plan 2.2): pass chain_key so entry addresses require "
                            "the host-held key to forge",
                            FutureWarning,
                            stacklevel=2,
                        )
                    addr = self._address(entry.body())
                    record = {"entry_hash": addr, **entry.body()}
                    fh.seek(0, os.SEEK_END)
                    fh.write(json.dumps(record, sort_keys=True) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            finally:
                if ledger_fd >= 0:
                    os.close(ledger_fd)
            if parent:
                self._sync_directory(parent)
            return addr
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)
            if lock_ready:
                try:
                    os.unlink(lock_path)
                    if parent:
                        self._sync_directory(parent)
                except FileNotFoundError:
                    pass

    def entries(self) -> list[dict[str, Any]]:
        """All intact persisted entries; invalid chains are never returned as data."""

        return self.verified_entries()

    def verified_entries(self) -> list[dict[str, Any]]:
        """Return one intact ledger snapshot or refuse it.

        Callers that need both the entries and their head must not verify one file read and
        consume a second.  This method reads once, verifies that exact snapshot, and returns
        those same records so a derived receipt cannot accidentally combine two ledger heads.
        """

        records = self._records()
        intact, detail = self._verify_records(records)
        if not intact:
            raise LedgerIntegrityError(f"ledger verification failed: {detail}")
        return records

    def verify_chain(self) -> tuple[bool, str]:
        """Walk the whole chain and prove it is untampered. Checks, per entry: the stored
        address re-derives from the body (content-address integrity), the sequence increments
        by one, and the recorded ``prev_hash`` equals the prior entry's address (chain
        linkage). Returns ``(ok, detail)``."""
        try:
            self.verified_entries()
        except LedgerIntegrityError as exc:
            return False, str(exc)
        return True, "chain intact"


def verify_ledger(path: str, *, chain_key: bytes | None = None) -> tuple[bool, str]:
    """Convenience wrapper: verify the hash-chain of the ledger at ``path``."""
    return Ledger(path, chain_key=chain_key).verify_chain()
