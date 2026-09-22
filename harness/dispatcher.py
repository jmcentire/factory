#!/usr/bin/env python3
"""dispatcher.py — deterministic transport for one Factory run.

The dispatcher observes panes, writes the durable event and activity streams,
and delivers every bounded activity delta plus independent cadence records to a
resident Orchestrator.  It does not decide which conversation bytes deserve
strategic attention.  Pattern checks remain deterministic fail-safe controls;
they are additional signals, not the Orchestrator's visibility boundary.

The stall metric is lane-tending, never repo diffs — answering a blocking
question counts as work; idle-awaiting-handoff is healthy and is never prodded
(fucked_up.md §11.4; the 4-minute scoreboard cron is the named disease).
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
from attention_gate import append_blocking_event  # noqa: E402 - adjacent harness module
from lane_dialogue import (  # noqa: E402 - adjacent harness module
    LaneDialogueError,
    pending_questions,
    record_question,
)
from legacy_abandonment import (  # noqa: E402 - load the adjacent harness module
    LegacyAbandonmentError,
    verify_legacy_abandonment,
)
from orchestrator_channel import (  # noqa: E402 - adjacent harness module
    OrchestratorChannelError,
    activity_highwater,
    append_activity,
)
from watchdog import SignalWatchdog  # noqa: E402 - adjacent harness module

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
FACTORY_QUESTION_RE = re.compile(r"\bFACTORY_QUESTION:\s*(?P<question>\S.*)$")

# Validator failure modes the Orchestrator audits (founder, 2026-08-09): announces
# work and doesn't do it; forgets the role separation and codes itself; misattributes
# authority; drifts from the ask. Detection here is DETERMINISTIC (pattern + timer +
# receipt count); judgment stays with the Orchestrator, whose closed report can
# only block the Validator path or do nothing.
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


def capture_delta(previous: str, current: str) -> str:
    """Return newly visible complete lines from successive bounded tmux captures.

    tmux returns a sliding window rather than a cursor.  Prefer the unchanged
    prefix (ordinary append/redraw), then the largest old-suffix/new-prefix
    overlap (scroll).  Reprocessing the entire capture would recreate an already
    answered ``FACTORY_QUESTION`` whenever any later output changed the pane.
    """

    if not previous:
        return current
    if previous == current:
        return ""
    old = previous.splitlines()
    new = current.splitlines()
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    if prefix:
        return "\n".join(new[prefix:])
    for overlap in range(min(len(old), len(new)), 0, -1):
        if old[-overlap:] == new[:overlap]:
            return "\n".join(new[overlap:])
    return current


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
        self.replay_authority_args = (
            "--genesis",
            os.environ.get("FACTORY_GENESIS", ""),
            "--root-public-key",
            os.environ.get("FACTORY_ROOT_PUBLIC_KEY", ""),
            "--tessera-bin",
            os.environ.get("FACTORY_TESSERA_BIN", "tessera"),
        )
        self.audit_interval_min = int(cfg.get("audit_interval_min") or 45)
        self.promise_window_min = int(cfg.get("promise_window_min") or 10)
        self.orchestrator_mode = str(cfg.get("orchestrator_mode") or "")
        self.last_delivered_cursor = 0
        # Plan §0.4c: the signal-deadline watchdog rides this seam. Knobs come
        # only from the frozen generation blob via the CLI door; passes only
        # from the verified ledger via pass-count — never ambient environment,
        # never a line count.
        self.signal_watchdog = SignalWatchdog(
            root=root,
            runs_root=self.runs_root,
            run_id=run,
            factory_cli=self.factory_cli,
            replay_authority_args=self.replay_authority_args,
            harness_dir=self.harness,
        )

    def counts(self) -> tuple[int, int]:
        """Display metrics only (the pass unit lives at the CLI pass-count door).

        4.2: the receipt count is unique-VALID-id counting, not a line count —
        a malformed row or duplicate id surfaces as an emitted event instead of
        silently inflating the number; full digest verification stays on the
        promotion seam's mandatory path.
        """
        receipt_ids: set[str] = set()
        malformed = 0
        for line in read_lines(self.root.parent.parent / "receipts" / "chain.jsonl"):
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                malformed += 1
                continue
            identifier = str(row.get("id", "")) if isinstance(row, dict) else ""
            if not identifier or identifier in receipt_ids:
                malformed += 1
                continue
            receipt_ids.add(identifier)
        if malformed and "chain_rows_unverifiable" not in self.notified:
            self.notified.add("chain_rows_unverifiable")
            self.event(
                "chain_rows_unverifiable",
                f"{malformed} chain row(s) malformed or duplicate — count reflects "
                f"unique valid ids only; the promotion seam verifies digests",
            )
        dispatches = len(read_lines(self.root / "dispatches.jsonl"))
        return len(receipt_ids), dispatches

    # -- record ---------------------------------------------------------------
    def event(self, kind: str, detail: str, wake: bool = False) -> None:
        body = {"ts": now(), "kind": kind, "detail": detail, "wake": wake}
        append_jsonl(self.events, body)
        print(f"[{body['ts']}] {kind}: {detail[:140]}")
        if wake:
            self.wake_orchestrator(body)

    def wake_orchestrator(self, trigger: dict[str, object]) -> None:
        """Route a deterministic signal into the resident Orchestrator's journal.

        Every run has a resident Orchestrator (founder ruling 2026-09-21: exactly
        four roles; the Orchestrator is always running and watches every lane).
        A signal is appended to the same complete activity journal as ordinary
        pane changes, and delivery happens once after the observation pass.

        There is no one-shot substitute. A run without a resident Orchestrator
        is refused: the signal becomes a blocking event on the Validator, never
        a throwaway headless session that answers once and exits.
        """
        if self.orchestrator_mode == "resident-monitoring":
            try:
                append_activity(
                    self.root,
                    kind="deterministic_signal",
                    source="dispatcher",
                    detail=f"deterministic signal: {trigger.get('kind', 'unknown')}",
                    snapshot=json.dumps(trigger, sort_keys=True, separators=(",", ":")),
                )
            except OrchestratorChannelError as exc:
                self._record_orchestrator_transport_failure(str(exc))
            return

        self._refuse_without_resident_orchestrator(
            f"deterministic signal {trigger.get('kind', 'unknown')} arrived"
        )

    def _refuse_without_resident_orchestrator(self, context: str) -> None:
        """Block the run: no resident Orchestrator means nothing is watching."""

        detail = (
            f"{context} but this run has no resident Orchestrator "
            f"(orchestrator_mode={self.orchestrator_mode or 'missing'}); every Factory "
            "run requires one and there is no orchestrator-less mode"
        )
        key = f"orchestrator-not-resident:{self.orchestrator_mode}"
        if key not in self.notified:
            self.notified.add(key)
            self.event("orchestrator_not_resident", detail[:300], wake=False)
            self._block("validator", "orchestrator_not_resident", detail[:200])
            self._banner(f"INCIDENT — {detail[:160]}")

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
            if head.startswith("ORCHESTRATOR HALT"):
                # The Orchestrator stopped the Validator. Enforce it here too, so a
                # failed immediate kill in the channel is never an escape.
                sh(["tmux", "kill-window", "-t", f"{self.run}:validator"])
                self.event(
                    "orchestrator_halt_enforced",
                    "validator window killed; HALT stands until a human clears it "
                    "and re-seats the Validator",
                    wake=False,
                )
            self._banner(
                "INCIDENT — HALT is set; lanes will not start new work until a human clears it"
            )
        elif not halt.exists():
            self.halted = False

    def check_window(self, window: str, tail: str) -> bool:
        previous = self.tails.get(window, "")
        changed = tail != previous
        fresh = capture_delta(previous, tail)
        self.tails[window] = tail

        if window in {"coder", "tester"}:
            for line in fresh.splitlines()[-25:]:
                matched = FACTORY_QUESTION_RE.search(line)
                if matched is None:
                    continue
                try:
                    question, created = record_question(
                        self.root,
                        window,
                        matched.group("question").strip(),
                    )
                except (LaneDialogueError, OSError) as exc:
                    self._record_orchestrator_transport_failure(str(exc))
                    continue
                notification = f"lane-question:{question['question_id']}"
                if created or notification not in self.notified:
                    self.notified.add(notification)
                    self.event(
                        "lane_question",
                        f"{question['question_id']} from {window}: {question['text']}",
                        wake=True,
                    )

        for kind, pat in TRIGGER_PATTERNS.items():
            for line in fresh.splitlines()[-25:]:
                if re.search(pat, line, re.IGNORECASE):
                    key = f"{window}:{kind}:{hashlib.sha256(line.encode()).hexdigest()[:12]}"
                    if key not in self.notified:
                        self.notified.add(key)
                        self.event(kind, f"{window}: {line.strip()}", wake=True)

        state = self.lane_state(window)
        if window in {"coder", "tester"}:
            try:
                waiting_on_validator = bool(pending_questions(self.root, window))
            except (LaneDialogueError, OSError) as exc:
                self._record_orchestrator_transport_failure(str(exc))
                waiting_on_validator = False
            if waiting_on_validator:
                # This is a known wait with a named resolver, not unknown liveness.
                # The question gate already blocks transition until delivery.
                self.quiet_since.pop(window, None)
                return changed
        if changed or state == HEALTHY_IDLE:
            self.quiet_since.pop(window, None)
            return changed
        first = self.quiet_since.setdefault(window, time.monotonic())
        quiet_min = (time.monotonic() - first) / 60
        threshold = 15 if window != "validator" else 30
        key = f"{window}:stall:{int(first)}"
        if quiet_min >= threshold and key not in self.notified:
            self.notified.add(key)
            if self.orchestrator_mode == "resident-monitoring":
                self.event(
                    "liveness_unknown",
                    f"{window} has produced no changed capture for {quiet_min:.0f}m; "
                    "silence does not distinguish reasoning from an I/O wait. Validator or "
                    "Orchestrator must inspect tmux and use tmux_lane_message.sh status for "
                    "a typed Codex-session probe",
                    wake=True,
                )
                return changed
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
        return changed

    def record_pane_delta(self, window: str, tail: str) -> None:
        """Retain every changed pane snapshot; split only for transport ceilings.

        The split is byte-preserving and carries no semantic selection.  tmux
        remains an inferred coordination surface, so each row names the exact
        captured bytes and their digest rather than pretending to be authority.
        """

        raw = tail.encode("utf-8")
        chunks: list[str] = []
        while raw:
            end = min(len(raw), 48 * 1024)
            while end and (raw[end : end + 1] and raw[end] & 0xC0 == 0x80):
                end -= 1
            if end == 0:
                raise OrchestratorChannelError("pane capture cannot be split as UTF-8")
            chunks.append(raw[:end].decode("utf-8"))
            raw = raw[end:]
        if not chunks:
            chunks = [""]
        total = len(chunks)
        for index, chunk in enumerate(chunks, 1):
            append_activity(
                self.root,
                kind="pane_delta",
                source=window,
                detail=f"complete bounded pane snapshot {index}/{total} for {window}",
                snapshot=chunk,
            )

    def record_cadence(self) -> None:
        append_activity(
            self.root,
            kind="cadence",
            source="dispatcher",
            detail=(
                "independent strategic cadence: reconstruct the user's ultimate goal, "
                "classify recent input, test direction and consequences, inspect side effects, "
                "and audit rule adherence"
            ),
        )

    def _record_orchestrator_transport_failure(self, detail: str) -> None:
        key = f"orchestrator-transport:{hashlib.sha256(detail.encode()).hexdigest()}"
        if key in self.notified:
            return
        self.notified.add(key)
        self.event("orchestrator_transport_failed", detail[:300], wake=False)
        self._block("validator", "orchestrator_transport_failed", detail[:200])
        self._banner(f"INCIDENT — resident Orchestrator transport failed: {detail[:160]}")

    def deliver_pending_activity(self) -> None:
        """Notify the Orchestrator of the complete cursor range, without judging it."""

        if self.orchestrator_mode != "resident-monitoring":
            return
        try:
            cursor = activity_highwater(self.root)
        except OrchestratorChannelError as exc:
            self._record_orchestrator_transport_failure(str(exc))
            return
        if cursor <= self.last_delivered_cursor:
            return
        start = self.last_delivered_cursor + 1
        report = f"orchestrator/assessment-{cursor}.json"
        message = (
            f"FACTORY_ACTIVITY cursors={start}..{cursor}. Consume EVERY unassessed record "
            "in that range from orchestrator/activity.jsonl. Follow orchestrator/ROLE.md's "
            "complete monitoring loop, update orchestrator/OUTSTANDING-WORK.md, write "
            f"assessment/3 to {report}, then submit: python3 "
            f"orchestrator/bin/orchestrator_channel.py report --root . --input {report}. "
            "Decide block, halt, or no-op; never grant or close."
        )
        environment = dict(os.environ)
        environment.update(
            {
                "INJECT_FROM": "dispatcher",
                "HARNESS_RUN_ROOT": str(self.root),
                "INJECT_SUBMIT_DELAY": "0.1",
            }
        )
        try:
            delivered = subprocess.run(
                [str(self.harness / "inject.sh"), self.run, "orchestrator", message],
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._record_orchestrator_transport_failure(str(exc))
            return
        if delivered.returncode != 0:
            detail = (delivered.stderr or delivered.stdout or "delivery failed").strip()
            self._record_orchestrator_transport_failure(detail)
            return
        self.last_delivered_cursor = cursor
        self.event(
            "orchestrator_activity_delivered",
            f"delivered every activity record through cursor {cursor}",
            wake=False,
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
        ts = now()
        payload = {"ts": ts, "class": cls, "evidence": evidence[:200]}
        # The shared attention lock orders this append against disposition and dispatch
        # admission. The paired write receipt remains inside that serialized producer call.
        append_blocking_event(self.root, lane, payload)

    def check_validator_failure_modes(self, fresh: str) -> None:
        """The Orchestrator's charter, detected deterministically, judged on wake:
        announced-but-undone, authority misattribution, role-separation bypass, drift."""
        # 1. Promises: announced intent must discharge into receipts or dispatches.
        rc, dc = self.counts()
        for line in detect_promises(fresh):
            key = hashlib.sha256(line.encode()).hexdigest()[:16]
            if key not in self.promises and f"promise:{key}" not in self.notified:
                self.promises[key] = (time.monotonic(), rc, dc, line)
        expired = [
            (k, v)
            for k, v in self.promises.items()
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
                    *self.replay_authority_args,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return_code = verification.returncode
            detail = (
                verification.stderr or verification.stdout or "target-state verification failed"
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
        """Append an independent cadence record; the Orchestrator supplies judgment."""
        if self.audit_interval_min <= 0:
            return
        if (time.monotonic() - self.last_audit) / 60 >= self.audit_interval_min:
            self.last_audit = time.monotonic()
            if self.orchestrator_mode == "resident-monitoring":
                try:
                    self.record_cadence()
                except OrchestratorChannelError as exc:
                    self._record_orchestrator_transport_failure(str(exc))
            else:
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
        if self.orchestrator_mode == "resident-monitoring":
            try:
                self.record_cadence()
            except OrchestratorChannelError as exc:
                self._record_orchestrator_transport_failure(str(exc))
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
                if metadata.get("status") in {"closed", "no"}:
                    # Round-3 carryover: a host-recorded terminal NO is terminal —
                    # a NO run is never babysat forever.
                    self.event("dispatcher_stop", f"run {metadata.get('status')}")
                    return
            except (OSError, json.JSONDecodeError):
                self.event("dispatcher_stop", "harness.json unreadable — refusing to babysit")
                return
            self.check_halt()
            for w in self.windows():
                tail = self.capture(w)
                changed = self.check_window(w, tail)
                if self.orchestrator_mode == "resident-monitoring" and changed:
                    try:
                        self.record_pane_delta(w, tail)
                    except OrchestratorChannelError as exc:
                        self._record_orchestrator_transport_failure(str(exc))
                if w == "validator":
                    self.check_validator_failure_modes(tail)
            self.check_alignment_audit()
            self.check_leases()
            self.signal_watchdog.check(self.event, self._block)
            self.snapshot_minutes()
            self.deliver_pending_activity()
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
    dispatcher = Dispatcher(args.run, root, args.interval)
    if dispatcher.orchestrator_mode != "resident-monitoring":
        dispatcher._refuse_without_resident_orchestrator("dispatcher start requested")
        sys.exit(
            "dispatcher refused: run has no resident Orchestrator "
            "(harness.json orchestrator_mode must be resident-monitoring)"
        )
    dispatcher.run_loop()


if __name__ == "__main__":
    main()
