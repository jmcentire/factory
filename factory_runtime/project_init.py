"""Project start: mint every worker's key and the genesis, with nothing for the human to sign.

The invariant: **the human never holds, uses or is asked for a key.** Their authority is what
they said in the conversation (docs/standards/AUTHORITY.md, H1). At project start the host
mints one Ed25519 key per worker and one host-held key for the human principal, writes the
genesis enrolling them, and signs it with that human-principal key. Afterwards the host uses
the human-principal key for one thing only: recording the human's own words as the receipt
an approval, a request or a ruling needs.

Each worker is handed only its own key file. That buys integrity and attribution between
workers: a Tester receipt is the Tester's, and a tampered envelope fails verification. It is
not a defense against an agent that chooses not to honor the rules, and nothing here pretends
otherwise. An agent that will not follow "the human's messages are authoritative" will not be
stopped by a signature either.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes
from factory_runtime.authority import (
    ACTION_CAPABILITY,
    AuthorityPolicy,
    AuthorityVerificationError,
    load_genesis,
)
from factory_runtime.broker import BROKER_CAPABILITY
from factory_runtime.durability import CHAIN_ROOT_KEY_FILENAME
from factory_runtime.schema import validate_document
from factory_runtime.tessera import TesseraCli, VerifiedEnvelope

GENESIS_FILE = "genesis.tessera.json"
ROOT_PUBLIC_KEY_FILE = "root.pub"
ROSTER_FILE = "roster.json"

# The workers a project enrolls, in a fixed order. The human principal's key is held by the
# host; every other key is handed to the worker it names and to no one else.
WORKERS: tuple[tuple[str, str, str], ...] = (
    ("human", "human:owner", "human"),
    ("validator", "agent:validator", "agent"),
    ("orchestrator", "agent:orchestrator", "agent"),
    ("coder", "agent:coder", "agent"),
    ("tester", "agent:tester", "agent"),
)

_VALIDATOR_ACTIONS = (
    "ratify-product-specification",
    "ratify-architecture",
    "ratify-operational-maturity",
    "ratify-acceptance-obligation-catalog",
    "ratify-test-change-authorization",
)


class ProjectInitError(ValueError):
    """The project's keys or genesis could not be minted or reloaded."""


@dataclass(frozen=True)
class ProjectKeys:
    keys_dir: Path
    genesis_path: Path
    root_public_key: str
    key_paths: Mapping[str, Path]
    identities: Mapping[str, str]
    policy: AuthorityPolicy

    def worker_env(self, role: str, *, tessera_bin: str | None = None) -> dict[str, str]:
        """The environment one worker needs: the shared trust root and its own key only."""

        if role not in self.key_paths:
            raise ProjectInitError(f"unknown worker role: {role}")
        env = {
            "FACTORY_GENESIS": str(self.genesis_path),
            "FACTORY_ROOT_PUBLIC_KEY": self.root_public_key,
            "FACTORY_SIGNER_IDENTITY": self.identities[role],
            "FACTORY_SIGNING_KEY": str(self.key_paths[role]),
        }
        if tessera_bin:
            env["FACTORY_TESSERA_BIN"] = tessera_bin
        return env


def _capabilities(role: str) -> list[str]:
    if role == "human":
        return sorted({*ACTION_CAPABILITY.values(), BROKER_CAPABILITY})
    if role == "validator":
        return sorted(ACTION_CAPABILITY[action] for action in _VALIDATOR_ACTIONS)
    return []


def load_project(keys_dir: str | Path, *, tessera: TesseraCli) -> ProjectKeys:
    """Reload a project minted by ``init_project`` and verify its genesis."""

    directory = Path(keys_dir)
    if not (directory / GENESIS_FILE).exists():
        raise ProjectInitError(f"no project at {directory}; run `factory init` first")
    return _load(directory, tessera)


def _load(keys_dir: Path, tessera: TesseraCli) -> ProjectKeys:
    roster = json.loads((keys_dir / ROSTER_FILE).read_text(encoding="utf-8"))
    root_public_key = (keys_dir / ROOT_PUBLIC_KEY_FILE).read_text(encoding="utf-8").strip()
    genesis_path = keys_dir / GENESIS_FILE
    try:
        policy = load_genesis(
            genesis_path, trusted_root_public_key=root_public_key, tessera=tessera
        )
    except AuthorityVerificationError as exc:
        raise ProjectInitError(f"existing genesis does not verify: {exc}") from exc
    return ProjectKeys(
        keys_dir=keys_dir,
        genesis_path=genesis_path,
        root_public_key=root_public_key,
        key_paths={role: keys_dir / f"{role}.key" for role in roster["identities"]},
        identities=dict(roster["identities"]),
        policy=policy,
    )


# Signed records are local integrity evidence, not project knowledge. What the project keeps in
# git is the artifacts themselves, each item annotated with its authority (AUTHORITY.md H2, H8).
LOCAL_ONLY = ("keys/", CHAIN_ROOT_KEY_FILENAME, "runs/", "receipts/", "*.tessera.json")


def _keep_signatures_out_of_git(factory_dir: Path) -> None:
    ignore = factory_dir / ".gitignore"
    present = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [line for line in LOCAL_ONLY if line not in present]
    if missing:
        header = [] if present else ["# Local integrity records; never committed (factory init)"]
        ignore.write_text("\n".join([*present, *header, *missing]) + "\n", encoding="utf-8")


def _write_grants(directory: Path, identities: Mapping[str, str]) -> None:
    """One grant per worker, in the shape ``harness/lane_env.sh`` consumes.

    ``HARNESS_SECRETS=<keys>/grants/<role> lane_env.sh <keys>/grants/<role>.manifest -- cmd``
    starts that worker with the trust anchors and its own key path, and nothing else.
    """

    grants = directory / "grants"
    for role, identity in identities.items():
        if role == "human":
            continue
        role_dir = grants / role
        role_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(role_dir, 0o700)
        values = {
            "FACTORY_GENESIS": str(directory / GENESIS_FILE),
            "FACTORY_ROOT_PUBLIC_KEY": (directory / ROOT_PUBLIC_KEY_FILE)
            .read_text(encoding="utf-8")
            .strip(),
            "FACTORY_SIGNER_IDENTITY": identity,
            "FACTORY_SIGNING_KEY": str(directory / f"{role}.key"),
        }
        for name, value in values.items():
            (role_dir / name).write_text(value, encoding="utf-8")
        (grants / f"{role}.manifest").write_text("\n".join(values) + "\n", encoding="utf-8")


def init_project(
    keys_dir: str | Path,
    *,
    repository_id: str,
    tessera: TesseraCli,
    chain_root_dir: str | Path | None = None,
    clock: Callable[[], int] | None = None,
) -> ProjectKeys:
    """Mint the project's keys and genesis once; on a re-run, reload and verify them.

    The ledger chain-root key goes in ``chain_root_dir`` (default: the parent of
    ``keys_dir``, which is the default runs root's parent) and the genesis commits to its
    digest, so every run ledger under it is keyed from the first entry.
    """

    directory = Path(keys_dir)
    if (directory / GENESIS_FILE).exists():
        return _load(directory, tessera)
    if directory.exists() and any(
        p.name != ".gitignore" or p.is_symlink() or not p.is_file() or p.read_bytes() != b"*\n"
        for p in directory.iterdir()
    ):
        raise ProjectInitError(
            f"{directory} holds files but no genesis; move them aside before minting"
        )
    if not repository_id.strip():
        raise ProjectInitError("repository id is required")
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    # The directory ignores itself, so no key is ever committed by accident.
    (directory / ".gitignore").write_text("*\n", encoding="utf-8")

    chain_root = Path(chain_root_dir) if chain_root_dir is not None else directory.parent
    chain_root_file = chain_root / CHAIN_ROOT_KEY_FILENAME
    if chain_root_file.exists():
        raise ProjectInitError(f"refusing to replace an existing chain root: {chain_root_file}")
    public_keys = {role: tessera.keygen(directory / f"{role}.key") for role, _, _ in WORKERS}
    identities = {role: identity for role, identity, _ in WORKERS}
    root_public_key = public_keys["human"]
    genesis = {
        "schema_version": "factory-genesis/1",
        "repository_id": repository_id,
        "policy_id": f"{repository_id}-authority/1",
        "root_public_key": root_public_key,
        "principals": [
            {
                "identity": identity,
                "kind": kind,
                "public_key": public_keys[role],
                "capabilities": _capabilities(role),
            }
            for role, identity, kind in WORKERS
        ],
        "bootstrap": {
            "enabled": True,
            "scope": ["authorize-target-resolution", "authorize-change", "activate-policy"],
            "deactivates_when": "the first replacement policy activation is consumed",
        },
        "issued_at": (clock or (lambda: int(time.time())))(),
    }
    chain_root.mkdir(parents=True, exist_ok=True)
    material = secrets.token_hex(32).encode("ascii")
    _keep_signatures_out_of_git(chain_root)
    chain_root_file.write_bytes(material)
    os.chmod(chain_root_file, 0o600)
    genesis["chain_root_commitment"] = digest_bytes(material)
    validate_document("genesis", genesis)
    tessera.wrap_json(
        genesis,
        kind="factory-genesis",
        key_path=directory / "human.key",
        output_path=directory / GENESIS_FILE,
    )
    (directory / ROOT_PUBLIC_KEY_FILE).write_text(root_public_key + "\n", encoding="utf-8")
    (directory / ROSTER_FILE).write_text(
        json.dumps({"identities": identities, "public_keys": public_keys}, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_grants(directory, identities)
    return _load(directory, tessera)


def record_human_statement(
    project: ProjectKeys,
    *,
    run_id: str,
    action: str,
    subject_digest: str,
    said: str,
    said_at: str,
    output_path: str | Path,
    tessera: TesseraCli,
    ttl_seconds: int = 7 * 24 * 3600,
    clock: Callable[[], int] | None = None,
    request: Mapping[str, Any] | None = None,
) -> VerifiedEnvelope:
    """Record what the human said as the authority receipt for one action on one subject.

    ``said`` is the human's message, verbatim. The receipt quotes it, so anyone reading the
    run sees the words the authority rests on, not a summary of them.
    """

    if action not in ACTION_CAPABILITY:
        raise ProjectInitError(f"unsupported authority action: {action}")
    if not said.strip():
        raise ProjectInitError("an authority receipt needs the human's words")
    now = (clock or (lambda: int(time.time())))()
    if request is not None and (
        action != "authorize-target-resolution"
        or not isinstance(request.get("nonce"), str)
        or not isinstance(request.get("expires_at"), int)
        or not now < request["expires_at"] <= now + ttl_seconds
    ):
        raise ProjectInitError("--request takes a live target-resolution request within the TTL")
    receipt = {
        "schema_version": "factory-authority-receipt/1",
        "receipt_id": f"{action}-{secrets.token_hex(6)}",
        "run_id": run_id,
        "repository_id": project.policy.repository_id,
        "action": action,
        "subject_digest": subject_digest,
        "signer_identity": project.identities["human"],
        "capabilities": [ACTION_CAPABILITY[action]],
        "issued_at": now,
        # Stage R requires the receipt to carry its request's own nonce and expiry.
        "expires_at": request["expires_at"] if request else now + ttl_seconds,
        "nonce": request["nonce"] if request else secrets.token_hex(16),
        "basis": {"source": "user-message", "said": said, "said_at": said_at},
    }
    validate_document("authority-receipt", receipt)
    return tessera.wrap_json(
        receipt,
        kind="factory-authority-receipt",
        key_path=project.key_paths["human"],
        output_path=output_path,
    )


RULING_FIELDS: Mapping[str, tuple[str, ...]] = {
    "factory-chain-repair": ("offending_entry_hash", "reason"),
    "factory-ledger-unlock": ("run_id", "guard", "reason"),
}


def record_human_ruling(
    project: ProjectKeys,
    *,
    kind: str,
    fields: Mapping[str, str],
    said: str,
    said_at: str,
    output_path: str | Path,
    tessera: TesseraCli,
) -> VerifiedEnvelope:
    """Record the human's ruling on a repair (a stuck chain or a locked ledger).

    The repair ceremony applies only a ruling the human principal's key signed. The human
    gives the ruling in words; the host records those words with the exact binding the
    ceremony checks, and signs it for them.
    """

    required = RULING_FIELDS.get(kind)
    if required is None:
        raise ProjectInitError(f"unsupported ruling kind: {kind}")
    missing = [name for name in required if not str(fields.get(name, "")).strip()]
    if missing:
        raise ProjectInitError(f"{kind} ruling needs: {', '.join(missing)}")
    if not said.strip():
        raise ProjectInitError("a ruling needs the human's words")
    payload = {
        **{name: str(fields[name]) for name in required},
        "basis": {"source": "user-message", "said": said, "said_at": said_at},
    }
    return tessera.wrap_json(
        payload,
        kind=kind,
        key_path=project.key_paths["human"],
        output_path=output_path,
    )
