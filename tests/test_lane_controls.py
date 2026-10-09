"""Forced-negative drills for the tmux lane launch controls (lessons-msg-r2-2026-10.md §1-§4, §6).

The msg-r2 run bypassed the harness with a run-local launcher, so none of these controls ran:
an author agent sat on an interactive login, a metered Tester model looped for hours with no
cap, finished lanes sat idle, and Tester briefs never carried the testing standard. Each
control here is exercised in both directions, through the real scripts where it is a launch
decision and through the real watcher where it is a watch decision.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import lane_agent, lane_watchdog, testing_standard
from tests.test_harness_scripts import (
    HARNESS,
    METERED_CAPS,
    _dispatch_env,
    _ollama_stub,
    _write_profiles,
    dispatch_success_fixture,
    execution_truth_fixture,
    factory_ignition_env,
    read_chain,
    record_passing_qualification,
    run,
)

REPO = HARNESS.parent
CODEX_HELP = (
    "--ignore-user-config --ignore-rules --strict-config --json --thread --no-alt-screen "
    "--no-daemon --add-dir --model"
)


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _codex_stub(directory: Path, *, signed_in: bool) -> None:
    _stub(directory, "codex", (
        'if [ "${1:-}" = "--version" ]; then echo "codex-cli 0.162.0-test"; exit 0; fi\n'
        'if [ "${1:-}" = login ] && [ "${2:-}" = status ]; then '
        + ('echo "Logged in using ChatGPT"; exit 0; fi\n' if signed_in
           else 'echo "Not logged in"; exit 1; fi\n')
        + f'if [[ "$*" == *"--help"* ]]; then echo "{CODEX_HELP}"; exit 0; fi\n'
        "exit 0\n"
    ))


def _cursor_stub(directory: Path, *, signed_in: bool) -> None:
    state = (
        '{"status":"authenticated","isAuthenticated":true}' if signed_in
        else '{"status":"unauthenticated","isAuthenticated":false}'
    )
    _stub(directory, "agent", (
        'case "${1:-}" in\n'
        '  --version) echo "2026.10.01-test" ;;\n'
        '  --help) echo "-p, --print --model --force --trust --workspace" ;;\n'
        f"  status) echo '{state}' ;;\n"
        "esac\n"
        "exit 0\n"
    ))


class Lanes:
    """One ignited run and a launcher for its lanes."""

    def __init__(self, tmp_path: Path, *, budget: str | None = None,
                 profiles: Path | None = None) -> None:
        task = "Exercise the lane launch controls."
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.operator, self.root, _ = execution_truth_fixture(
            tmp_path, task=task, harness_status=None
        )
        env, self.tmux_log = factory_ignition_env(tmp_path, self.root)
        if profiles is not None:
            env["FACTORY_PROFILES"] = str(profiles)
        self.env = env
        self.stub = Path(env["PATH"].split(os.pathsep)[0])
        ignite = ["bash", str(HARNESS / "factory.sh"), "r1", task, "--runs", str(self.root.parent)]
        if budget is not None:
            ignite += ["--budget", budget]
        ignited = run(ignite, self.operator, env)
        assert ignited.returncode == 0, ignited.stdout + ignited.stderr
        self.prompt = tmp_path / "brief.txt"
        self.prompt.write_text("Write the three tests the brief names.\n", encoding="utf-8")

    def clone(self, name: str) -> Path:
        lane = self.tmp / f"clone-{name}"
        if not lane.exists():
            subprocess.run(["git", "init", "-q", "-b", "main", str(lane)], check=True)
        return lane

    def lane(self, role: str, *extra: str, action: str = "launch", repo: Path | None = None,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return run(
            ["bash", str(HARNESS / "tmux_lane.sh"), "r1", role, action,
             "--repo", str(repo or self.clone(role)), "--prompt", str(self.prompt),
             "--runs", str(self.root.parent), *extra],
            self.operator,
            {**self.env, **(env or {})},
        )

    def windows(self, name: str) -> list[str]:
        if not self.tmux_log.exists():
            return []
        return [line for line in self.tmux_log.read_text().splitlines() if f"-n {name} " in line]

    def refusals(self) -> list[dict[str, object]]:
        path = self.root / "events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row.get("kind") == "refusal-tmux-lane"]


# -- 1. agents and invocation shape ---------------------------------------------------------


def test_cursor_agent_lane_runs_the_print_mode_invocation_under_the_lane_wrapper(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    _cursor_stub(lanes.stub, signed_in=True)
    unnamed = lanes.lane("coder", "--agent", "cursor-agent")
    assert unnamed.returncode == 64 and "cursor-agent needs a named model" in unnamed.stderr

    launched = lanes.lane("coder", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    lane = lanes.clone("coder").resolve()
    [call] = lanes.windows("coder")
    assert f"-- agent -p --model grok-4.7-xhigh --force --trust --workspace {lane}" in call
    assert "env -i" in call and "lane_agent.py run --slot coder" in call
    assert "--prompt-arg" in call and "coder-prompt.txt" in call
    [watcher] = lanes.windows("watch-coder")
    assert "lane_watchdog.py lane --root" in watcher and "--slot coder" in watcher
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert row["status"] == "active" and row["agent"] == "cursor-agent"
    assert row["model"] == "grok-4.7-xhigh" and row["prompt_via"] == "argv"
    assert row["cli_contract"] == "cursor-agent-print-v1"
    assert row["auth_preflight"] == "cursor-agent-status"
    assert row["watch"] == {"read_allowance_s": 900, "stall_window_s": 1200, "poll_s": 30}

    # A cursor-agent lane has no Codex thread to resume: messaging it says to launch a round.
    message = run(
        ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator", "coder", "status",
         "--runs", str(lanes.root.parent)],
        lanes.operator, lanes.env,
    )
    assert message.returncode == 70 and "new --round" in message.stderr


def test_interactive_codex_runs_the_tui_without_its_daemon(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    _codex_stub(lanes.stub, signed_in=True)
    launched = lanes.lane("coder", "--agent", "codex-interactive")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    [call] = lanes.windows("coder")
    assert "--terminal --prompt-arg" in call
    assert "-- codex --no-daemon --no-alt-screen --strict-config --ask-for-approval never" in call
    assert "never exec" not in call and "codex_lane_session" not in call


def test_codex_exec_stays_the_default_author_lane(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    assert lanes.lane("coder").returncode == 0
    [call] = lanes.windows("coder")
    assert "lane_agent.py run --slot coder" in call
    assert "codex_lane_session.py" in call and "codex --ask-for-approval never exec" in call
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert row["agent"] == "codex" and row["metered"] is False
    assert row["caps"]["wall_cap_s"] is None


# -- 2. auth preflight ----------------------------------------------------------------------


def test_signed_out_codex_is_refused_with_the_exact_fix_before_anything_starts(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    _codex_stub(lanes.stub, signed_in=False)
    refused = lanes.lane("coder")
    assert refused.returncode == 77, refused.stdout + refused.stderr
    assert "AUTH: codex cannot start non-interactively" in refused.stderr
    assert "Not logged in" in refused.stderr
    assert "remediation: CODEX_HOME=" in refused.stderr
    assert "codex login --device-auth" in refused.stderr
    assert lanes.windows("coder") == [] and lanes.windows("watch-coder") == []
    assert not (lanes.root / "tmux-lanes" / "coder-prompt.txt").exists()
    assert [row["exit_code"] for row in lanes.refusals()] == [77]


def test_signed_out_cursor_agent_is_refused(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    _cursor_stub(lanes.stub, signed_in=False)
    refused = lanes.lane("tester", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh")
    assert refused.returncode == 77
    assert "cursor-agent is not signed in" in refused.stderr
    assert "remediation: agent login" in refused.stderr
    assert lanes.windows("tester") == []


def test_an_auth_check_that_waits_for_input_is_a_login_screen() -> None:
    def waits(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["stdin"] is subprocess.DEVNULL
        raise subprocess.TimeoutExpired("codex", 1.0)

    with pytest.raises(lane_agent.PreflightRefused, match="waiting for an interactive step"):
        lane_agent.preflight("codex", environ={"CODEX_HOME": "/h"}, timeout=1.0, runner=waits)

    def signed_in(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "Logged in", "")

    # The msg-r2 Coder's TUI could not bind its socket under a long CODEX_HOME and fell back to
    # its login screen: an interactive lane is refused before that can happen.
    long_home = "/" + "x" * 100
    with pytest.raises(lane_agent.PreflightRefused, match="login screen") as refused:
        lane_agent.preflight("codex-interactive", environ={"CODEX_HOME": long_home},
                             runner=signed_in)
    assert "--agent codex" in refused.value.remediation
    assert lane_agent.preflight("codex-interactive", environ={"CODEX_HOME": "/h"},
                                runner=signed_in) == "codex-login-status"


# -- 3. spend caps for metered agents -------------------------------------------------------


def test_a_metered_agent_refuses_to_launch_without_a_cap_budget_and_qualification(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path / "unbudgeted")
    _ollama_stub(lanes.stub, available=True)
    ollama = ("--agent", "codex-ollama", "--model", "glm-test:cloud")
    no_cap = lanes.lane("coder", *ollama)
    assert no_cap.returncode == 64
    assert "is metered; refusing to launch without a spend cap" in no_cap.stderr
    assert "--spend-cap-usd" in no_cap.stderr and "--usd-per-hour" in no_cap.stderr
    no_rate = lanes.lane("coder", *ollama, "--spend-cap-usd", "5")
    assert no_rate.returncode == 64
    record_passing_qualification(lanes.root.parent, "coder", "codex-ollama", "glm-test:cloud")
    no_budget = lanes.lane("coder", *ollama, *METERED_CAPS)
    assert no_budget.returncode == 70 and "BUDGET" in no_budget.stderr
    assert "explicit objective budget" in no_budget.stderr and "--budget" in no_budget.stderr
    assert lanes.windows("coder") == []

    lanes = Lanes(tmp_path / "budgeted", budget="50")
    _ollama_stub(lanes.stub, available=True)
    unqualified = lanes.lane("coder", *ollama, *METERED_CAPS)
    assert unqualified.returncode == 77 and "UNQUALIFIED" in unqualified.stderr
    assert "tmux_lane.sh r1 coder qualify" in unqualified.stderr
    record_passing_qualification(lanes.root.parent, "coder", "codex-ollama", "glm-test:cloud")
    launched = lanes.lane("coder", *ollama, *METERED_CAPS, "--wall-cap-minutes", "120")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    # $5 at $2/h is 2.5 h; the declared 120-minute wall cap is tighter and wins.
    assert row["metered"] is True and row["caps"]["wall_cap_s"] == 7200
    assert row["caps"]["spend_cap_usd"] == "5" and row["caps"]["usd_per_hour"] == "2"
    assert row["caps"]["enforcement"] == "wall-clock-proxy"
    [reservation] = read_chain(lanes.root / "budget-reservations.jsonl")
    assert reservation["reservation_id"] == "r1:tmux:coder"
    assert reservation["reserved_max_cost_microusd"] == 5_000_000
    assert row["caps"]["budget_reservation"] == "sha256:" + reservation["hash"]


def test_a_binding_the_profile_marks_metered_needs_a_cap(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles.json"
    _write_profiles(profiles, validator="codex:gpt-val", orchestrator="agy:gemini-test",
                    coder="codex:gpt-coder", tester="cursor-agent:grok-4.7-xhigh")
    document = json.loads(profiles.read_text())
    document["profiles"]["team"]["roles"]["tester"]["metered"] = True
    profiles.write_text(json.dumps(document))
    lanes = Lanes(tmp_path, profiles=profiles)
    _cursor_stub(lanes.stub, signed_in=True)
    refused = lanes.lane("tester")
    assert refused.returncode == 64 and "cursor-agent:grok-4.7-xhigh is metered" in refused.stderr
    assert lanes.windows("tester") == []


# -- 4. the watchdog: stall stop, cap stop, completion wake ---------------------------------


T0 = 1_791_000_000.0


class Clock:
    def __init__(self) -> None:
        self.value = T0

    def __call__(self) -> float:
        return self.value


def _commit(repo: Path, at: float, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    stamp = f"@{int(at)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), "add", name], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", name], check=True, env=env)


class Watched:
    def __init__(self, tmp_path: Path, *, caps: dict[str, object] | None = None,
                 qualify: dict[str, object] | None = None) -> None:
        self.root = tmp_path / "run"
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.log = self.lanes / "tester-lane.log"
        self.exits = self.lanes / "tester-exits.jsonl"
        row = {
            "status": "active", "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC)
            .isoformat(), "run_id": "r1", "role": "tester", "lane": "tester", "slot": "tester",
            "agent": "codex-ollama", "model": "glm-test:cloud", "terminal": False,
            "repository": str(self.repo), "lane_log": str(self.log),
            "lane_exits": str(self.exits), "tmux_target": "r1:tester",
            "watch": {"read_allowance_s": 900, "stall_window_s": 1200, "poll_s": 30},
            "caps": caps or {}, "qualify": qualify,
        }
        (self.lanes / "tester-launch.jsonl").write_text(json.dumps(row) + "\n")
        self.clock = Clock()
        self.stops: list[str] = []
        self.said: list[str] = []
        self.watch = lane_watchdog.LaneWatch(
            root=self.root, slot="tester", now=self.clock, stop=self._stop,
            tmux=lambda *a: subprocess.CompletedProcess(a, 0, "", ""), echo=self.said.append,
        )
        self.watch.arm()

    def _stop(self, target: str) -> str:
        self.stops.append(target)
        return f"stopped {target}"

    def at(self, seconds: float) -> str:
        self.clock.value = T0 + seconds
        return self.watch.check()

    def rows(self, name: str) -> list[dict[str, object]]:
        path = self.lanes / name if not name.startswith("/") else Path(name)
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def events(self) -> list[dict[str, object]]:
        return self.rows(str(self.root / "events.jsonl"))


def test_watchdog_stops_a_lane_that_does_not_commit_and_wakes_the_operator(
    tmp_path: Path,
) -> None:
    watched = Watched(tmp_path)
    assert watched.at(60) == "watching"
    _commit(watched.repo, T0 + 600, "first-test")
    # The window runs from the end of the read allowance (900 s) or the last commit, if later.
    assert watched.at(900 + 1199) == "watching"
    assert watched.stops == []
    assert watched.at(900 + 1201) == "stalled"
    assert watched.stops == ["r1:tester"]
    [event] = [row for row in watched.events() if row["kind"] == "refusal-lane-stall"]
    assert event["class"] == "refusal" and "no commit for" in str(event["detail"])
    [wake] = watched.rows("wake.jsonl")
    assert wake["kind"] == "refusal-lane-stall" and wake["slot"] == "tester"
    assert any(line.startswith("FACTORY_WAKE refusal-lane-stall") for line in watched.said)
    [output] = watched.rows("lane-output.jsonl")
    assert output["outcome"] == "stalled" and output["commits"] == 1
    assert output["time_to_first_commit_s"] == 600
    # A stopped lane is stopped once; the watcher's verdict is final.
    assert watched.at(9000) == "stalled" and watched.stops == ["r1:tester"]


def test_a_lane_waiting_on_its_own_question_is_not_stalled(tmp_path: Path) -> None:
    from harness.lane_dialogue import record_question

    watched = Watched(tmp_path)
    record_question(watched.root, "tester", "Which clock does the contract's `at` mean?")
    assert watched.at(900 + 5000) == "watching"
    assert watched.stops == []


def test_watchdog_wakes_the_operator_when_the_lane_reports_done(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    # The protocol's own wording, echoed, is not a completion.
    watched.log.write_text(
        "- When the assigned work is committed, end with one line of the form\n"
        "  __LANE_DONE__ tester commits=<the number of commits you made>\n"
    )
    assert watched.at(120) == "watching"
    _commit(watched.repo, T0 + 200, "a")
    _commit(watched.repo, T0 + 260, "b")
    message = {"type": "item.completed",
               "item": {"type": "agent_message", "text": "Report written.\n"
                        "__LANE_DONE__ tester commits=2"}}
    with watched.log.open("a") as stream:
        stream.write(json.dumps(message) + "\n")
    assert watched.at(300) == "done"
    assert watched.stops == []
    [event] = [row for row in watched.events() if row["kind"] == "lane-done"]
    assert event["wake"] is True and "2 commit(s)" in str(event["detail"])
    assert [row["kind"] for row in watched.rows("wake.jsonl")] == ["lane-done"]


def test_watchdog_wakes_the_operator_when_the_lane_process_exits(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    lane_agent.record_exit(watched.exits, slot="tester", rc=1, stopped=False)
    assert watched.at(30) == "exited"
    [event] = [row for row in watched.events() if row["kind"] == "lane-exited"]
    assert "rc=1" in str(event["detail"]) and event["wake"] is True


def test_watchdog_enforces_the_spend_cap_as_wall_clock_and_reports_output_per_dollar(
    tmp_path: Path,
) -> None:
    watched = Watched(tmp_path, caps={"spend_cap_usd": "1", "usd_per_hour": "2",
                                      "wall_cap_s": 1800})
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1700) == "watching"
    assert watched.at(1800) == "capped"
    assert watched.stops == ["r1:tester"]
    [event] = [row for row in watched.events() if row["kind"] == "refusal-lane-cap"]
    assert "wall-clock cap of 30 min reached" in str(event["detail"])
    [output] = watched.rows("lane-output.jsonl")
    assert output["spend_usd"] == "1.00" and output["commits_per_usd"] == 1.0
    assert output["spend_basis"] == "declared-usd-per-hour-x-wall-clock"
    report = lane_watchdog.lane_report(watched.root)
    assert [row["outcome"] for row in report] == ["capped"]


def test_a_resumed_turn_carries_the_lanes_active_time_toward_its_cap(tmp_path: Path) -> None:
    watched = Watched(tmp_path, caps={"usd_per_hour": "2", "spend_cap_usd": "1"})
    _commit(watched.repo, T0 + 100, "a")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(1200) == "exited"
    watched.clock.value = T0 + 5000
    watched.watch.arm(new_life=True)
    assert watched.at(5000 + 500) == "watching"
    assert watched.at(5000 + 600) == "capped"  # 1200 s + 600 s = $1 at $2/h


# -- 5. the qualification probe -------------------------------------------------------------


def test_qualification_probe_records_time_to_first_commit_and_commit_count(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "lane-qualifications.jsonl"
    passing = Watched(tmp_path / "pass", caps={"wall_cap_s": 1800},
                      qualify={"first_commit_s": 900, "min_commits": 2, "ledger": str(ledger)})
    _commit(passing.repo, T0 + 300, "a")
    _commit(passing.repo, T0 + 700, "b")
    lane_agent.record_exit(passing.exits, slot="tester", rc=0, stopped=False)
    assert passing.at(800) == "exited"
    failing = Watched(tmp_path / "fail", caps={"wall_cap_s": 1800},
                      qualify={"first_commit_s": 900, "min_commits": 1, "ledger": str(ledger)})
    assert failing.at(1800) == "capped"
    first, second = (json.loads(line) for line in ledger.read_text().splitlines())
    assert first["verdict"] == "pass" and first["time_to_first_commit_s"] == 300
    assert first["commit_count"] == 2 and first["model"] == "glm-test:cloud"
    assert second["verdict"] == "fail" and second["commit_count"] == 0
    assert "no commit" in second["reasons"]
    assert any("qualification fail" in line for line in failing.said)


def test_qualify_mode_launches_a_wall_capped_probe_slot(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    _cursor_stub(lanes.stub, signed_in=True)
    metered = lanes.lane("tester", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh",
                         "--metered", action="qualify")
    assert metered.returncode == 64 and "spend cap" in metered.stderr
    probed = lanes.lane("tester", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh",
                        "--round", "qualify-grok", action="qualify")
    assert probed.returncode == 0, probed.stdout + probed.stderr
    row = read_chain(lanes.root / "tmux-lanes" / "tester.qualify-grok-launch.jsonl")[-1]
    assert row["mode"] == "qualify" and row["slot"] == "tester.qualify-grok"
    assert row["caps"]["wall_cap_s"] == 1800  # a probe is always wall-capped
    assert row["qualify"]["first_commit_s"] == 900 and row["qualify"]["min_commits"] == 1
    assert row["qualify"]["ledger"] == str(lanes.root.parent / "lane-qualifications.jsonl")


# -- 6. parallel instances use clones, never worktrees --------------------------------------


def test_parallel_instances_need_their_own_clone(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    assert lanes.lane("tester").returncode == 0
    shared = lanes.lane("tester", "--instance", "b", repo=lanes.clone("tester"))
    assert shared.returncode != 0 and "own copy" in shared.stderr
    source = lanes.clone("source")
    subprocess.run(["git", "-C", str(source), "commit", "-q", "--allow-empty", "-m", "base",
                    "--author", "t <t@t>"], check=True,
                   env={**os.environ, "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    worktree = tmp_path / "worktree-b"
    subprocess.run(["git", "-C", str(source), "worktree", "add", "-q", str(worktree)],
                   check=True)
    refused = lanes.lane("tester", "--instance", "b", repo=worktree)
    assert refused.returncode == 70 and "never a git worktree" in refused.stderr
    launched = lanes.lane("tester", "--instance", "b", repo=lanes.clone("tester-b"))
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert len(lanes.windows("tester-b")) == 1
    row = read_chain(lanes.root / "tmux-lanes" / "tester-b-launch.jsonl")[-1]
    assert row["lane"] == "tester-b" and row["instance"] == "b"


def test_a_slot_launches_once_and_a_new_round_gets_its_own(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    assert lanes.lane("coder").returncode == 0
    again = lanes.lane("coder")
    assert again.returncode == 64 and "--round" in again.stderr
    second = lanes.lane("coder", "--round", "r2")
    assert second.returncode == 0, second.stdout + second.stderr
    assert (lanes.root / "tmux-lanes" / "coder.r2-prompt.txt").is_file()


# -- 7. the testing standard reaches every Tester ------------------------------------------


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def test_a_tester_prompt_carries_the_testing_standard_itself(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    strategy = lanes.root / "artifacts" / "testing-strategy.md"
    strategy.parent.mkdir(exist_ok=True)
    strategy.write_text("# Testing strategy\nParity shadow against the legacy oracle.\n")
    assert lanes.lane("tester").returncode == 0
    prompt = (lanes.root / "tmux-lanes" / "tester-prompt.txt").read_text(encoding="utf-8")
    standard = REPO / "docs" / "standards" / "TESTING.md"
    doctrine = REPO / "prompts" / "test.md"
    for key, path in (("testing-standard", standard), ("tester-doctrine", doctrine),
                      ("testing-strategy", strategy)):
        assert f"<<<FACTORY_TESTING_STANDARD {key} digest={_sha(path)}>>>" in prompt
        assert path.read_text(encoding="utf-8").strip() in prompt
    assert prompt.index("MANDATORY FIRST READING") < prompt.index("VERBATIM LANE TASK")
    assert prompt.index("FACTORY TMUX LANE PROTOCOL") < prompt.index("MANDATORY FIRST READING")
    row = read_chain(lanes.root / "tmux-lanes" / "tester-launch.jsonl")[-1]
    assert row["testing_standard"]["sources"]["testing-standard"]["digest"] == _sha(standard)
    # The Coder's prompt does not carry it.
    assert lanes.lane("coder").returncode == 0
    coder = (lanes.root / "tmux-lanes" / "coder-prompt.txt").read_text(encoding="utf-8")
    assert "FACTORY_TESTING_STANDARD" not in coder


def test_a_tester_launch_is_refused_when_the_standard_cannot_be_injected(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    strategy = lanes.root / "artifacts" / "testing-strategy.md"
    strategy.parent.mkdir(exist_ok=True)
    (tmp_path / "elsewhere.md").write_text("not the run's strategy\n")
    strategy.symlink_to(tmp_path / "elsewhere.md")
    refused = lanes.lane("tester")
    assert refused.returncode == 70 and "TESTING STANDARD" in refused.stderr
    assert lanes.windows("tester") == []
    assert not (lanes.root / "tmux-lanes" / "tester-prompt.txt").exists()

    home = tmp_path / "factory-without-standard"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "test.md").write_text("# /test\n")
    with pytest.raises(testing_standard.StandardMissing, match="How We Test"):
        testing_standard.build(home)


def test_a_qualified_tester_dispatch_opens_with_the_testing_standard(tmp_path: Path) -> None:
    cwd, root, dispatch, stub = dispatch_success_fixture(tmp_path, role="tester", primer=True)
    result = run(
        ["bash", str(HARNESS / "dispatch_lane.sh"), "r1", "tester", "--dispatch", str(dispatch)],
        cwd,
        _dispatch_env(stub, root),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    task = (root / "runner-tasks" / "tester.md").read_text(encoding="utf-8")
    standard = REPO / "docs" / "standards" / "TESTING.md"
    assert f"<<<FACTORY_TESTING_STANDARD testing-standard digest={_sha(standard)}>>>" in task
    headings = ("## FENCE", "## MANDATORY FIRST READING — TESTING STANDARD",
                "## FROZEN DISPATCH")
    assert [task.index(h) for h in headings] == sorted(task.index(h) for h in headings)
    # The run's Markdown strategy view still reaches the qualified lane only through run-model.
    assert "testing-strategy digest=" not in task


# -- the lane process wrapper ---------------------------------------------------------------


def test_the_lane_wrapper_tees_output_and_records_the_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log, exits = tmp_path / "lane.log", tmp_path / "exits.jsonl"
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("the brief")
    rc = lane_agent.run_lane(
        ["--", sys.executable, "-c", "import sys; print('got', sys.argv[1]); sys.exit(3)"],
        slot="coder", log=log, exits=exits, prompt=prompt,
    )
    assert rc == 3
    assert log.read_text().splitlines() == ["got the brief", "__LANE_EXIT__ coder rc=3"]
    [row] = [json.loads(line) for line in exits.read_text().splitlines()]
    assert row["rc"] == 3 and row["slot"] == "coder" and row["stopped_by_signal"] is False
