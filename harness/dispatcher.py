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
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

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
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_lines(path: pathlib.Path) -> list[str]:
    try:
        return [ln for ln in path.read_text().splitlines() if ln.strip()]
    except OSError:
        return []


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
            cfg = json.loads((root / "run.json").read_text())
        except (OSError, json.JSONDecodeError):
            cfg = {}
        self.repo = cfg.get("repo", ".")
        self.audit_interval_min = int(cfg.get("audit_interval_min") or 45)
        self.promise_window_min = int(cfg.get("promise_window_min") or 10)

    def counts(self) -> tuple[int, int]:
        receipts = len(read_lines(pathlib.Path(".harness/receipts/chain.jsonl")))
        dispatches = len(read_lines(self.root / "dispatches.jsonl"))
        return receipts, dispatches

    # -- record ---------------------------------------------------------------
    def event(self, kind: str, detail: str, wake: bool = False) -> None:
        body = {"ts": now(), "kind": kind, "detail": detail, "wake": wake}
        with open(self.events, "a") as f:
            f.write(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
        print(f"[{body['ts']}] {kind}: {detail[:140]}")
        if wake:
            self.wake_orchestrator(body)

    def wake_orchestrator(self, trigger: dict[str, object]) -> None:
        """Invoked, not resident: hand the agent a projection, not the transcript."""
        wake = self.harness / "orchestrator_wake.sh"
        if wake.exists():
            subprocess.Popen(
                [str(wake), self.run, json.dumps(trigger)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

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
        halt = pathlib.Path(".harness/HALT")
        if halt.exists() and not self.halted:
            self.halted = True
            head = halt.read_text().splitlines()[0] if halt.read_text() else "HALT"
            self.event("halt", head, wake=False)  # deterministic path, no agent in loop
            subprocess.run(
                ["tmux", "display-message", "-t", self.run,
                 "INCIDENT — HALT is set; lanes will not start new work until a human clears it"],
                capture_output=True,
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
            subprocess.run(
                [str(self.harness / "inject.sh"), self.run, "validator",
                 f"[dispatcher] lane '{window}' has been quiet {quiet_min:.0f}m with no "
                 f"idle marker. Tend it or have it write {HEALTHY_IDLE} to "
                 f"{self.root}/lanes/{window}.state"],
                capture_output=True, env={"INJECT_FROM": "dispatcher", "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(pathlib.Path.home())},
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
        # 3. Triumvirate bypass: the target repo moving with no coder lane dispatched.
        _, dispatches = self.counts()
        dirty = sh(["git", "-C", self.repo, "status", "--porcelain"])
        digest = hashlib.sha256(dirty.encode()).hexdigest()
        if dirty.strip() and digest not in self.repo_dirty_seen:
            self.repo_dirty_seen.add(digest)
            has_coder = any('"role":"coder"' in ln or '"role": "coder"' in ln
                            for ln in read_lines(self.root / "dispatches.jsonl"))
            if not has_coder:
                self.event(
                    "triumvirate_bypass_suspected",
                    "target repo working tree is changing but no coder lane was ever "
                    "dispatched — the Validator may be doing lane work itself "
                    "(orchestrate-never-execute, fucked_up §13.5)",
                    wake=True,
                )

    def check_alignment_audit(self) -> None:
        """Periodic strategic audit — the Orchestrator reads the task, design docs,
        and recent activity, and answers: is the run still pointed at what the
        founder asked for? Cadence is run.json data, ratified at ignition."""
        if self.audit_interval_min <= 0:
            return
        if (time.monotonic() - self.last_audit) / 60 >= self.audit_interval_min:
            self.last_audit = time.monotonic()
            self.event(
                "alignment_audit",
                "scheduled strategic audit: check hyper-focus drift, promises vs "
                "receipts, unnecessary waiting, doc currency, cleanup debt "
                "(branches/stashes/worktrees/PRs accruing)",
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
            cfg = self.root / "run.json"
            try:
                if json.loads(cfg.read_text()).get("status") == "closed":
                    self.event("dispatcher_stop", "run closed")
                    return
            except (OSError, json.JSONDecodeError):
                self.event("dispatcher_stop", "run.json unreadable — refusing to babysit")
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
    if not (root / "run.json").exists():
        sys.exit(f"no run.json under {root} — harness/factory.sh creates runs")
    Dispatcher(args.run, root, args.interval).run_loop()


if __name__ == "__main__":
    main()
