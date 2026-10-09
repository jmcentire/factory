#!/usr/bin/env python3
"""lane_budget — the run's objective-budget reservation ledger, one writer for every lane path.

``harness.json`` carries the run's objective budget (``factory.sh --budget <usd>``). Every
metered lane start reserves its ceiling in ``budget-reservations.jsonl`` before it starts:
``dispatch_lane.sh`` reserves the qualified runner's ``max_cost_microusd``, ``tmux_lane.sh``
reserves a metered round's spend cap. The ledger is a hash chain; a start whose ceiling would
take the sum of reservations past the objective budget is refused, and a run with no objective
budget cannot start a metered lane at all.

  reserve   print ``sha256:<row hash>`` of the reservation (an exact replay of an existing
            reservation id returns the existing row); exit non-zero with the reason otherwise.

Stdlib only, and no imports from this package: the shell entry points call it by path.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import fcntl
import hashlib
import hmac
import json
import os
import pathlib
import stat
import string
import sys

SCHEMA = "factory-budget-reservation/1"


class BudgetRefused(RuntimeError):
    pass


def _canonical(row: dict[str, object]) -> bytes:
    return json.dumps(row, sort_keys=True, separators=(",", ":")).encode()


def objective_budget_microusd(metadata_path: pathlib.Path) -> int:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    budget = metadata.get("budget_usd")
    if budget is None:
        raise BudgetRefused("model dispatch requires an explicit objective budget")
    try:
        value = decimal.Decimal(str(budget)) * decimal.Decimal(1_000_000)
        if value != value.to_integral_value():
            raise decimal.InvalidOperation
        microusd = int(value)
    except (decimal.InvalidOperation, ValueError):
        raise BudgetRefused("objective budget is not exactly representable in microusd") from None
    if microusd <= 0:
        raise BudgetRefused("objective budget must be positive")
    return microusd


def reserve(
    *,
    metadata_path: pathlib.Path,
    ledger_path: pathlib.Path,
    requested: int,
    run: str,
    role: str,
    generation: int,
    reservation_id: str,
    recovery: bool = False,
) -> str:
    budget_microusd = objective_budget_microusd(metadata_path)
    if requested <= 0:
        raise BudgetRefused("dispatch requires a positive runner cost ceiling")
    if ledger_path.is_symlink():
        raise BudgetRefused("budget reservation ledger may not be a symlink")
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    fd = os.open(ledger_path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise BudgetRefused("budget reservation ledger must be regular")
    with os.fdopen(fd, "r+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        rows = [json.loads(line) for line in stream if line.strip()]
        previous = "0" * 64
        reserved = 0
        prior = None
        for number, row in enumerate(rows, 1):
            if (
                not isinstance(row, dict)
                or row.get("run") != run
                or row.get("prev_hash") != previous
            ):
                raise BudgetRefused(f"budget reservation chain mismatch at row {number}")
            supplied = row.get("hash")
            if not isinstance(supplied, str) or len(supplied) != 64 or any(
                character not in string.hexdigits for character in supplied
            ):
                raise BudgetRefused(f"budget reservation hash invalid at row {number}")
            unsigned = dict(row)
            del unsigned["hash"]
            if not hmac.compare_digest(supplied, hashlib.sha256(_canonical(unsigned)).hexdigest()):
                raise BudgetRefused(f"budget reservation content mismatch at row {number}")
            value = row.get("reserved_max_cost_microusd")
            if not isinstance(value, int) or value <= 0:
                raise BudgetRefused("budget reservation has no positive ceiling")
            reserved += value
            if row.get("reservation_id") == reservation_id:
                prior = row
            previous = supplied
        if prior is not None:
            if (
                prior.get("role") != role
                or prior.get("generation") != generation
                or prior.get("reserved_max_cost_microusd") != requested
            ):
                raise BudgetRefused("budget reservation id was replayed with different scope")
            return "sha256:" + str(prior["hash"])
        if recovery:
            raise BudgetRefused("orphan recovery has no prior objective budget reservation")
        if reserved + requested > budget_microusd:
            raise BudgetRefused("runner reservations exceed the objective budget")
        body: dict[str, object] = {
            "schema_version": SCHEMA,
            "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "run": run,
            "generation": generation,
            "role": role,
            "reservation_id": reservation_id,
            "reserved_max_cost_microusd": requested,
            "objective_budget_microusd": budget_microusd,
            "prev_hash": previous,
        }
        body["hash"] = hashlib.sha256(_canonical(body)).hexdigest()
        stream.seek(0, os.SEEK_END)
        stream.write(_canonical(body).decode() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(ledger_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return "sha256:" + str(body["hash"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="objective-budget reservation ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("reserve")
    command.add_argument("--metadata", type=pathlib.Path, required=True)
    command.add_argument("--ledger", type=pathlib.Path, required=True)
    command.add_argument("--requested-microusd", type=int, required=True)
    command.add_argument("--run", required=True)
    command.add_argument("--role", required=True)
    command.add_argument("--generation", type=int, required=True)
    command.add_argument("--reservation-id", required=True)
    command.add_argument("--recovery", choices=("0", "1"), default="0")
    arguments = parser.parse_args(argv)
    try:
        print(
            reserve(
                metadata_path=arguments.metadata,
                ledger_path=arguments.ledger,
                requested=arguments.requested_microusd,
                run=arguments.run,
                role=arguments.role,
                generation=arguments.generation,
                reservation_id=arguments.reservation_id,
                recovery=arguments.recovery == "1",
            )
        )
        return 0
    except (BudgetRefused, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
