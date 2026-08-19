#!/usr/bin/env python3
"""dispatcher.py — the resident seat that reasons about nothing.

Owns the event stream for one run: watches lane windows, detects the trigger
conditions, records events, and wakes the orchestrator-agent on judgment-shaped
exceptions. It is deterministic, spends no model tokens, and holds zero grant
authority: manifests, registries, and the ledger are files it reads and cannot
write. The stall metric is lane-tending, never repo diffs — answering a blocking
question counts as work; idle-awaiting-handoff is healthy and is never prodded
(fucked_up.md §11.4; the 4-minute scoreboard cron is the named disease).

Wake triggers (orchestrate.md: invoked, not resident): a lane's blocking
question, a judgment-shaped failure class, a HALT, a lease expiry, a confirmed
stall. Cadence is a config value in the run root, changed by humans, never by
an agent mid-run.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Mapping

_HARNESS_MODULE_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_MODULE_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_MODULE_ROOT)
from legacy_abandonment import (  # noqa: E402 - load the adjacent harness module
    LegacyAbandonmentError,
    verify_legacy_abandonment,
)

TRIGGER_PATTERNS: dict[str, str] = {
    "blocking_question": r"BLOCKING QUESTION|blocked on|awaiting (?:the )?Validator",
    "failure_class": (
        r"POLICY_DENIED|AUTHORITY_AMBIGUOUS|ORACLE_DEFECT|BASELINE_CONFLICT|"
        r"SIDE_EFFECT_UNCERTAIN|EVIDENCE_UNAVAILABLE"
    ),
    "spec_defect": r"spec-defect|specification defect",
    "contamination": r"contaminat",
}
HEALTHY_IDLE = "idle-awaiting-handoff"

# Validator failure modes the Orchestrator audits (founder, 2026-08-09): announces
# work and doesn't do it; forgets the triumvirate and codes itself; misattributes
# authority; drifts from the ask. Detection here is DETERMINISTIC (pattern + timer +
# receipt count); judgment stays with the woken orchestrator-agent, which flags to
# the Validator or the human and never gates.
PROMISE_RE = re.compile(
    r"\b(?:I(?:'|’)ll|I will|I am going to|going to|about to|let me(?: now)?)\s+"
    r"(\w+(?:\s+\S+){0,6})",
    re.IGNORECASE,
)
AUTHORITY_RE = re.compile(
    r"founder\s+(?:said|ruled|asked|approved|wants|told)|\[D-\d{4}\]|"
    r"\byou\s+(?:said|asked|told me|approved|ruled)\b",
    re.IGNORECASE,
)


def detect_promises(text: str) -> list[str]:
    """Announced intent in the Validator's own words. A promise is a claim about
    the future; the receipt chain is where it either discharges or doesn't."""
    return [m.group(0).strip() for m in PROMISE_RE.finditer(text)]


def detect_authority_claims(text: str) -> list[str]:
    """Claims of human authority. Each must resolve to a ledger entry or the
    required sentence is 'I cannot find where you said this; I may have invented
    it' — then stop (fucked_up §9.5-9.6)."""
    return [m.group(0).strip() for m in AUTHORITY_RE.finditer(text)]


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def terminate_wake_group(proc: subprocess.Popen[bytes]) -> str:
    """Give a responsive supervisor time to close its client group, then force the wrapper group."""

    try:
        pgid = proc.pid
        os.killpg(pgid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError):
        proc.kill()
        proc.wait()
        return "wrapper-only-fallback"
    # Do not use the wrapper principal's exit as proof that its supervisor and
    # client are gone. The supervisor owns a separate client group and handles
    # TERM; preserve its cleanup grace even when the shell exits immediately.
    time.sleep(1.0)
    proc.poll()  # reap an exited wrapper leader before addressing the remaining group
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    return "process-group-term-kill"


def read_lines(path: pathlib.Path) -> list[str]:
    try:
        return [ln for ln in path.read_text().splitlines() if ln.strip()]
    except OSError:
        return []


def append_jsonl(path: pathlib.Path, body: Mapping[str, object]) -> None:
    """Append one durable record under the same advisory-plane lock protocol.

    `consume_block.sh` holds the blocking file before it takes the events lock. Every producer
    must therefore lock its append as well; otherwise a disposition can race a partial write or
    silently truncate a newly appended event.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_APPEND
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise RuntimeError(f"event sink is not a regular file: {path}")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Dispatcher:
    def __init__(self, run: str, root: pathlib.Path, interval: int) -> None:
        self.run = run
        self.root = root
        self.interval = interval
        self.events = root / "events.jsonl"
        self.tails: dict[str, str] = {}
        self.quiet_since: dict[str, float] = {}
        self.notified: set[str] = set()
        self.harness = pathlib.Path(__file__).resolve().parent
        self.halted = False
        # validator-failure-mode state
        self.promises: dict[str, tuple[float, int, int, str]] = {}
        self.last_audit = time.monotonic()
        self.repo_dirty_seen: set[str] = set()
        try:
            runtime = json.loads((root / "run.json").read_text())
            cfg = json.loads((root / "harness.json").read_text())
        except (OSError, json.JSONDecodeError):
            runtime = {}
            cfg = {}
        target = runtime.get("target_state")
        if not isinstance(target, dict):
            target = {}
        self.source_root = str(target.get("source_root", ""))
        self.workdir = str(target.get("workdir", ""))
        self.runs_root = root.parent
        self.factory_cli = shlex.split(os.environ.get("FACTORY_CLI", "factory"))
        self.audit_interval_min = int(cfg.get("audit_interval_min") or 45)
        self.promise_window_min = int(cfg.get("promise_window_min") or 10)

    def counts(self) -> tuple[int, int]:
        receipts = len(read_lines(self.root.parent.parent / "receipts" / "chain.jsonl"))
        dispatches = len(read_lines(self.root / "dispatches.jsonl"))
        return receipts, dispatches

    # -- record ---------------------------------------------------------------
    def event(self, kind: str, detail: str, wake: bool = False) -> None:
        body = {"ts": now(), "kind": kind, "detail": detail, "wake": wake}
        append_jsonl(self.events, body)
        print(f"[{body['ts']}] {kind}: {detail[:140]}")
        if wake:
            self.wake_orchestrator(body)

    def wake_orchestrator(self, trigger: dict[str, object]) -> None:
        """Invoked, not resident: hand the agent a projection, not the transcript.

        SINGLE-FLIGHT. Without this, one busy minute spawns a seat per event and
        they file contradictory records against each other — batch0 had three live
        at once, one of which filed remediation from a stale snapshot that a sibling
        had already overtaken (upstream finding #5). A wake that arrives while a
        seat is working is COALESCED: the running seat reads events.jsonl and sees
        it anyway, so dropping the duplicate loses no information.

        BOUNDED-TIME LIVENESS (Amend 2.5): the coalescing predicate was
        `proc.poll() is None` with no deadline. A hung wake (claude -p that never
        returns) left poll() None forever, so every later trigger coalesced as "a
        seat is still working" — the orchestrator dead but reported healthy, for
        the whole endgame, while this check said it was fine. The liveness
        detector must watch the PRINCIPAL (the process) against a deadline, not a
        surface (the pane that stays warm). Past the deadline the seat is hung,
        not working: kill it, record the death, let a new wake spawn.
        """
        wake = self.harness / "orchestrator_wake.sh"
        if not wake.exists():
            return
        wake_timeout = float(os.environ.get("WAKE_TIMEOUT", "600"))
        proc = getattr(self, "_wake_proc", None)
        if proc is not None and proc.poll() is None:
            wake_start = getattr(self, "_wake_start", None)
            # Explicit None check, not `or`: a start time of 0.0 is a real value
            # (monotonic origin), and `0.0 or fallback` would discard it and report
            # a live seat as healthy. A proc with no recorded start is suspicious —
            # treat it as already past the deadline so it is killed, not coalesced.
            elapsed = (time.monotonic() - wake_start) if wake_start is not None else wake_timeout
            if elapsed < wake_timeout:
                self.coalesced_wakes = getattr(self, "coalesced_wakes", 0) + 1
                print(f"[wake coalesced] a seat is still working "
                      f"({self.coalesced_wakes} since it started); not spawning a rival")
                return
            # past the deadline — the seat is hung, not working. Kill it, record
            # the death, and fall through to spawn a fresh wake.
            kill_scope = terminate_wake_group(proc)
            kill_detail = (
                f"orchestrator wake hung past {wake_timeout:.0f}s and was killed "
                f"with scope={kill_scope}; no independent check is running"
            )
            self.event("orchestrator_dead",
                       kill_detail, wake=False)
            # A dead orchestrator that is only silently recorded is the opposite of
            # the founder's "get the validator's attention" requirement. Banner it
            # (a non-executing display-message, like HALT/stall) and record the death
            # into wakes/receipts.jsonl — the wake script was killed before it could
            # write its own dead-wake record, so without this the labeled count in
            # status.sh misses the death the dispatcher itself caused.
            self._banner(
                f"INCIDENT — {kill_detail}"
            )
            wd = self.root / "wakes"
            wd.mkdir(parents=True, exist_ok=True)
            with open(wd / "receipts.jsonl", "a") as wf:
                wf.write(json.dumps(
                    {"ts": now(), "status": "ORCHESTRATOR_DID_NOT_RUN",
                     "detail": kill_detail},
                    sort_keys=True, separators=(",", ":")) + "\n")
            self._wake_proc = None
        self._wake_proc = subprocess.Popen(
            [str(wake), self.run, json.dumps(trigger)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._wake_start = time.monotonic()
        self.coalesced_wakes = 0

    # -- observe --------------------------------------------------------------
    def windows(self) -> list[str]:
        out = sh(["tmux", "list-windows", "-t", self.run, "-F", "#{window_name}"])
        return [w for w in out.splitlines() if w in ("validator", "coder", "tester")]

    def capture(self, window: str) -> str:
        return sh(["tmux", "capture-pane", "-pt", f"{self.run}:{window}", "-S", "-60"])

    def lane_state(self, window: str) -> str:
        f = self.root / "lanes" / f"{window}.state"
        return f.read_text().strip() if f.exists() else ""

    # -- checks ---------------------------------------------------------------
    def check_halt(self) -> None:
        halt = self.root.parent.parent / "HALT"
        if halt.exists() and not self.halted:
            self.halted = True
            head = halt.read_text().splitlines()[0] if halt.read_text() else "HALT"
            self.event("halt", head, wake=False)  # deterministic path, no agent in loop
            self._banner(
                "INCIDENT — HALT is set; lanes will not start new work until a human clears it"
            )
        elif not halt.exists():
            self.halted = False

    def check_window(self, window: str, tail: str) -> None:
        prev = self.tails.get(window, "")
        digest = hashlib.sha256(tail.encode()).hexdigest()
        changed = digest != prev
        self.tails[window] = digest
        fresh = tail[len(prev):] if not changed else tail

        for kind, pat in TRIGGER_PATTERNS.items():
            for line in fresh.splitlines()[-25:]:
                if re.search(pat, line, re.IGNORECASE):
                    key = f"{window}:{kind}:{hashlib.sha256(line.encode()).hexdigest()[:12]}"
                    if key not in self.notified:
                        self.notified.add(key)
                        self.event(kind, f"{window}: {line.strip()}", wake=True)

        state = self.lane_state(window)
        if changed or state == HEALTHY_IDLE:
            self.quiet_since.pop(window, None)
            return
        first = self.quiet_since.setdefault(window, time.monotonic())
        quiet_min = (time.monotonic() - first) / 60
        threshold = 15 if window != "validator" else 30
        key = f"{window}:stall:{int(first)}"
        if quiet_min >= threshold and key not in self.notified:
            self.notified.add(key)
            self.event(
                "stall_confirmed",
                f"{window} quiet {quiet_min:.0f}m with no state change and no "
                f"{HEALTHY_IDLE} marker — tending needed",
                wake=True,
            )
            # Attention, not shepherding (founder refinement — the time-kill).
            # The old control injected prose into the validator's pane mid-reasoning
            # (shepherding contaminates; METHODOLOGY.md: -22:1 with reset), and a
            # pane injection is a surface that stays warm after the seat behind it
            # is dead. Attention is a control-plane PRECONDITION the lane cannot run
            # past: a blocking event carrying its class and evidence (the lane and
            # quiet duration), which lane_env enforces on (re)start and the
            # validator consumes between tasks. The display-message is a transient
            # banner — non-executing, not typed into the reasoning stream — so it
            # draws attention without entering it. This extends to every lane, not
            # the validator alone.
            self._block(window, "stall", f"{window} quiet {quiet_min:.0f}m")
            self._banner(
                f"[dispatcher] {window} stalled {quiet_min:.0f}m — blocking event "
                f"written; consume lanes/{window}.blocking before new work"
            )

    def _banner(self, message: str) -> None:
        """Show a transient, non-executing tmux display-message banner. tmux is the
        attention surface, not a control: a missing binary (FileNotFoundError) or a
        dead session must not crash the dispatcher, because the blocking event
        already carries the control. The banner is best-effort decoration on top of
        a deterministic signal; swallow the OSError and keep dispatching."""
        try:
            subprocess.run(
                ["tmux", "display-message", "-t", self.run, message],
                capture_output=True,
            )
        except OSError:
            pass

    def _block(self, lane: str, cls: str, evidence: str) -> None:
        """Write a control-plane blocking event the lane cannot run past. This is
        the orchestrator's attention channel — legitimate control, not shepherding:
        the event carries its class and evidence (a one-line state / receipt id),
        never prose about the validator's process, and lane_env enforces it as a
        precondition rather than injecting it mid-reasoning. Append-only: a lane
        may accumulate several events; lane_env refuses to start while any remain.
        """
        bf = self.root / "lanes" / f"{lane}.blocking"
        bf.parent.mkdir(parents=True, exist_ok=True)
        ts = now()
        payload = {"ts": ts, "class": cls, "evidence": evidence[:200]}
        append_jsonl(bf, payload)
        # Receipt the WRITE so a silent clear is visible by its absence: a
        # blocking_written record with no matching blocking_consumed means the
        # file was rm'd/truncated without consume_block.sh, not consumed.
        append_jsonl(
            self.events,
            {"ts": ts, "kind": "blocking_written", "lane": lane, "event": payload},
        )

    def check_validator_failure_modes(self, fresh: str) -> None:
        """The Orchestrator's charter, detected deterministically, judged on wake:
        announced-but-undone, authority misattribution, triumvirate bypass, drift."""
        # 1. Promises: announced intent must discharge into receipts or dispatches.
        rc, dc = self.counts()
        for line in detect_promises(fresh):
            key = hashlib.sha256(line.encode()).hexdigest()[:16]
            if key not in self.promises and f"promise:{key}" not in self.notified:
                self.promises[key] = (time.monotonic(), rc, dc, line)
        expired = [
            (k, v) for k, v in self.promises.items()
            if (time.monotonic() - v[0]) / 60 >= self.promise_window_min
        ]
        for key, (_, rc0, dc0, line) in expired:
            del self.promises[key]
            self.notified.add(f"promise:{key}")
            if rc == rc0 and dc == dc0:
                self.event(
                    "announced_unreceipted",
                    f"validator announced '{line}' {self.promise_window_min}m ago; "
                    f"no receipt and no dispatch since — saying is not doing",
                    wake=True,
                )
        # 2. Authority claims must resolve to the ledger.
        for claim in detect_authority_claims(fresh):
            key = f"authority:{hashlib.sha256(claim.encode()).hexdigest()[:16]}"
            if key not in self.notified:
                self.notified.add(key)
                self.event(
                    "authority_claim",
                    f"validator asserted authority: '{claim}' — resolve against the "
                    f"directive ledger; unfindable means 'I cannot find where you "
                    f"said this; I may have invented it', then stop",
                    wake=True,
                )
        # 3. Execution truth. Only the run-owned source checkout is this run's subject.
        # Operator branches, worktrees, stashes, and dirt are neither alarms nor cleanup
        # targets. A changed source checkout is instead an exact target-state failure.
        failure_class = "target_state_diverged"
        try:
            verification = subprocess.run(
                [
                    *self.factory_cli,
                    "verify-target-state",
                    "--runs",
                    str(self.runs_root),
                    "--run-id",
                    self.run,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return_code = verification.returncode
            detail = (
                verification.stderr
                or verification.stdout
                or "target-state verification failed"
            ).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return_code = 1
            failure_class = "target_state_verifier_unavailable"
            detail = f"target-state verifier unavailable: {exc}"
        if return_code != 0:
            digest = hashlib.sha256(detail.encode()).hexdigest()
            if digest not in self.repo_dirty_seen:
                self.repo_dirty_seen.add(digest)
                self.event(failure_class, detail[:300], wake=True)
                # Unavailability still blocks: inability to prove the subject is not evidence
                # that it stayed intact. The distinct class avoids reporting PATH or packaging
                # failure as checkout tampering.
                self._block("validator", failure_class, detail[:200])

    def check_alignment_audit(self) -> None:
        """Periodic strategic audit — the Orchestrator reads the task, design docs,
        and recent activity, and answers: is the run still pointed at what the
        founder asked for? Cadence is harness.json data, bound at ignition."""
        if self.audit_interval_min <= 0:
            return
        if (time.monotonic() - self.last_audit) / 60 >= self.audit_interval_min:
            self.last_audit = time.monotonic()
            self.event(
                "alignment_audit",
                "scheduled strategic audit: check hyper-focus drift, promises vs "
                "receipts, unnecessary waiting, doc currency, and run-owned "
                "resource disposition",
                wake=True,
            )

    def check_leases(self) -> None:
        for lease in sorted((self.root / "leases").glob("*.json")):
            try:
                body = json.loads(lease.read_text())
            except json.JSONDecodeError:
                continue
            if body.get("expires", "9999") <= now():
                self.event("lease_expired", f"{lease.name}: {body.get('reason', '')}", wake=True)
                lease.unlink()

    def snapshot_minutes(self) -> None:
        """Raw capture of the human<->Validator window. Non-authoritative by
        construction: [INFERRED] label, ephemeral, an index into the ledger —
        never the record itself."""
        tail = self.capture("validator")
        if not tail.strip():
            return
        day = datetime.date.today().isoformat()
        f = self.root / "minutes" / f"validator-{day}.log"
        if not f.exists():
            f.write_text(
                "[INFERRED] Non-authoritative pane capture. Minutes index the "
                "directive ledger; they are never citable as authority.\n---\n"
            )
        existing = f.read_text()
        fresh = "\n".join(
            ln for ln in tail.splitlines() if ln.strip() and ln not in existing[-8000:]
        )
        if fresh:
            with open(f, "a") as fh:
                fh.write(fresh + "\n")

    def run_loop(self) -> None:
        self.event("dispatcher_start", f"interval={self.interval}s run={self.run}")
        while True:
            cfg = self.root / "harness.json"
            try:
                abandonment = self.root / "legacy-harness-abandonment.json"
                if abandonment.exists() or abandonment.is_symlink():
                    try:
                        verify_legacy_abandonment(cfg, abandonment, run_id=self.run)
                    except LegacyAbandonmentError as exc:
                        self._block(
                            "validator",
                            "invalid_legacy_abandonment",
                            f"legacy abandonment marker refused: {exc}",
                        )
                        self.event(
                            "dispatcher_stop",
                            "invalid legacy abandonment marker refused",
                        )
                        return
                    self.event("dispatcher_stop", "verified legacy harness abandonment")
                    return
                metadata = json.loads(cfg.read_text())
                if metadata.get("schema_version") != "factory-harness/2":
                    self._block(
                        "validator",
                        "legacy_harness",
                        "dispatcher requires factory-harness/2; use the explicit "
                        "human abandonment ceremony before a clean restart",
                    )
                    self.event(
                        "dispatcher_stop",
                        "legacy or unversioned harness refused before monitoring",
                    )
                    return
                if metadata.get("status") == "closed":
                    self.event("dispatcher_stop", "run closed")
                    return
            except (OSError, json.JSONDecodeError):
                self.event("dispatcher_stop", "harness.json unreadable — refusing to babysit")
                return
            self.check_halt()
            for w in self.windows():
                tail = self.capture(w)
                self.check_window(w, tail)
                if w == "validator":
                    self.check_validator_failure_modes(tail)
            self.check_alignment_audit()
            self.check_leases()
            self.snapshot_minutes()
            time.sleep(self.interval)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--interval", type=int, default=30)
    args = p.parse_args()
    root = pathlib.Path(args.root)
    if not (root / "run.json").exists() or not (root / "harness.json").exists():
        sys.exit(f"no checked run.json + harness.json under {root}")
    Dispatcher(args.run, root, args.interval).run_loop()


if __name__ == "__main__":
    main()
