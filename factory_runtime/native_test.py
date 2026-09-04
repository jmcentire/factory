"""The generic, target-agnostic native-test Validator executor contract.

The Validator does not know how to build or launch a target's fixtures or protocol processes.
When a target declares an acceptance-test interface in its build ABI, the Validator runs the
candidate and the test suite in **two distinct Seatbelt profiles with disjoint artifact roots**,
so segregation of duties holds at execution time, not just at authoring:

  * Profile A (candidate) — the Validator launches and supervises the target-declared
    ``candidate_launch`` argv. It may read only the candidate artifact tree and the declared
    read-only runtime, write only its own output/temp, and bind the declared loopback grant. It
    never sees the Tester artifact.
  * Profile B (test) — the Validator runs the target-declared ``test_entrypoint`` argv (and,
    first, an optional ``readiness_entrypoint``). It may read only the Tester artifact tree (which
    carries the sealed oracle, the acceptance catalog, and any declared readiness implementation)
    and the declared read-only runtime, write only its own output/temp, and connect the declared
    loopback grant. It never sees the candidate artifact tree.

The candidate and the test communicate **only** through the declared loopback endpoints; no
filesystem path crosses between the two profiles. The Factory names no transport and parses no
protocol: readiness is a target-declared argv whose *exit code* is the only signal the Validator
reads, so TCP, UDP, or application-specific readiness all work without Factory protocol knowledge.

Every argv (candidate launch, readiness, test) plus the readiness bounds and the executor contract
are bound into the acceptance-obligation-catalog's command/configuration/environment digests, so
the execution identity a run uses is ratified and re-verified like any other. Stdlib + the core's
canonical digest only; no networking transport, terminal-multiplexer, fixture, or other
target-specific knowledge appears here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from factory_core.manifest import digest_obj

CONTRACT_VERSION = "factory-native-test-executor/3"

# Disjoint materialization roots. The candidate tree and the test tree are never siblings inside a
# single readable directory: each profile is rooted at, and may read, only its own tree.
CANDIDATE_ROOT_NAME = "candidate"
TEST_ROOT_NAME = "test"

# The single ratified acceptance-obligation catalog is materialized verbatim into the *test* root
# under this fixed name and exposed through one declared path variable to the test profile only.
# The candidate profile cannot read it. The target reads its ratified per-criterion assertions
# (triggers -> obligations -> test_assertions) from it; the Factory encodes no criterion count and
# no individual digest, so the same executor scales to any number of ratified criteria.
ACCEPTANCE_CATALOG_FILENAME = "acceptance-catalog.json"

# Default readiness bounds when a target declares a readiness entrypoint without overriding them.
DEFAULT_READINESS_TIMEOUT_SECONDS = 30.0
DEFAULT_READINESS_INTERVAL_SECONDS = 0.5
DEFAULT_READINESS_MAX_ATTEMPTS = 120

# The exact environment keys each profile exposes. Everything else is stripped; there is no ambient
# environment. ``FACTORY_LOOPBACK_*`` are present only when a loopback grant exists.
_COMMON_ENV_KEYS = (
    "HOME",
    "TMPDIR",
    "PATH",
    "LANG",
    "LC_ALL",
    "PYTHONDONTWRITEBYTECODE",
    "FACTORY_OUTPUT_DIR",
    "FACTORY_ICE_HOST",
    "FACTORY_LOOPBACK_TCP_PORTS",
    "FACTORY_LOOPBACK_UDP_PORTS",
)
# Candidate profile: sees its own tree, never the test tree or the catalog.
CANDIDATE_ENV_KEYS = (*_COMMON_ENV_KEYS, "FACTORY_CANDIDATE_DIR")
# Test/readiness profile: sees the test tree and the catalog, never the candidate tree.
TEST_ENV_KEYS = (*_COMMON_ENV_KEYS, "FACTORY_TEST_DIR", "FACTORY_ACCEPTANCE_CATALOG")

NATIVE_TEST_EXECUTOR_CONTRACT: dict[str, object] = {
    "schema_version": CONTRACT_VERSION,
    "launch_mode": "argv-exec/1",  # exact argv handed to the OS; never a shell
    "shell": False,
    "isolation": "two-profile-disjoint-roots/1",
    "cross_artifact_reads": "denied",
    "candidate_profile": {
        "role": "candidate",
        "owner": "validator",  # the Validator launches and supervises the candidate
        "working_directory": "materialized-candidate-root/1",
        "readable": ["candidate-root", "declared-runtime"],
        "writable": ["candidate-output", "candidate-temp"],
        "environment_keys": list(CANDIDATE_ENV_KEYS),
        "network": "validator-declared-loopback-bind-or-deny/1",
    },
    "test_profile": {
        "role": "test",
        "working_directory": "materialized-test-root/1",
        "readable": ["test-root", "acceptance-catalog", "declared-runtime"],
        "writable": ["test-output", "test-temp"],
        "environment_keys": list(TEST_ENV_KEYS),
        "network": "validator-declared-loopback-connect-or-deny/1",
    },
    "readiness": {
        "schema": "optional-declared-readiness-argv/1",
        "profile": "test",  # runs in the test-side profile shape; never reads the candidate tree
        "signal": "exit-code",  # 0 == ready; the Factory parses no protocol
        "retry": "bounded-timeout-interval-attempts/1",
    },
    "endpoint_discovery": "shared-declared-loopback-ports/1",
    "artifact_materialization": {
        "schema": "disjoint-candidate-and-test-roots/1",
        "candidate_root": CANDIDATE_ROOT_NAME,
        "test_root": TEST_ROOT_NAME,
        "acceptance_catalog_file": ACCEPTANCE_CATALOG_FILENAME,
        "regular_files_only": True,
    },
    "output_evidence": "per-profile-output-dir-plus-captured-streams/1",
    "process_cleanup": "supervise-candidate-then-reap-both-groups-no-leak/1",
}


@dataclass(frozen=True)
class NativeTestExecution:
    """The ratifiable identity of one two-profile native-test Validator execution.

    ``command_digest`` binds every exact argv (candidate launch, optional readiness, test);
    ``configuration_digest`` binds the full executor contract with those argvs and the readiness
    bounds; ``environment_digest`` binds the per-profile environment contract. All three slot into
    the existing acceptance-obligation-catalog trigger and resume checkpoint, so changing any argv,
    any readiness bound, or the executor contract forces a fresh ratification.
    """

    candidate_launch: tuple[str, ...]
    test_entrypoint: tuple[str, ...]
    readiness_entrypoint: tuple[str, ...]
    readiness_timeout_seconds: float
    readiness_interval_seconds: float
    readiness_max_attempts: int
    port_bindings: tuple[tuple[int, str], ...]
    command_digest: str
    configuration_digest: str
    environment_digest: str
    # Retained so the executor need not re-parse; not part of the digests beyond the fields above.
    _unused: tuple[()] = field(default=(), repr=False, compare=False)

    @property
    def digests(self) -> tuple[str, str, str]:
        return (self.command_digest, self.configuration_digest, self.environment_digest)

    @property
    def has_readiness(self) -> bool:
        return bool(self.readiness_entrypoint)


# The retained native-execution evidence variant. Its presence as a ledger artifact key is the
# explicit positive discriminator that a VALIDATING/PREVIEW entry was produced by the two-profile
# native executor rather than a frozen validator-runner; the checked projection dispatches on it.
NATIVE_EXECUTION_MANIFEST_SCHEMA = "factory-native-execution-identity/2"
NATIVE_EXECUTION_IDENTITY_KEY = "native-execution-identity"


def native_execution_manifest_document(execution: NativeTestExecution) -> dict[str, object]:
    """The retained, content-addressed native execution identity.

    It embeds the exact target-declared argvs and readiness bounds plus the three ratified
    digests, so a verifier can re-derive the native command/configuration/environment digests from
    the retained bytes alone and fail closed on any tampering or downgrade.
    """

    return {
        "schema_version": NATIVE_EXECUTION_MANIFEST_SCHEMA,
        "candidate_launch": list(execution.candidate_launch),
        "readiness_entrypoint": list(execution.readiness_entrypoint),
        "test_entrypoint": list(execution.test_entrypoint),
        "readiness_timeout_seconds": execution.readiness_timeout_seconds,
        "readiness_interval_seconds": execution.readiness_interval_seconds,
        "readiness_max_attempts": execution.readiness_max_attempts,
        "port_bindings": [
            {"tcp_slot": slot, "target_input": target_input}
            for slot, target_input in execution.port_bindings
        ],
        "command_digest": execution.command_digest,
        "configuration_digest": execution.configuration_digest,
        "environment_digest": execution.environment_digest,
    }


def native_execution_identity_digest(execution: NativeTestExecution) -> str:
    """Content address of the retained native execution manifest — the ledger identity marker."""

    return digest_obj(native_execution_manifest_document(execution))


def _environment_contract() -> dict[str, object]:
    return {
        "schema_version": "factory-native-test-environment/3",
        "ambient_environment": "closed",
        "candidate_environment_keys": list(CANDIDATE_ENV_KEYS),
        "test_environment_keys": list(TEST_ENV_KEYS),
        "network": "validator-declared-loopback-or-deny",
        "candidate_dir_key": "FACTORY_CANDIDATE_DIR",
        "test_dir_key": "FACTORY_TEST_DIR",
        "acceptance_catalog_key": "FACTORY_ACCEPTANCE_CATALOG",
        "output_dir_key": "FACTORY_OUTPUT_DIR",
        "loopback_port_keys": ["FACTORY_LOOPBACK_TCP_PORTS", "FACTORY_LOOPBACK_UDP_PORTS"],
        "target_port_inputs": "declared-loopback-tcp-bindings-only/1",
        "cross_artifact_reads": "denied",
    }


def _clean_argv(parts: Sequence[str], *, label: str, allow_empty: bool) -> tuple[str, ...]:
    argv = tuple(str(part) for part in parts)
    if not argv:
        if allow_empty:
            return ()
        raise ValueError(f"{label} must be a non-empty argv of non-empty strings")
    if not all(part for part in argv):
        raise ValueError(f"{label} must not contain empty argv parts")
    return argv


def native_test_execution_digests(
    candidate_launch: Sequence[str],
    test_entrypoint: Sequence[str],
    *,
    readiness_entrypoint: Sequence[str] = (),
    readiness_timeout_seconds: float = DEFAULT_READINESS_TIMEOUT_SECONDS,
    readiness_interval_seconds: float = DEFAULT_READINESS_INTERVAL_SECONDS,
    readiness_max_attempts: int = DEFAULT_READINESS_MAX_ATTEMPTS,
    port_bindings: Sequence[tuple[int, str]] = (),
) -> NativeTestExecution:
    """Bind the exact candidate/readiness/test argvs to a ratifiable execution identity."""

    candidate = _clean_argv(candidate_launch, label="candidate launch", allow_empty=False)
    test = _clean_argv(test_entrypoint, label="test entrypoint", allow_empty=False)
    readiness = _clean_argv(readiness_entrypoint, label="readiness entrypoint", allow_empty=True)
    normalized_bindings = tuple(sorted((int(slot), str(name)) for slot, name in port_bindings))

    if readiness:
        if not (readiness_timeout_seconds > 0 and readiness_interval_seconds > 0):
            raise ValueError("readiness bounds must be positive when a readiness argv is declared")
        if readiness_max_attempts < 1:
            raise ValueError("readiness max attempts must be at least 1")
        timeout = float(readiness_timeout_seconds)
        interval = float(readiness_interval_seconds)
        attempts = int(readiness_max_attempts)
    else:
        # No readiness argv: the bounds are inert and pinned to zero so they cannot vary the digest.
        timeout = 0.0
        interval = 0.0
        attempts = 0

    readiness_config = {
        "argv": list(readiness),
        "timeout_seconds": timeout,
        "interval_seconds": interval,
        "max_attempts": attempts,
    }
    command_digest = digest_obj(
        {
            "schema_version": CONTRACT_VERSION,
            "launch_mode": NATIVE_TEST_EXECUTOR_CONTRACT["launch_mode"],
            "shell": False,
            "candidate_launch": list(candidate),
            "readiness_entrypoint": list(readiness),
            "test_entrypoint": list(test),
            "port_bindings": [
                {"tcp_slot": slot, "target_input": target_input}
                for slot, target_input in normalized_bindings
            ],
        }
    )
    configuration_digest = digest_obj(
        {
            "contract": NATIVE_TEST_EXECUTOR_CONTRACT,
            "candidate_launch": list(candidate),
            "test_entrypoint": list(test),
            "readiness": readiness_config,
            "port_bindings": [
                {"tcp_slot": slot, "target_input": target_input}
                for slot, target_input in normalized_bindings
            ],
        }
    )
    environment_digest = digest_obj(_environment_contract())
    return NativeTestExecution(
        candidate_launch=candidate,
        test_entrypoint=test,
        readiness_entrypoint=readiness,
        readiness_timeout_seconds=timeout,
        readiness_interval_seconds=interval,
        readiness_max_attempts=attempts,
        port_bindings=normalized_bindings,
        command_digest=command_digest,
        configuration_digest=configuration_digest,
        environment_digest=environment_digest,
    )
