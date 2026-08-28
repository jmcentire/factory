"""The generic, target-agnostic native-test Validator executor contract.

The Validator does not know how to build or launch a target's fixtures or protocol processes.
When a target declares an acceptance-test argv in its manifest build ABI
(``build.test_entrypoint``), the Validator does exactly this and nothing target-specific:

  1. materialize the reviewed candidate implementation and the sealed Tester artifact into a
     fresh, writable workspace (regular files only; no symlinks escape it);
  2. expose *only* the declared per-attempt loopback grant (or full deny) for the whole attempt;
  3. run the target's argv — argv-only, never a shell — with the working directory rooted at the
     materialized candidate tree and a closed environment that carries only the declared keys;
  4. reap the entire process group and prove no listener/socket leaked;
  5. retain the argv's declared output directory plus its captured streams as evidence.

The argv is target data, bound by the manifest digest at admission. This module additionally
binds the exact executor *configuration* (contract + argv + materialization layout) into the
acceptance-obligation-catalog's command/configuration/environment digests, so the executor a run
uses is ratified and re-verified like any other Validator execution identity. Stdlib + the
core's canonical digest only; no networking transport, terminal-multiplexer, fixture, or other
target-specific knowledge appears here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from factory_core.manifest import digest_obj

CONTRACT_VERSION = "factory-native-test-executor/1"

# Fixed, generic materialization layout the executor guarantees inside the workspace. The target
# discovers these through the environment variables below; it never receives a Factory path it
# did not ask for, and never the reverse (Factory learns nothing target-specific).
CANDIDATE_SUBDIR = "candidate"
TESTER_SUBDIR = "tester"

# The single ratified acceptance-obligation catalog is materialized verbatim into the workspace
# under this fixed name and exposed through one declared path variable. The target reads the
# ratified per-criterion assertions (triggers -> obligations -> test_assertions) from it; the
# Factory encodes no criterion count and no individual digest, so the same executor scales to any
# number of ratified criteria without a contract change.
ACCEPTANCE_CATALOG_FILENAME = "acceptance-catalog.json"

# The exact environment keys the executor exposes to the argv. Everything else is stripped; there
# is no ambient environment. ``FACTORY_LOOPBACK_*`` are present only when a loopback grant exists.
ENVIRONMENT_KEYS = (
    "HOME",
    "TMPDIR",
    "PATH",
    "LANG",
    "LC_ALL",
    "PYTHONDONTWRITEBYTECODE",
    "FACTORY_OUTPUT_DIR",
    "FACTORY_CANDIDATE_DIR",
    "FACTORY_TEST_DIR",
    "FACTORY_ACCEPTANCE_CATALOG",
    "FACTORY_LOOPBACK_TCP_PORTS",
    "FACTORY_LOOPBACK_UDP_PORTS",
)

NATIVE_TEST_EXECUTOR_CONTRACT: dict[str, object] = {
    "schema_version": CONTRACT_VERSION,
    "launch_mode": "argv-exec/1",  # exact argv handed to the OS; never a shell
    "shell": False,
    "working_directory": "materialized-candidate-workspace/1",
    "runtime_tcb": "target-declared-argv/1",
    "environment": "closed-declared/1",
    "environment_keys": list(ENVIRONMENT_KEYS),
    "artifact_materialization": {
        "schema": "candidate-plus-sealed-tester-plus-catalog/1",
        "candidate_subdir": CANDIDATE_SUBDIR,
        "tester_subdir": TESTER_SUBDIR,
        "acceptance_catalog_file": ACCEPTANCE_CATALOG_FILENAME,
        "regular_files_only": True,
    },
    "output_evidence": "output-dir-plus-captured-streams/1",
    "process_cleanup": "session-group-reap-then-no-leak/1",
    "network": "validator-declared-loopback-or-deny/1",
}


@dataclass(frozen=True)
class NativeTestExecution:
    """The ratifiable identity of one native-test Validator execution.

    ``command_digest`` binds the exact argv and launch mode; ``configuration_digest`` binds the
    full executor contract (materialization layout, cleanup, working directory) with the argv;
    ``environment_digest`` binds the environment contract. All three slot into the existing
    acceptance-obligation-catalog trigger and resume checkpoint, so changing the argv or the
    executor contract forces a fresh ratification.
    """

    test_entrypoint: tuple[str, ...]
    command_digest: str
    configuration_digest: str
    environment_digest: str

    @property
    def digests(self) -> tuple[str, str, str]:
        return (self.command_digest, self.configuration_digest, self.environment_digest)


def _environment_contract() -> dict[str, object]:
    return {
        "schema_version": "factory-native-test-environment/1",
        "ambient_environment": "closed",
        "environment_keys": list(ENVIRONMENT_KEYS),
        "network": "validator-declared-loopback-or-deny",
        "candidate_dir_key": "FACTORY_CANDIDATE_DIR",
        "tester_dir_key": "FACTORY_TEST_DIR",
        "output_dir_key": "FACTORY_OUTPUT_DIR",
        "acceptance_catalog_key": "FACTORY_ACCEPTANCE_CATALOG",
        "loopback_port_keys": ["FACTORY_LOOPBACK_TCP_PORTS", "FACTORY_LOOPBACK_UDP_PORTS"],
    }


def native_test_execution_digests(test_entrypoint: Sequence[str]) -> NativeTestExecution:
    """Bind an exact target-declared argv to a ratifiable Validator execution identity."""

    argv = tuple(str(part) for part in test_entrypoint)
    if not argv or not all(part for part in argv):
        raise ValueError("native test entrypoint must be a non-empty argv of non-empty strings")

    command_digest = digest_obj(
        {
            "schema_version": CONTRACT_VERSION,
            "launch_mode": NATIVE_TEST_EXECUTOR_CONTRACT["launch_mode"],
            "shell": False,
            "argv": list(argv),
        }
    )
    configuration_digest = digest_obj(
        {
            "contract": NATIVE_TEST_EXECUTOR_CONTRACT,
            "argv": list(argv),
        }
    )
    environment_digest = digest_obj(_environment_contract())
    return NativeTestExecution(
        test_entrypoint=argv,
        command_digest=command_digest,
        configuration_digest=configuration_digest,
        environment_digest=environment_digest,
    )
