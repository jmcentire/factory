"""Gate L runtime: the sole harness-close path through ``decide_promotion``.

The harness (``promote.sh``) is the sole writer of ``harness.json`` ``"closed"`` status, and it
reaches the promotion decision ONLY through this module — invoked as a subprocess via the
factory CLI (``factory promote``), so the generic harness never imports the factory package.
This module is the wire that makes the deterministic cage (``factory_core.promotion``) live
on a real run rather than exercised by tests alone.

The translator loads the run's ``promotion_inputs.json`` — the single artifact the run's
evidence-production pipeline gathers: the ``PromotionRequest`` fields (candidate digest,
lane, disturbed surfaces, per-surface observations with their oracle/flake receipts,
gates, provenance, tool policy, independence, monitors, the candidate-build receipt) plus
the ``SegregationPolicy`` (enrollment, from the intake authority) and ``CriticalityProfile``
(surface criticality, from the ratified architecture). It builds the request and calls the
PURE ``decide_promotion``; it performs no promotion itself. The CLI writes the decision to
``promotion_verdict.json``; ``promote.sh`` closes harness coordination only when the decision
allows. It never mutates authoritative RunStore ``run.json``.

An absent or malformed ``promotion_inputs.json`` is FAIL-CLOSED: a run that has not
gathered its evidence cannot close, and the close-path refuses rather than advancing on no
evidence. This is the honest state of an incomplete evidence pipeline — the cage blocks
until the inputs arrive, which is exactly what enforcement means. The alternative (closing
on ``make ship`` green, as the pre-cage harness did) is the route-around this gate exists to
prevent: a green build is not a verified run.

HONEST SCOPE (updated 2026-08-14). ``endgame.sh`` invokes ``promote.sh`` only after its other
gates are green, so this module is now on the live harness close path. No producer of
``promotion_inputs.json`` exists yet, however; an otherwise green live run therefore stops at
Gate L until those inputs have been gathered. The harness close also remains distinct from a
RunStore ``PROMOTED`` ledger transition.

What this module DOES do — the F3 chain-anchor check (built 2026-08-14, hardened R2/R3
2026-08-14). The pure core verifies each receipt envelope's content-address, subject binding,
and value equality, but a self-consistent FORGERY (a body+digest pair the agent invented) passes
the core, because the core is pure (no disk) and cannot read the tamper-evident receipt chain.
This seam NARROWS — does not close — that fabrication route-around in three layers (Opus
cross-family re-verification, 2026-08-14, CHANGES_REQUESTED then re-verified). The three
layers raise the bar from "invent any self-consistent envelope" to "the attacker must also
write ``chain.jsonl``"; the lane agent runs inside the lane that owns the chain file, so an
attacker who can append can still MINT a never-existed receipt (R4, below) — the three layers
are not authenticity, they are per-entry + link + id-uniqueness integrity on an agent-writable
file. They close lazy tampering (edit one entry, leave the rest) and re-use of a real id; they
do not close a full forward re-chain or a mint-by-append, which need an external anchor:

- ``_load_chain`` reads ``<H>/receipts/chain.jsonl`` and re-derives each entry's bare-hex
  content-address (per-entry tamper-evidence).
- ``_load_chain`` ALSO verifies the hash-LINKAGE the producers write (each entry's
  ``prev_hash`` = the prior entry's content address, genesis = 64 zeros) and rejects duplicate
  receipt ids. Per-entry self-consistency alone is not chain tamper-evidence: an entry whose
  body was edited AND whose hash was recomputed passes the per-entry check but breaks the link,
  and a second correctly-hashed entry reusing a real receipt id would shadow it under last-wins
  indexing. The producers already write a real hash-chain (``receipt.sh``/``mutate.sh``/
  ``flake.sh`` set ``prev_hash`` to the prior entry's hash); the seam verifies what they write
  (Opus cross-family review residuals R2 + R3, 2026-08-14).
- ``verify_chain_anchor`` finds each cited envelope's real chain entry by id and confirms the
  envelope body matches a deterministic PROJECTION of that entry (oracle: oracle_adequate;
  flake: deterministic + flake_count + retry_count<-automatic_retry_count). A forged envelope
  is self-consistent (passes the core) but does not match the real entry's projection, so the
  seam fail-closes. The build projection was deleted in 1.1c: the disturbed-surface set is
  host-derived inside ``decide_promotion`` from changed paths + the plan-declared surface map,
  so there is no agent-attested build envelope left to ground (or to forge).

The prior Opus review's F3 "cannot be built without a receipt-schema decision" was FALSIFIED:
every field the envelope attests is already carried in the corresponding chain entry, and the
chain's bare-hex hash equals ``digest_obj(body).removeprefix("sha256:")`` (same canonical
encoding, prefix aside) — ordinary seam engineering, not a founder decision. A present
envelope with no chain file cannot be grounded and fail-closes; an absent envelope (no receipt
cited) is left to the core's omission-enforcement.

What this module does NOT yet do — the R1 replay residual (Opus, 2026-08-14; the single most
important risk). Each receipt envelope binds only its own ``receipt_id``; no receipt body
carries the run's ``candidate_digest`` or ``surface_id``, so a stale green receipt from another
run/suite — or a build receipt whose ``git_head`` is not compared to the promotion's candidate
— can be cited to advance. Closing this is the "one schema decision" the prior review named:
put ``candidate_digest`` + ``surface_id`` in the flake/oracle receipt bodies, bind them in
``verifies_binding``, and extend the build projection to bind ``git_head`` to the request's
``candidate_digest``. That decision belongs with the evidence-production pipeline (plan Part 7):
the envelopes are produced by that pipeline, which does not yet exist (nothing produces
``promotion_inputs.json``), so building the binding now would define a
contract with no producer — the F1 pattern. R1 is captured as the named "next proxy" (plan Part
5 §4) and lands with Part 7.

R4 — chain AUTHENTICITY / mint-by-append (Opus, 2026-08-14 re-verification; a NEW gap, not R1 in
disguise). The seam verifies *membership in an agent-writable file*: the producers run inside
the agent's own lane, so the same principal that runs the tests appends the chain. An attacker
who can append can MINT a never-existed receipt — a correctly-hashed, correctly-linked,
id-unique entry the attacker authored — and cite it to promote (Opus Exploit G promoted). The
three layers above are blind to it (the minted entry re-derives, links, and has a fresh id).
**R1's named fix does NOT close this**: putting ``candidate_digest`` + ``surface_id`` in the
receipt body and binding them stops *replay of a stale honest receipt*, but the attacker who
mints writes those fields too. Closing R4 needs authenticity, not binding — privilege-separated
receipt writing, signed entries (Tessera), or an external head/length anchor. It is its own
residual item, landing with the evidence-production pipeline (plan Part 7) alongside R1.

This is the runtime half of Gate L. The core half — Gate M/N advisory->hard-block, the
receipt-binding verification (omission-enforcement) — is in ``factory_core/promotion.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from factory_core.criticality import CriticalityProfile
from factory_core.manifest import SegregationPolicy
from factory_core.promotion import PromotionRequest, decide_promotion

PROMOTION_INPUTS = "promotion_inputs.json"
PROMOTION_VERDICT = "promotion_verdict.json"


class PromotionGateError(Exception):
    """Fail-closed: the gate refuses to render a decision it cannot ground.

    A missing-inputs refusal is the cage doing its job, not a crash: the run has not
    produced the evidence a promotion decision requires, so no decision is rendered and
    the run cannot close. The caller (the CLI) surfaces this as a non-zero exit so
    ``promote.sh`` fail-closes rather than writing ``"closed"``.
    """


# --- F3: the seam's chain-anchor check (fabrication-enforcement) ---------------------
# The core is pure (no disk) and verifies each receipt envelope's content-address, subject
# binding, and value equality — but a self-consistent FORGERY (a body+digest the agent invented)
# passes the core, because the core cannot read the tamper-evident receipt chain. Closing that
# fabrication route-around is the SEAM's job (the plan Part 7 trust model: "the seam verifies
# the cited envelope's digest is in that chain"). For each cited receipt the seam finds the
# real chain entry by id, verifies the entry's own content-address (chain integrity), and
# confirms the submitted envelope body matches a deterministic PROJECTION of that entry. A
# forged envelope is self-consistent (passes the core) but does not match the real entry's
# projection, so the seam fail-closes. This is ordinary seam engineering — the prior Opus
# cross-family review (2026-08-14) confirmed it is buildable today with no schema decision:
# every field the envelope bodies attest is already carried in the corresponding chain
# entry (oracle: oracle_adequate; flake:
# deterministic + flake_count + automatic_retry_count; all carry id), and the chain's bare-hex
# hash equals ``digest_obj(body).removeprefix("sha256:")`` (same canonical encoding, prefix
# aside). The flake producer writes ``automatic_retry_count``; the core reads ``retry_count`` —
# the projection renames it. Empirically proven: an honest envelope projected from a chain
# entry is grounded; a forged self-consistent envelope is caught by the projection mismatch.


def _chain_path(run_root: Path) -> Path:
    """The receipt chain for the harness holding ``run_root``.

    ``$H/receipts/chain.jsonl`` where ``$H`` is the harness dir; ``run_root`` is
    ``$H/runs/<run>``, so the chain is run_root's grandparent's ``receipts/chain.jsonl``.
    """
    return run_root.parent.parent / "receipts" / "chain.jsonl"


def _load_chain(chain_path: Path) -> dict[str, dict[str, Any]]:
    """Index the receipt chain by id, verifying each entry's content-address AND the
    chain's hash-linkage.

    An entry's ``hash`` is ``sha256(json.dumps(body_without_hash, sort_keys=True,
    separators=(",",":")))`` — bare hex, no ``"sha256:"`` prefix (the harness producers and
    the core's ``digest_obj`` differ only by that prefix). The producers (``receipt.sh``,
    ``mutate.sh``, ``flake.sh``) write a real hash-chain: each entry's ``prev_hash`` is the
    prior entry's content address, with the genesis entry's ``prev_hash`` = 64 zeros. This
    function verifies BOTH invariants the producers write, not just per-entry
    self-consistency:

    - Per-entry tamper-evidence: an entry whose ``hash`` does not re-derive (body edited
      after hashing) is a tampered chain.
    - Hash-linkage (Opus R2): an entry whose ``prev_hash`` does not equal the prior entry's
      content address breaks the chain. A per-entry self-consistent rewrite (body edited AND
      hash recomputed) would pass a per-entry-only check — linkage is what makes the chain
      tamper-evident as a chain, not merely as a bag of entries. The genesis entry's
      ``prev_hash`` must be 64 zeros (the producers' genesis sentinel).
    - Duplicate-id rejection (Opus R3): a second correctly-hashed entry reusing a real
      receipt id would shadow the first under last-wins indexing. The producers append under
      ``fcntl`` locking but a duplicate is still writable; the seam rejects it so a re-used id
      cannot substitute a green receipt for a red one.

    A missing chain file returns ``{}`` (no chain to anchor against — a present envelope then
    fail-closes in ``verify_chain_anchor``). A corrupt/unreadable chain, a tampered entry, a
    broken linkage, or a duplicate id all raise ``PromotionGateError`` — the seam refuses to
    ground a decision on a chain that is not a verified, link-consistent, id-unique chain.
    """
    if not chain_path.exists():
        return {}
    try:
        text = chain_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromotionGateError(f"chain-anchor: chain unreadable: {exc}") from exc
    entries: dict[str, dict[str, Any]] = {}
    prev_hash = "0" * 64  # genesis: the producers write prev_hash = "0"*64 for the first entry
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise PromotionGateError(f"chain-anchor: corrupt chain entry: {exc}") from exc
        body = {k: v for k, v in entry.items() if k != "hash"}
        rederived = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if rederived != entry.get("hash"):
            raise PromotionGateError(
                f"chain-anchor: entry {entry.get('id')!r} hash does not re-derive (tampered chain)"
            )
        link = entry.get("prev_hash")
        if link != prev_hash:
            raise PromotionGateError(
                f"chain-anchor: entry {entry.get('id')!r} prev_hash does not link the chain "
                f"(expected {prev_hash[:12]}, got {str(link)[:12]}) — chain is not tamper-evident"
            )
        # Advance the cursor on EVERY physically-previous entry (Opus optional fix). The
        # producers link each entry to the physically-previous one; advancing only after the
        # id check made an id-less entry skip the cursor, so the linkage rule silently became
        # "link to the last id-bearing entry" — a mismatch that fails closed today (all three
        # producers always set id, so it is unreachable) but is wrong against the producers'
        # own convention. Advancing here makes the seam's cursor match the producers'.
        prev_hash = rederived  # the next entry must link to THIS entry's content address
        rid = entry.get("id")
        if rid is None:
            continue
        key = str(rid)
        if key in entries:
            raise PromotionGateError(
                f"chain-anchor: duplicate receipt id {key!r} — a re-used id shadows the real "
                f"entry (append-only does not permit a second entry for one receipt)"
            )
        entries[key] = entry
    return entries


def _envelope_projection(entry: dict[str, Any], kind: str) -> dict[str, Any]:
    """Re-derive the envelope body the core expects, as a projection of the real chain entry.

    ``kind`` is ``"oracle"`` (Gate N oracle receipt) or ``"flake"`` (Gate N flake
    receipt); the Gate M build-envelope branch was deleted in 1.1c — the disturbed-surface
    set is host-derived inside decide_promotion from changed paths and the plan-declared
    surface map, so no build envelope exists to ground. The flake producer writes
    ``automatic_retry_count``; the core reads ``retry_count`` — the projection renames it so
    the envelope and the chain entry speak the same field names.
    """
    if kind == "oracle":
        return {"receipt_id": entry.get("id"), "oracle_adequate": entry.get("oracle_adequate")}
    if kind == "flake":
        return {
            "receipt_id": entry.get("id"),
            "deterministic": entry.get("deterministic"),
            "flake_count": entry.get("flake_count"),
            "retry_count": entry.get("automatic_retry_count"),
        }
    raise PromotionGateError(f"chain-anchor: unknown receipt kind {kind!r}")


def _verify_grounded(
    env: Any, receipt_id: str, chain: dict[str, dict[str, Any]], kind: str
) -> None:
    """Fail-closed unless ``env`` is grounded in a real, hash-verified chain entry."""
    entry = chain.get(receipt_id)
    if entry is None:
        raise PromotionGateError(
            f"chain-anchor: receipt {receipt_id!r} not in the chain (forged or stale)"
        )
    if env is None or not env.present:
        raise PromotionGateError(
            f"chain-anchor: receipt {receipt_id!r} is cited but its envelope is absent"
        )
    expected = _envelope_projection(entry, kind)
    if not all(env.body.get(k) == v for k, v in expected.items()):
        raise PromotionGateError(
            f"chain-anchor: envelope for {receipt_id!r} does not match the chain entry "
            f"(forged — self-consistent but not grounded in the real receipt)"
        )


def verify_chain_anchor(request: PromotionRequest, chain: dict[str, dict[str, Any]]) -> None:
    """Fail-closed unless every cited receipt envelope is grounded in the real chain.

    A cited receipt whose id is not in the chain, whose chain entry is tampered (caught by
    ``_load_chain``), or whose envelope body does not match the entry's projection, is a
    fabrication (or a stale receipt) — the gate refuses to ground a decision on it. An absent
    envelope (no receipt cited) is not verified here; the core's omission-enforcement
    hard-blocks it on disturbed surfaces. A present envelope with an empty chain (no chain
    file) cannot be grounded and fail-closes — the only safe answer when the seam cannot verify.
    """
    for obs in request.observations:
        if obs.oracle_receipt:
            _verify_grounded(obs.oracle_receipt_evidence, obs.oracle_receipt, chain, "oracle")
        if obs.flake_receipt:
            _verify_grounded(obs.flake_receipt_evidence, obs.flake_receipt, chain, "flake")


def _policy_from_dict(raw: dict[str, Any]) -> SegregationPolicy:
    """Build the segregation policy from the intake-established enrollment data.

    ``SegregationPolicy`` is built by the authority seam (genesis) at intake; the
    evidence-production pipeline carries the resolved policy into ``promotion_inputs.json``
    so the translator does not re-resolve identity (the ledger is pure, identity resolution
    is the seam's job). Keys are lowercased to match ``canonical()``'s lookup.
    """
    aliases = raw.get("human_aliases") or {}
    if not isinstance(aliases, Mapping):
        raise PromotionGateError(
            "promotion-inputs-malformed: policy.human_aliases must be a JSON object "
            f"(alias -> identity), got {type(aliases).__name__}"
        )
    # Strip BOTH keys and values (Opus F9): canonical() strips the lookup key, and an
    # enrollment identity carries the same whitespace/case sensitivity either side of the
    # map, so a value like "  alice" would never match a human_id "alice" and the alias
    # would silently fail to resolve. human_ids members are stripped for the same reason.
    return SegregationPolicy(
        human_ids=frozenset(str(x).strip() for x in (raw.get("human_ids") or []) if str(x).strip()),
        human_aliases={
            str(k).strip().lower(): str(v).strip()
            for k, v in aliases.items()
            if str(k).strip() and str(v).strip()
        },
        excluded_service_identities=frozenset(
            str(x).strip() for x in (raw.get("excluded_service_identities") or []) if str(x).strip()
        ),
        require_signature=bool(raw.get("require_signature", False)),
        allowlist_digest=str(raw.get("allowlist_digest", "")),
    )


def decide(run_root: Path) -> dict[str, Any]:
    """Load the run's gathered evidence and render the pure promotion decision.

    Returns the ``PromotionDecision`` as a dict (the ``promotion_verdict.json`` body).
    Raises ``PromotionGateError`` (fail-closed) when the inputs are absent, unreadable, or
    malformed — the run has not gathered its evidence and no decision is rendered.
    """
    inputs_path = run_root / PROMOTION_INPUTS
    if not inputs_path.exists():
        raise PromotionGateError(
            f"promotion-inputs-missing: {inputs_path} — a run cannot close without the "
            "evidence-production pipeline gathering promotion_inputs.json; the close-path "
            "refuses rather than advancing on no evidence"
        )
    try:
        raw = json.loads(inputs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionGateError(f"promotion-inputs-unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromotionGateError("promotion-inputs-malformed: root is not a JSON object")

    # Wrap the from_dict builders (Opus F8): a malformed-but-JSON input (e.g.
    # human_aliases as a list, or an observation that is not an object) raises
    # AttributeError/TypeError/ValueError from inside a from_dict. The CLI's main()
    # catches only (OSError, ValueError), so an AttributeError would escape as a traceback
    # (exit 1) rather than a refused control (exit 2). Fail-closed wraps it: any parsing
    # failure is a malformed input the gate refuses to ground a decision on.
    try:
        request = PromotionRequest.from_dict(raw.get("request") or {})
        policy = _policy_from_dict(raw.get("policy") or {})
        profile = CriticalityProfile.from_dict(raw.get("profile") or {})
    except PromotionGateError:
        raise
    except (TypeError, AttributeError, ValueError) as exc:
        raise PromotionGateError(f"promotion-inputs-malformed: {exc}") from exc
    # F3: the seam's chain-anchor check. Every cited receipt envelope must be grounded in the
    # real tamper-evident chain — an envelope whose id is not in the chain, whose chain entry is
    # tampered, or whose body does not match the entry's projection, is a fabrication the core
    # (pure, no disk) cannot catch. A present envelope with no chain file cannot be grounded and
    # fail-closes; an absent envelope (no receipt cited) is left to the core's omission-enforcement.
    chain = _load_chain(_chain_path(run_root))
    verify_chain_anchor(request, chain)
    decision = decide_promotion(request, policy, profile)
    return decision.to_dict()


def render(run_root: Path) -> dict[str, Any]:
    """Render the decision AND persist it as ``promotion_verdict.json``.

    The CLI calls this so ``promote.sh`` (which cannot import the factory package) can read
    the verdict as a JSON file. Returns the decision dict; raises ``PromotionGateError`` on
    fail-closed (the caller surfaces a non-zero exit and writes no verdict file).
    """
    decision = decide(run_root)
    (run_root / PROMOTION_VERDICT).write_text(
        json.dumps(decision, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return decision
