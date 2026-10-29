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
import re
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
        '  --help) echo "-p, --print --model --force --trust --workspace --output-format '
        '--resume --approve-mcps" ;;\n'
        f"  status) echo '{state}' ;;\n"
        "esac\n"
        "exit 0\n"
    ))


class Lanes:
    """One ignited run and a launcher for its lanes."""

    def __init__(self, tmp_path: Path, *, budget: str | None = None) -> None:
        task = "Exercise the lane launch controls."
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.operator, self.root, _ = execution_truth_fixture(
            tmp_path, task=task, harness_status=None
        )
        env, self.tmux_log = factory_ignition_env(tmp_path, self.root)
        strategy = self.root / "artifacts" / "testing-strategy.md"
        strategy.parent.mkdir(exist_ok=True)
        strategy.write_text("# Testing strategy\nExercise the real seam.\n")
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


def test_cursor_agent_lane_runs_the_stream_mode_invocation_under_the_lane_wrapper(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    _cursor_stub(lanes.stub, signed_in=True)
    launched = lanes.lane("coder", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    lane = lanes.clone("coder").resolve()
    [call] = lanes.windows("coder")
    assert ("-- agent -p --output-format stream-json --force --trust --approve-mcps "
            f"--workspace {lane} --model grok-4.7-xhigh") in call
    assert "env -i" in call and "lane_agent.py run --slot coder" in call
    assert "--prompt-arg" in call and "coder-prompt.txt" in call
    # A plan lane: the wrapper records its session as it streams and stops API billing.
    assert "--session-agent cursor-agent" in call and "coder-thread-id" in call
    assert "--plan-only" in call and "coder-credentials.env" in call
    # Its private HOME holds only the lane's Kindex, never the operator's MCP servers.
    home = lanes.root / "tmux-lanes" / "coder-agent-home" / "home"
    assert list(json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]) == \
        ["kindex"]
    turn = json.loads((lanes.root / "tmux-lanes" / "coder-turn.json").read_text())
    assert turn["agent"] == "cursor-agent" and turn["billing"] == "plan"
    [watcher] = lanes.windows("watch-coder")
    assert "lane_watchdog.py lane --root" in watcher and "--slot coder" in watcher
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert row["status"] == "active" and row["agent"] == "cursor-agent"
    assert row["model"] == "grok-4.7-xhigh" and row["prompt_via"] == "argv"
    assert row["cli_contract"] == "cursor-agent-stream-resume-v1"
    assert row["auth_preflight"] == "cursor-agent-status"
    watch = dict(row["watch"])
    assert Path(watch.pop("runs_dir")).name == "runs"
    # A cursor-agent session is resumable, so a Coder gets the Validator's review pauses.
    assert watch == {"read_allowance_s": 900, "stall_window_s": 1200, "poll_s": 30,
                     "wrap_s": 0, "review_every_s": 1080, "review_wait_s": 900}

    # Until the harness has streamed its session there is nothing to resume: launch a round.
    message = run(
        ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator", "coder", "status",
         "--runs", str(lanes.root.parent)],
        lanes.operator, lanes.env,
    )
    assert message.returncode == 70 and "new --round" in message.stderr


def test_cursor_agent_without_a_named_model_runs_its_own_default(tmp_path: Path) -> None:
    """The factory names no model: with none named, cursor-agent gets no --model flag and the
    launch records that the agent chose."""

    lanes = Lanes(tmp_path)
    _cursor_stub(lanes.stub, signed_in=True)
    launched = lanes.lane("coder", "--agent", "cursor-agent")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    [call] = lanes.windows("coder")
    assert "-- agent -p --output-format stream-json --force --trust --approve-mcps --workspace" \
        in call and "--model" not in call
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert (row["model"], row["model_source"]) == ("", "agent")


def test_a_lane_names_its_model_family_from_the_model_first_then_a_single_vendor_agent(
    tmp_path: Path,
) -> None:
    family = lane_agent.model_family
    assert family("muse", "") == "meta" and family("codex", "") == "openai"
    assert family("codex", "gpt-6.1-sol") == "openai" and family("agy", "") == "google"
    assert family("cursor-agent", "grok-4.7-xhigh") == "xai"
    assert family("claude-ollama", "glm-5.3:cloud") == "zhipu"
    assert family("claude", "claude-opus-5-5") == "anthropic"
    # A multi-vendor agent with no named model, or a named model no prefix recognizes (a custom
    # provider can serve anything under any name), does not say.
    assert family("cursor-agent", "") == "" and family("prime-agent", "") == ""
    assert family("codex", "private-model") == ""
    # A Codex lane with no named model runs the operator's config, provider included.
    (tmp_path / "config.toml").write_text(
        'model = "claude-opus-5"\nmodel_provider = "proxy"\n'
        '[model_providers.proxy]\nname = "Proxy"\nbase_url = "http://127.0.0.1:9/v1"\n',
        encoding="utf-8")
    assert lane_agent.lane_family("codex", "", tmp_path) == "anthropic"
    (tmp_path / "config.toml").write_text(
        'model = "house"\nmodel_provider = "proxy"\n'
        '[model_providers.proxy]\nname = "Proxy"\nbase_url = "http://127.0.0.1:9/v1"\n',
        encoding="utf-8")
    assert lane_agent.lane_family("codex", "", tmp_path) == ""


def test_the_tester_does_not_start_on_the_coders_model_family(tmp_path: Path) -> None:
    """SOFTWARE-FACTORY.md "Across the lanes" (E1, I19): the Coder and the Tester run different
    model families; a same-family or unknown-family Tester is refused before anything starts."""

    lanes = Lanes(tmp_path)
    lanes.env.pop("FACTORY_LANE_INDEPENDENCE_GAP")
    assert lanes.lane("coder").returncode == 0
    same = lanes.lane("tester")
    assert same.returncode == 70 and "different, known model families" in same.stderr
    assert "openai" in same.stderr and not lanes.windows("tester")
    _cursor_stub(lanes.stub, signed_in=True)
    unknown = lanes.lane("tester", "--agent", "cursor-agent")
    assert unknown.returncode == 70 and "unknown model family" in unknown.stderr

    crossed = lanes.lane("tester", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh")
    assert crossed.returncode == 0, crossed.stdout + crossed.stderr
    row = read_chain(lanes.root / "tmux-lanes" / "tester-launch.jsonl")[-1]
    assert row["status"] == "active"
    assert row["independence"] == {"family": "xai", "other_role": "coder", "other_slot": "coder",
                                   "other_family": "openai", "tier": "cross-family"}
    seats = json.loads((lanes.root / "tmux-lanes" / "lane-seats.json").read_text())
    assert seats["tester"]["status"] == "active" and seats["tester"]["family"] == "xai"


def test_a_codex_lane_is_judged_by_the_model_its_config_runs(tmp_path: Path) -> None:
    """A Codex lane with no --model runs the operator's config; a config that routes Codex to a
    Claude model through a custom provider is Anthropic, not OpenAI."""

    lanes = Lanes(tmp_path)
    lanes.env.pop("FACTORY_LANE_INDEPENDENCE_GAP")
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "claude-opus-5"\nmodel_provider = "proxy"\n'
        '[model_providers.proxy]\nname = "Proxy"\nbase_url = "http://127.0.0.1:9/v1"\n',
        encoding="utf-8")
    _cursor_stub(lanes.stub, signed_in=True)
    assert lanes.lane("tester", "--agent", "cursor-agent", "--model",
                      "claude-sonnet-5").returncode == 0
    routed = lanes.lane("coder", env={"CODEX_HOME": str(home)})
    assert routed.returncode == 70 and "anthropic" in routed.stderr
    assert not lanes.windows("coder")


def test_the_first_lane_needs_a_known_family_and_records_its_tier(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    lanes.env.pop("FACTORY_LANE_INDEPENDENCE_GAP")
    _cursor_stub(lanes.stub, signed_in=True)
    unknown = lanes.lane("coder", "--agent", "cursor-agent")
    assert unknown.returncode == 70 and "unknown model family" in unknown.stderr
    assert lanes.lane("coder").returncode == 0
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert row["independence"] == {"family": "openai", "tier": "no-peer"}


def test_a_qualification_probe_takes_no_seat_and_masks_nothing(tmp_path: Path) -> None:
    """A qualify run is a probe, not a seat: it neither masks the production Tester nor is
    checked against it."""

    lanes = Lanes(tmp_path)
    lanes.env.pop("FACTORY_LANE_INDEPENDENCE_GAP")
    assert lanes.lane("tester").returncode == 0
    _cursor_stub(lanes.stub, signed_in=True)
    lanes.lane("tester", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh", action="qualify",
               repo=lanes.clone("tester-probe"))
    seats = json.loads((lanes.root / "tmux-lanes" / "lane-seats.json").read_text())
    assert seats["tester"]["family"] == "openai"
    same = lanes.lane("coder")
    assert same.returncode == 70 and "openai" in same.stderr


def test_one_seated_launch_at_a_time_and_a_dead_one_falls_back(tmp_path: Path) -> None:
    """While a seat is pending in a live launch, every other claim waits, so no lane is admitted
    against a launch that may still fail. A pending seat whose launch is gone falls back to the
    seat it would have replaced."""

    root = tmp_path / "tmux-lanes"
    claim = lane_agent.claim_seat
    claim(root, role="tester", slot="tester", agent_name="codex", model="", codex_home=None,
          gap="", pid=os.getpid())
    for role, slot in (("coder", "coder"), ("tester", "tester.r2")):
        with pytest.raises(SystemExit, match="still starting"):
            claim(root, role=role, slot=slot, agent_name="cursor-agent", model="grok-4.7-xhigh",
                  codex_home=None, gap="", pid=os.getpid())
    lane_agent.confirm_seat(root, role="tester", slot="tester")
    dead = subprocess.Popen(["true"])
    dead.wait()
    # A replacement Tester on xAI that never got going: the OpenAI seat it would have replaced
    # still stands, so an OpenAI Coder is refused.
    claim(root, role="tester", slot="tester.r2", agent_name="cursor-agent",
          model="grok-4.7-xhigh", codex_home=None, gap="", pid=dead.pid)
    with pytest.raises(SystemExit, match="openai"):
        claim(root, role="coder", slot="coder", agent_name="codex", model="", codex_home=None,
              gap="", pid=os.getpid())


def test_a_reused_pid_does_not_revive_an_abandoned_launch(tmp_path: Path) -> None:
    root = tmp_path / "tmux-lanes"
    lane_agent.claim_seat(root, role="tester", slot="tester", agent_name="codex", model="",
                          codex_home=None, gap="", pid=os.getpid())
    lane_agent.confirm_seat(root, role="tester", slot="tester")
    seats = json.loads((root / "lane-seats.json").read_text())
    # An abandoned xAI replacement whose PID now belongs to another process (this one).
    seats["tester"] = {"slot": "tester.r2", "agent": "cursor-agent", "model": "grok-4.7-xhigh",
                       "family": "xai", "status": "pending", "pid": os.getpid(),
                       "pid_start": "Thu Jan  1 00:00:00 1970", "previous": seats["tester"]}
    (root / "lane-seats.json").write_text(json.dumps(seats))
    with pytest.raises(SystemExit, match="openai"):
        lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="codex", model="",
                              codex_home=None, gap="", pid=os.getpid())


def test_a_run_launched_before_the_seat_file_keeps_its_seats(tmp_path: Path) -> None:
    """Upgrading the factory mid-run: the seats come from the latest active launches."""

    root = tmp_path / "tmux-lanes"
    root.mkdir()
    rows = [
        {"status": "active", "mode": "launch", "role": "tester", "slot": "tester",
         "agent": "muse", "model": "", "ts": "2026-10-20T16:08:27+00:00"},
        {"status": "active", "mode": "launch", "role": "tester", "slot": "tester.sol-1",
         "agent": "codex", "model": "gpt-6.1-sol", "ts": "2026-10-22T01:39:00+00:00"},
        {"status": "active", "mode": "qualify", "role": "tester", "slot": "tester-b",
         "agent": "cursor-agent", "model": "grok-4.7-xhigh", "ts": "2026-10-22T02:00:00+00:00"},
    ]
    (root / "tester-launch.jsonl").write_text(json.dumps(rows[0]) + "\n")
    (root / "tester.sol-1-launch.jsonl").write_text(json.dumps(rows[1]) + "\n")
    (root / "tester-b-launch.jsonl").write_text(json.dumps(rows[2]) + "\n")
    with pytest.raises(SystemExit, match="openai"):
        lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="codex", model="",
                              codex_home=None, gap="", pid=os.getpid())
    record = lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="muse", model="",
                                   codex_home=None, gap="", pid=os.getpid())
    assert record["tier"] == "cross-family" and record["other_slot"] == "tester.sol-1"


def test_a_seeded_seat_never_guesses_its_family(tmp_path: Path) -> None:
    """Seeding from launch records: a Codex TUI run on the operator's config, or two latest
    launches at the same second with different families, seed an unknown family."""

    root = tmp_path / "tmux-lanes"
    root.mkdir()
    tui = {"status": "active", "mode": "launch", "role": "tester", "slot": "tester",
           "agent": "codex-interactive", "model": "", "ts": "2026-10-22T01:00:00+00:00"}
    (root / "tester-launch.jsonl").write_text(json.dumps(tui) + "\n")
    with pytest.raises(SystemExit, match="unknown model family"):
        lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="claude", model="",
                              codex_home=None, gap="", pid=os.getpid())
    (root / "lane-seats.json").unlink(missing_ok=True)
    tie = [dict(tui, agent="muse", slot="tester.a", ts="2026-10-22T02:00:00+00:00"),
           dict(tui, agent="agy", slot="tester.b", ts="2026-10-22T02:00:00+00:00")]
    (root / "tester-launch.jsonl").write_text("".join(json.dumps(r) + "\n" for r in tie))
    with pytest.raises(SystemExit, match="unknown model family"):
        lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="claude", model="",
                              codex_home=None, gap="", pid=os.getpid())


def test_only_the_pending_launch_confirms_its_seat(tmp_path: Path) -> None:
    root = tmp_path / "tmux-lanes"
    lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="codex", model="",
                          codex_home=None, gap="", pid=os.getpid())
    with pytest.raises(SystemExit, match="not coder.r2's to confirm"):
        lane_agent.confirm_seat(root, role="coder", slot="coder.r2")
    lane_agent.confirm_seat(root, role="coder", slot="coder")
    with pytest.raises(SystemExit, match="not coder's to confirm"):
        lane_agent.confirm_seat(root, role="coder", slot="coder")


def test_a_same_family_lane_starts_only_with_its_gap_recorded(tmp_path: Path) -> None:
    """Where no other family is configured the doctrine proceeds and records the gap; the
    operator's reason travels in the launch record."""

    lanes = Lanes(tmp_path)
    assert lanes.lane("coder").returncode == 0
    gapped = lanes.lane("tester", env={"FACTORY_LANE_INDEPENDENCE_GAP": "only Codex configured"})
    assert gapped.returncode == 0, gapped.stdout + gapped.stderr
    row = read_chain(lanes.root / "tmux-lanes" / "tester-launch.jsonl")[-1]
    assert row["independence"]["tier"] == "same-family"
    assert row["independence"]["independence_gap"] == "only Codex configured"


def test_an_unknown_peer_is_an_unknown_family_tier_whichever_lane_starts_second(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tmux-lanes"
    first = lane_agent.claim_seat(root, role="coder", slot="coder", agent_name="cursor-agent",
                                  model="", codex_home=None, gap="no other family", pid=os.getpid())
    assert first["tier"] == "unknown-family" and first["independence_gap"] == "no other family"
    lane_agent.confirm_seat(root, role="coder", slot="coder")
    second = lane_agent.claim_seat(root, role="tester", slot="tester", agent_name="codex",
                                   model="", codex_home=None, gap="no other family",
                                   pid=os.getpid())
    assert second["tier"] == "unknown-family" and second["other_family"] is None


def test_every_lane_agent_is_described_once_and_only_the_per_use_agents_are_metered() -> None:
    """lane_agent.py names each agent tmux_lane.sh admits, and no other; the agents metered by
    construction are the Ollama-backed Codex and prime-agent (OpenRouter, no plan). Any other
    launch is metered only when marked."""

    text = (HARNESS / "tmux_lane.sh").read_text(encoding="utf-8")
    match = re.search(r'case "\$AGENT" in ([a-z|-]+)\) ;;', text)
    assert match is not None
    assert set(match.group(1).split("|")) == set(lane_agent.AGENTS)
    assert {name for name, agent in lane_agent.AGENTS.items() if agent.metered} == {
        "codex-ollama", "prime-agent"
    }


def test_codex_model_selection_carries_only_the_model_keys(tmp_path: Path) -> None:
    """What a codex lane carries over from the operator's Codex config: the model keys, a legacy
    profile's model keys over them, and the selected provider's definition. Nothing else, and
    nothing at all when the config names no model or does not exist."""

    assert lane_agent.codex_model_selection(tmp_path / "absent") == ("", [])
    (tmp_path / "config.toml").write_text(
        'model = "base"\nmodel_provider = "local"\nprofile = "fast"\n'
        'approval_policy = "never"\n'
        '[profiles.fast]\nmodel = "quick"\nmodel_reasoning_effort = "low"\n'
        '[model_providers.local]\nname = "Local"\nbase_url = "http://127.0.0.1:1234/v1"\n'
        '[mcp_servers.personal]\ncommand = "personal-mcp"\n',
        encoding="utf-8",
    )
    model, overrides = lane_agent.codex_model_selection(tmp_path)
    assert model == "quick"
    assert overrides == [
        'model="quick"',
        'model_provider="local"',
        'model_reasoning_effort="low"',
        'model_providers.local={"name" = "Local", "base_url" = "http://127.0.0.1:1234/v1"}',
    ]
    (tmp_path / "config.toml").write_text('approval_policy = "never"\n', encoding="utf-8")
    assert lane_agent.codex_model_selection(tmp_path) == ("", [])
    (tmp_path / "config.toml").write_text("model = \n", encoding="utf-8")
    with pytest.raises(ValueError, match="not readable TOML"):
        lane_agent.codex_model_selection(tmp_path)


def test_interactive_codex_runs_the_tui_without_its_daemon(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    _codex_stub(lanes.stub, signed_in=True)
    launched = lanes.lane("coder", "--agent", "codex-interactive")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    [call] = lanes.windows("coder")
    assert "--terminal --prompt-arg" in call
    assert "-- codex --no-daemon --no-alt-screen --strict-config --ask-for-approval never" in call
    assert "never exec" not in call and "codex_lane_session" not in call
    assert "--disable apps" in call


def test_codex_exec_stays_the_default_author_lane(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    assert lanes.lane("coder").returncode == 0
    [call] = lanes.windows("coder")
    assert "lane_agent.py run --slot coder" in call
    assert "codex_lane_session.py" in call and "codex --ask-for-approval never exec" in call
    assert "--disable apps" in call
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


def test_a_launch_the_operator_marks_metered_needs_a_cap(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    _cursor_stub(lanes.stub, signed_in=True)
    refused = lanes.lane("tester", "--agent", "cursor-agent", "--model", "grok-4.7-xhigh",
                         "--metered")
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


def test_status_counts_lane_commits_without_running_host_git(tmp_path: Path) -> None:
    """A lane's .git is agent-written, so status reads its reflog file. A planted fsmonitor or
    signature program never runs, because no git process starts at all."""

    repo = tmp_path / "lane"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, T0, "first")
    _commit(repo, T0 + 60, "second")
    marker = tmp_path / "git-ran"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _stub(stubs, "git", f'touch "{marker}"\nexit 1\n')
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}

    shown = subprocess.run(
        [sys.executable, str(HARNESS / "lane_watchdog.py"), "commits", "--repository", str(repo)],
        capture_output=True, text=True, check=True, env=env,
    )

    summary = json.loads(shown.stdout)
    assert summary["count"] == 2
    assert summary["latest"]["subject"].endswith("second")
    assert not marker.exists()
    status = (HARNESS / "status.sh").read_text(encoding="utf-8")
    assert 'git -C "$workspace"' not in status
    assert '"$D/lane_watchdog.py" commits --repository "$workspace"' in status


class Watched:
    def __init__(self, tmp_path: Path, *, caps: dict[str, object] | None = None,
                 qualify: dict[str, object] | None = None,
                 watch: dict[str, object] | None = None, thread: bool = False) -> None:
        self.root = tmp_path / "run"
        self.lanes = self.root / "tmux-lanes"
        self.lanes.mkdir(parents=True)
        self.repo = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.log = self.lanes / "tester-lane.log"
        self.log.touch()
        self.exits = self.lanes / "tester-exits.jsonl"
        row = {
            "status": "active", "ts": datetime.datetime.fromtimestamp(T0, datetime.UTC)
            .isoformat(), "run_id": "r1", "role": "tester", "lane": "tester", "slot": "tester",
            "agent": "codex-ollama", "model": "glm-test:cloud", "terminal": False,
            "repository": str(self.repo), "lane_log": str(self.log),
            "lane_exits": str(self.exits), "tmux_target": "r1:tester",
            "watch": {"read_allowance_s": 900, "stall_window_s": 1200, "poll_s": 30,
                      **(watch or {})},
            "caps": caps or {}, "qualify": qualify,
        }
        (self.lanes / "tester-launch.jsonl").write_text(json.dumps(row) + "\n")
        if thread:
            (self.lanes / "tester-thread-id").write_text(
                "12345678-1234-4234-8234-123456789abc\n")
            delivery = self.lanes / "tester-delivery.env"
            delivery.write_text("PATH=/usr/bin:/bin\n")
            delivery.chmod(0o600)
        self.clock = Clock()
        self.stops: list[str] = []
        self.said: list[str] = []
        self.delivered: list[list[str]] = []
        self.watch = lane_watchdog.LaneWatch(
            root=self.root, slot="tester", now=self.clock, stop=self._stop,
            tmux=lambda *a: subprocess.CompletedProcess(
                a, 0, "tester @1\n" if a[0] == "list-windows" else "1\n", ""),
            echo=self.said.append,
            deliver=self._deliver,
            processes=lambda: {},
        )
        self.watch.arm()

    def _deliver(self, argv: list[str]) -> str:
        self.delivered.append(argv)
        return "scheduled"

    def resume(self, seconds: float) -> None:
        """What a delivered lane message does: a new life of the watcher after the resume."""
        self.clock.value = T0 + seconds
        self.watch.arm(new_life=True)

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
    [requested, wake] = watched.rows("wake.jsonl")
    assert requested["kind"] == "lane-stop-requested"
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
    assert watched.at(300) == "watching"  # a response is still only a claim
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(301) == "done"
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


def test_tool_fixture_cannot_end_monitoring_even_after_clean_exit(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "tool_result", "content": {
        "fixture": "__LANE_DONE__ tester commits=9"}}) + "\n")
    assert watched.at(30) == "watching"
    assert not watched.rows("lane-output.jsonl")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(31) == "exited"  # no independent agent completion claim


def test_interactive_screen_text_cannot_certify_agent_completion(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    launch = watched.watch.launch()
    launch["terminal"] = True
    watched.watch.journal.write_text(json.dumps(launch) + "\n")
    # Even a displayed JSON fixture cannot establish a terminal event's author.
    displayed = json.dumps({"type": "result", "result": "__LANE_DONE__ tester commits=0"})
    watched.log.write_text(displayed + "\n")
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, displayed if a[0] == "capture-pane" else
        "tester @1\n" if a[0] == "list-windows" else "1\n", "")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(31) == "exited"


@pytest.mark.parametrize("pane", ["0", "", "unknown"])
def test_completion_waits_for_positive_process_exit_evidence(tmp_path: Path, pane: str) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ tester commits=0"}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, "tester @1\n" if a[0] == "list-windows" else pane, "")
    assert watched.at(30) == "watching"
    assert not watched.rows("lane-output.jsonl")
    # A watcher restart retains the claim, but still requires a positive dead-pane observation.
    watched.watch.arm()
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, "tester @1\n" if a[0] == "list-windows" else "1\n", "")
    assert watched.at(31) == "done"


@pytest.mark.parametrize(("rc", "stopped"), [(1, False), (0, True)])
def test_failed_or_signalled_turn_is_not_done(tmp_path: Path, rc: int, stopped: bool) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ tester commits=0"}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=rc, stopped=stopped)
    assert watched.at(30) == "exited"


def test_completion_rejects_wrong_lane_and_untyped_exit_receipts(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ coder commits=1"}) + "\n")
    lane_agent.record_exit(watched.exits, slot="coder", rc=0, stopped=False)
    with watched.exits.open("a") as stream:
        stream.write(json.dumps({"slot": "tester", "rc": 0}) + "\n")
    assert watched.at(30) == "watching"
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(31) == "exited"


def test_new_life_does_not_inherit_a_previous_completion_claim(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ tester commits=0"}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(30) == "done"
    watched.resume(60)
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(61) == "exited"


def test_requested_stop_stays_pending_across_restart_questions_and_done_claims(
    tmp_path: Path,
) -> None:
    from harness.lane_dialogue import record_question

    watched = Watched(tmp_path, caps={"wall_cap_s": 60})
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, "tester @1\n" if a[0] == "list-windows" else "0\n", "")
    assert watched.at(61) == "stopping"
    assert watched.stops == ["r1:tester"]
    assert not watched.rows("lane-output.jsonl")
    watched.watch.arm()  # the watcher restarts; intent survives
    record_question(watched.root, "tester", "Can I continue?")
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ tester commits=0"}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(62) == "stopping"
    with pytest.raises(lane_watchdog.LaneWatchError, match="stop is unverified"):
        watched.watch.arm(new_life=True)
    assert watched.stops == ["r1:tester"]  # no repeat signal to a potentially reused identity
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, "tester @1\n" if a[0] == "list-windows" else "1\n", "")
    assert watched.at(63) == "capped"
    [output] = watched.rows("lane-output.jsonl")
    assert output["outcome"] == "capped" and output["wall_s"] == 63
    state = json.loads(watched.watch.state_path.read_text())
    assert state["stop_verified_ts"] > state["stop_requested_ts"]


def test_dead_pane_does_not_verify_stop_while_observed_reparented_child_survives(
    tmp_path: Path,
) -> None:
    watched = Watched(tmp_path, caps={"wall_cap_s": 60})
    table = {
        1: {"pid": 1, "ppid": 99, "started": "root-start", "state": "S"},
        2: {"pid": 2, "ppid": 1, "started": "child-start", "state": "S"},
    }
    watched.watch.processes = lambda: table
    assert watched.at(30) == "watching"  # records the tree before the parent exits
    del table[1]
    table[2]["ppid"] = 99  # orphan is no longer discoverable by walking from the old root
    assert watched.at(61) == "stopping"
    assert not watched.rows("lane-output.jsonl")
    watched.watch.arm()
    assert watched.at(62) == "stopping"
    assert json.loads(watched.watch.state_path.read_text())["surviving_processes"] == [
        {"pid": 2, "started": "child-start"}]
    # PID reuse is not a surviving child, and the observer must not signal the unrelated process.
    table[2]["started"] = "different-process"
    assert watched.at(63) == "capped"
    assert watched.stops == ["r1:tester"]


def test_process_observation_failure_cannot_certify_completion(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ tester commits=0"}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    watched.watch.processes = lambda: None
    assert watched.at(30) == "watching"
    assert not watched.rows("lane-output.jsonl")


def test_later_agent_result_can_retract_an_earlier_done_claim(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    watched.log.write_text(json.dumps({"type": "result",
                                      "result": "__LANE_DONE__ tester commits=0"}) + "\n")
    assert watched.at(30) == "watching"
    with watched.log.open("a") as stream:
        stream.write(json.dumps({"type": "result", "result": "Blocked: unfinished."}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(31) == "exited"


def test_exit_waits_for_all_log_chunks_and_split_response_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    watched = Watched(tmp_path)
    first = json.dumps({"type": "result", "result": "__LANE_DONE__ tester commits=0"}) + "\n"
    last = json.dumps({"type": "result", "result": "Blocked; still unfinished."}) + "\n"
    watched.log.write_text(first + last)
    monkeypatch.setattr(lane_watchdog, "_MAX_LOG_SCAN_BYTES", len(first) + 10)
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(30) == "watching"  # first claim, but later response is only partly read
    assert watched.at(31) == "exited"  # completes the split JSON record, retracting the claim


def test_partial_tool_output_never_leaks_into_watcher_status(tmp_path: Path) -> None:
    watched = Watched(tmp_path)
    private = "private-author-work-product"
    event = json.dumps({"type": "tool_result", "content": private})
    watched.log.write_text(event)
    assert watched.at(30) == "watching"
    status = watched.watch.state_path.read_text()
    assert private not in status and "log_fragment" not in json.loads(status)
    assert json.loads(status)["log_offset"] == 0
    with watched.log.open("a") as stream:
        stream.write("\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(31) == "exited"


def test_discarded_oversized_record_invalidates_an_earlier_completion_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    watched = Watched(tmp_path)
    first = json.dumps({"type": "result", "result": "__LANE_DONE__ tester commits=0"}) + "\n"
    watched.log.write_text(first)
    assert watched.at(30) == "watching"
    monkeypatch.setattr(lane_watchdog, "_MAX_LOG_SCAN_BYTES", 100)
    with watched.log.open("a") as stream:
        stream.write(json.dumps({"type": "result", "result": "unfinished " * 80}) + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    outcome = "watching"
    for seconds in range(31, 50):
        outcome = watched.at(seconds)
        if outcome != "watching":
            break
    assert outcome == "exited"


def test_stop_failure_remains_durable_and_is_not_retried_blindly(tmp_path: Path) -> None:
    watched = Watched(tmp_path, caps={"wall_cap_s": 60})
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, "tester @1\n" if a[0] == "list-windows" else "0\n", "")
    attempts = []

    def refused(target: str) -> str:
        attempts.append(target)
        raise PermissionError("test stop denied")

    watched.watch.stop = refused
    assert watched.at(61) == "stopping"
    watched.watch.arm()
    assert watched.at(62) == "stopping"
    assert attempts == ["r1:tester"]
    assert "stop failed" in json.loads(watched.watch.state_path.read_text())["stop_response"]


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


def test_a_new_life_never_reports_the_earlier_turns_done_line(tmp_path: Path) -> None:
    """herald-dash-r3: the watcher of a resumed turn re-read the previous turn's done line from
    the log and woke the Validator two minutes into a twenty-minute turn."""

    watched = Watched(tmp_path)
    _commit(watched.repo, T0 + 100, "a")
    watched.log.write_text("__LANE_DONE__ tester commits=1\n", encoding="utf-8")
    resumed_at = watched.log.stat().st_size
    watched.clock.value = T0 + 3000
    watched.watch.arm(new_life=True, log_offset=resumed_at)
    assert watched.at(3060) == "watching"
    with watched.log.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"type": "result", "result": "__LANE_DONE__ tester commits=2"})
                  + "\n")
    lane_agent.record_exit(watched.exits, slot="tester", rc=0, stopped=False)
    assert watched.at(3120) == "done"


# -- 4b. wrap-up before the cap, and progress-review pauses ----------------------------------

RESUMABLE = {"runs_dir": "/runs", "wrap_s": 300}


def test_a_resumable_lane_gets_a_wrap_up_turn_before_the_cap_stops_it(tmp_path: Path) -> None:
    """A lane killed mid-edit at its cap leaves nothing that runs (Terminal-Bench 4: a 59/60
    deliverable fell to 0/60). Five minutes before the cap the turn ends and a generated wrap-up
    resumes the thread; the cap still stops the lane, counting only active time."""

    watched = Watched(tmp_path, caps={"wall_cap_s": 1800}, watch=RESUMABLE, thread=True)
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1499) == "watching" and watched.delivered == []
    assert watched.at(1500) == "wrapping"
    assert watched.stops == ["r1:tester"]
    [argv] = watched.delivered
    assert argv[0].endswith("tmux_lane_message.sh")
    assert argv[1:5] == ["r1", "watchdog", "tester", "wrapup"]
    assert argv[argv.index("--minutes") + 1] == "5" and argv[argv.index("--runs") + 1] == "/runs"
    [event] = [row for row in watched.events() if row["kind"] == "lane-wrap-up"]
    assert event["wake"] is True
    # Time between the interrupt and the resumed turn is not work.
    watched.resume(1600)
    assert watched.at(1600 + 299) == "watching"
    assert watched.at(1600 + 300) == "capped"  # 1500 s + 300 s
    assert len(watched.delivered) == 1  # one wrap-up per lane
    assert watched.stops == ["r1:tester", "r1:tester"]


def test_an_undelivered_wrap_up_ends_the_lane_as_capped(tmp_path: Path) -> None:
    watched = Watched(tmp_path, caps={"wall_cap_s": 1800}, watch=RESUMABLE, thread=True)
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1500) == "wrapping"
    assert watched.at(1500 + 300) == "wrapping"
    assert watched.at(1500 + 301) == "capped"
    [event] = [row for row in watched.events() if row["kind"] == "refusal-lane-cap"]
    assert "did not resume it" in str(event["detail"])
    # Whatever became of the notice, the lane is stopped again before it is left unwatched.
    assert watched.stops == ["r1:tester", "r1:tester"]


def test_a_wrap_up_that_cannot_start_finishes_only_after_verified_stop(tmp_path: Path) -> None:
    watched = Watched(tmp_path, caps={"wall_cap_s": 1800}, watch=RESUMABLE, thread=True)
    watched.watch.deliver = lambda argv: "not started: no environment"
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1500) == "capped" and watched.stops == ["r1:tester"]
    assert watched.at(1600) == "capped"  # no restart or repeat delivery


def test_the_wrap_up_delivery_runs_in_its_own_session_from_the_recorded_environment(
    tmp_path: Path,
) -> None:
    import time

    env_file = tmp_path / "coder-delivery.env"
    env_file.write_text(f"PATH={os.environ['PATH']}\nFACTORY_GENESIS=/keys/genesis\n")
    env_file.chmod(0o644)
    log = tmp_path / "coder-wrapup.log"
    # The session id portably: `ps -o sid=` is Linux-only (macOS ps has no sid keyword).
    probe = ["sh", "-c", 'echo "genesis=$FACTORY_GENESIS home=${HOME:-none}"; "$0" -c '
             '"import os; print(os.getsid(0))"', sys.executable]
    assert lane_watchdog.spawn_delivery(probe, env_file, log).startswith("not started")
    env_file.chmod(0o600)
    assert lane_watchdog.spawn_delivery(probe, env_file, log).startswith("started")
    for _ in range(50):
        if log.exists() and len(log.read_text().splitlines()) >= 2:
            break
        time.sleep(0.1)
    shown, session = log.read_text().splitlines()[:2]
    assert shown == "genesis=/keys/genesis home=none"  # exactly the recorded names
    assert int(session) != os.getsid(0)  # survives the watcher's window being killed


def test_a_lane_without_a_resumable_thread_is_stopped_at_the_cap_without_a_wrap_up(
    tmp_path: Path,
) -> None:
    watched = Watched(tmp_path, caps={"wall_cap_s": 1800}, watch=RESUMABLE, thread=False)
    _commit(watched.repo, T0 + 100, "a")
    assert watched.at(1500) == "watching"
    assert watched.at(1800) == "capped" and watched.delivered == []


def test_a_coder_is_paused_for_review_and_its_paused_time_is_not_work(tmp_path: Path) -> None:
    """Terminal-Bench 4: reviews every 18 minutes against one review at the first stop raised
    solves from 15.8% to 28.9% (+13 points, 95% CI 0 to +26) with the same models."""

    watched = Watched(tmp_path, watch={"runs_dir": "/runs", "review_every_s": 1080,
                                       "review_wait_s": 900}, thread=True)
    _commit(watched.repo, T0 + 100, "a")
    _commit(watched.repo, T0 + 1000, "b")
    assert watched.at(1079) == "watching"
    assert watched.at(1080) == "paused" and watched.stops == ["r1:tester"]
    [due] = [row for row in watched.events() if row["kind"] == "lane-review-due"]
    assert due["wake"] is True and "tmux_lane_message.sh r1 validator tester review" in str(
        due["detail"])
    # Paused: no stall, no cap. A late review is a stuck effect: the overdue notice re-emits with
    # its age every review wait, then the pause emits expired once and stays paused.
    assert watched.at(1080 + 900) == "paused"
    assert watched.at(1080 + 901) == "paused"
    assert watched.at(1080 + 1200) == "paused"  # still inside the second wait: no repeat yet
    assert watched.at(1080 + 1801) == "paused"
    kinds = [row["kind"] for row in watched.events()]
    assert kinds.count("lane-review-overdue") == 2 and "lane-review-expired" not in kinds
    assert watched.at(1080 + 3601) == "paused"
    assert watched.at(1080 + 5000) == "paused"
    kinds = [row["kind"] for row in watched.events()]
    assert kinds.count("lane-review-overdue") == 2 and kinds.count("lane-review-expired") == 1
    [expired] = [row for row in watched.events() if row["kind"] == "lane-review-expired"]
    assert "60 min expiry" in str(expired["detail"])
    # The review resumes the lane: the next pause comes after another 18 minutes of work.
    watched.resume(9000)
    _commit(watched.repo, T0 + 9500, "c")
    assert watched.at(9000 + 1079) == "watching"
    assert watched.at(9000 + 1080) == "paused"
    assert json.loads((watched.lanes / "tester-watch.json").read_text())["reviews"] == 2


def test_a_review_pause_carries_the_stall_window_instead_of_resetting_it(tmp_path: Path) -> None:
    """Without the carry, a pause every 18 minutes would grant a fresh 15-minute read allowance
    each time, and a Coder that stopped committing would never be stall-stopped."""

    watched = Watched(tmp_path, watch={"runs_dir": "/runs", "review_every_s": 1080},
                      thread=True)
    _commit(watched.repo, T0 + 120, "only")
    assert watched.at(1080) == "paused"  # 180 s past the read allowance, carried
    watched.resume(5000)
    assert watched.at(5000 + 1019) == "watching"  # 1019 + 180 = 1199 s idle
    assert watched.at(5000 + 1021) == "stalled"


def test_a_review_with_lost_exit_evidence_becomes_pending_not_authorized_work(
    tmp_path: Path,
) -> None:
    watched = Watched(tmp_path, watch={"runs_dir": "/runs", "review_every_s": 1080},
                      thread=True)
    _commit(watched.repo, T0 + 600, "a")
    assert watched.at(1080) == "paused"
    live = subprocess.CompletedProcess((), 0, "tester @6\n", "")
    watched.watch.tmux = lambda *a: (
        live if a[0] == "list-windows" else subprocess.CompletedProcess(a, 0, "0\n", ""))
    assert watched.at(1080 + 30) == "stopping"
    assert watched.at(1080 + 61) == "stopping"
    assert [row["kind"] for row in watched.events()].count("lane-review-unverified") == 1


def test_a_new_round_closes_a_slot_left_paused(tmp_path: Path) -> None:
    watched = Watched(tmp_path, watch={"runs_dir": "/runs", "review_every_s": 1080},
                      thread=True)
    _commit(watched.repo, T0 + 600, "a")
    assert watched.at(1080) == "paused"
    assert lane_watchdog.close_superseded(
        watched.root, "tester", "tester.r2", tmux=watched.watch.tmux,
        processes=lambda: {},
    ) == ["tester"]
    [output] = watched.rows("lane-output.jsonl")
    assert output["outcome"] == "superseded" and output["commits"] == 1
    assert watched.at(9000) == "superseded"
    assert lane_watchdog.close_superseded(watched.root, "tester", "tester.r2",
                                           tmux=watched.watch.tmux, processes=lambda: {}) == []


def test_review_schedule_is_independent_of_questions_but_respects_wrap_up_headroom(
    tmp_path: Path,
) -> None:
    from harness.lane_dialogue import record_question

    near = Watched(tmp_path / "near", caps={"wall_cap_s": 1800},
                   watch={**RESUMABLE, "review_every_s": 1260}, thread=True)
    _commit(near.repo, T0 + 100, "a")
    assert near.at(1260) == "watching"  # 540 s left, inside the wrap-up plus headroom
    assert near.at(1500) == "wrapping"
    asking = Watched(tmp_path / "asking", watch={"runs_dir": "/runs", "review_every_s": 600},
                     thread=True)
    record_question(asking.root, "tester", "Which clock does the contract's `at` mean?")
    assert asking.at(700) == "paused" and asking.stops == ["r1:tester"]
    unrecorded = Watched(tmp_path / "unrecorded", watch={"runs_dir": "/runs",
                                                         "review_every_s": 600}, thread=True)
    (unrecorded.lanes / "tester-delivery.env").unlink()
    _commit(unrecorded.repo, T0 + 100, "a")
    assert unrecorded.at(700) == "watching" and unrecorded.stops == []


def test_remaining_reports_the_cap_as_the_watchdog_counts_it(tmp_path: Path) -> None:
    watched = Watched(tmp_path, caps={"wall_cap_s": 1800})
    watched.at(600)
    assert lane_watchdog.remaining(watched.root, "tester", now=watched.clock).startswith(
        "time left: 20m 00s of this lane's 30m cap")
    uncapped = Watched(tmp_path / "uncapped")
    assert lane_watchdog.remaining(uncapped.root, "tester") == "no wall-clock cap on this lane"


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


# -- 6. one Coder and one Tester, each one long-lived session (founder, 2026-10-20) ---------


def test_a_run_has_one_tester_and_refuses_a_second_instance(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    assert lanes.lane("tester").returncode == 0
    second = lanes.lane("tester", "--instance", "b", repo=lanes.clone("tester-b"))
    assert second.returncode == 64 and "a run has one tester" in second.stderr
    assert "tmux_lane_message.sh" in second.stderr and "one factory per model" in second.stderr
    assert lanes.windows("tester-b") == []


def test_new_work_goes_into_the_session_and_a_round_replaces_only_a_dead_one(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    assert lanes.lane("coder").returncode == 0
    again = lanes.lane("coder")
    assert again.returncode == 64 and "tmux_lane_message.sh" in again.stderr
    (lanes.root / "tmux-lanes" / "coder-thread-id").write_text("thread-1\n")
    fresh = lanes.lane("coder", "--round", "r2")
    assert fresh.returncode == 64 and "new work goes into it, not a new round" in fresh.stderr
    assert not (lanes.root / "tmux-lanes" / "coder.r2-prompt.txt").exists()
    replaced = lanes.lane("coder", "--round", "r2", "--replace-session", "transcript corrupt")
    assert replaced.returncode == 0, replaced.stdout + replaced.stderr
    assert (lanes.root / "tmux-lanes" / "coder.r2-prompt.txt").is_file()
    events = [json.loads(line) for line in (lanes.root / "events.jsonl").read_text().splitlines()]
    [fresh_event] = [e for e in events if e.get("kind") == "fresh-session"]
    assert fresh_event["detail"] == "coder.r2 replaces coder's session: transcript corrupt"
    assert fresh_event["wake"] is True


def test_a_plan_lane_has_no_time_limit(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    refused = lanes.lane("tester", "--wall-cap-minutes", "60")
    assert refused.returncode == 64 and "a plan-billed lane has no time limit" in refused.stderr
    assert lanes.windows("tester") == []


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
    row = read_chain(lanes.root / "tmux-lanes" / "tester-launch.jsonl")[-1]
    # The prompt is a main prompt plus references (founder, 2026-10-18): the standard travels as
    # a file in the lane's own .git/factory-brief/, named in the prompt with its sha256.
    (brief,) = [item for item in row["brief"] if item["path"].endswith("testing-standard.md")]
    body = Path(brief["path"]).read_text(encoding="utf-8")
    for key, path in (("testing-standard", standard), ("tester-doctrine", doctrine),
                      ("testing-strategy", strategy)):
        assert f"<<<FACTORY_TESTING_STANDARD {key} digest={_sha(path)}>>>" in body
        assert path.read_text(encoding="utf-8").strip() in body
        assert path.read_text(encoding="utf-8").strip() not in prompt
    assert f"{brief['path']} (" in prompt and brief["sha256"] in prompt
    assert brief["sha256"] == "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert "/.git/factory-brief/" in brief["path"]
    assert prompt.index("FACTORY TMUX LANE PROTOCOL") < prompt.index("READ FIRST")
    assert row["prompt_bytes"] == len(prompt.encode("utf-8"))
    assert row["testing_standard"]["sources"]["testing-standard"]["digest"] == _sha(standard)
    # The Coder's prompt does not carry it.
    assert lanes.lane("coder").returncode == 0
    coder = (lanes.root / "tmux-lanes" / "coder-prompt.txt").read_text(encoding="utf-8")
    assert "FACTORY_TESTING_STANDARD" not in coder


def test_a_coder_prompt_carries_its_clock_and_its_review_cadence(
    tmp_path: Path,
) -> None:
    """A capped (metered) Coder lane is told its cap, how to read what is left of it, the wrap-up
    it will get, and that the Validator will pause it for reviews."""

    lanes = Lanes(tmp_path, budget="50")
    _ollama_stub(lanes.stub, available=True)
    record_passing_qualification(lanes.root.parent, "coder", "codex-ollama", "glm-test:cloud")
    launched = lanes.lane("coder", "--agent", "codex-ollama", "--model", "glm-test:cloud",
                          *METERED_CAPS, "--wall-cap-minutes", "60")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    prompt = (lanes.root / "tmux-lanes" / "coder-prompt.txt").read_text(encoding="utf-8")
    assert (prompt.index("FACTORY TMUX LANE PROTOCOL")
            < prompt.index("READ FIRST"))
    assert "factory-brief/task.txt (the verbatim lane task;" in prompt
    assert "capped at 60 minutes of active time" in prompt
    assert "lane_watchdog.py remaining --root" in prompt
    assert "About 5 minutes before the cap you receive FACTORY_WRAP_UP" in prompt
    assert "Every 18 minutes of work the watchdog pauses this lane" in prompt
    assert "FACTORY_TESTING_STANDARD" not in prompt
    assert "FACTORY_CODER_DOCTRINE" not in prompt
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert "coder_doctrine" not in row
    assert row["testing_standard"] is None
    assert row["watch"]["wrap_s"] == 300 and row["watch"]["review_every_s"] == 1080
    assert row["watch"]["runs_dir"] == str(lanes.root.parent)
    # What the watcher needs to deliver the wrap-up itself, owner-only.
    delivery = lanes.root / "tmux-lanes" / "coder-delivery.env"
    assert delivery.stat().st_mode & 0o777 == 0o600
    recorded = dict(line.split("=", 1) for line in delivery.read_text().splitlines())
    assert {"PATH", "FACTORY_CLI", "FACTORY_GENESIS", "FACTORY_ROOT_PUBLIC_KEY",
            "FACTORY_RESUME_CHECKPOINT", "FACTORY_RESUME_CONFIG_MANIFEST"} <= set(recorded)


def test_review_pauses_are_for_resumable_coder_launches_and_can_be_turned_off(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    refused = lanes.lane("tester", "--review-every-minutes", "10")
    assert refused.returncode == 64 and "Coder launch only" in refused.stderr
    refused = lanes.lane("coder", "--agent", "codex-interactive", "--review-every-minutes", "10")
    assert refused.returncode == 64 and "need a resumable lane" in refused.stderr
    assert lanes.lane("coder", "--review-every-minutes", "0").returncode == 0
    prompt = (lanes.root / "tmux-lanes" / "coder-prompt.txt").read_text(encoding="utf-8")
    assert "pauses this lane" not in prompt and "capped at" not in prompt
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert row["watch"]["review_every_s"] == 0 and row["watch"]["wrap_s"] == 0


def test_a_tester_launch_is_refused_when_the_standard_cannot_be_injected(
    tmp_path: Path,
) -> None:
    lanes = Lanes(tmp_path)
    strategy = lanes.root / "artifacts" / "testing-strategy.md"
    strategy.parent.mkdir(exist_ok=True)
    (tmp_path / "elsewhere.md").write_text("not the run's strategy\n")
    strategy.unlink()
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


@pytest.mark.parametrize("action", ["launch", "qualify"])
def test_tester_without_run_strategy_is_refused_before_start(tmp_path: Path, action: str) -> None:
    lanes = Lanes(tmp_path)
    (lanes.root / "artifacts" / "testing-strategy.md").unlink()
    result = lanes.lane("tester", action=action)
    assert result.returncode == 70 and "testing strategy" in result.stderr
    assert lanes.windows("tester") == []
    assert not list((lanes.root / "tmux-lanes").glob("*-launch.jsonl"))


@pytest.mark.parametrize("source_kind", ["empty", "whitespace", "directory", "fifo", "utf8"])
def test_tester_refuses_unusable_strategy_without_waiting_for_input(
    tmp_path: Path, source_kind: str,
) -> None:
    import os

    strategy = tmp_path / "strategy.md"
    if source_kind == "directory":
        strategy.mkdir()
    elif source_kind == "fifo":
        os.mkfifo(strategy)
    else:
        strategy.write_bytes({"empty": b"", "whitespace": b" \n", "utf8": b"\xff"}[source_kind])
    result = run(
        [sys.executable, str(HARNESS / "testing_standard.py"), "block",
         "--factory-home", str(REPO), "--strategy", str(strategy),
         "--output", str(tmp_path / "injected.md")],
        tmp_path,
    )
    assert result.returncode == 70 and "testing strategy" in result.stderr
    assert not (tmp_path / "injected.md").exists()


# -- 8. the dependency cache and runtime reach the lane's commands (dogfood #31/#61) ---------


def _read_only_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    path.chmod(0o555)
    return path


def _policy(call: str) -> str:
    import shlex

    [policy] = [word for word in shlex.split(call)
                if word.startswith("shell_environment_policy=")]
    return policy


def test_a_lanes_commands_get_the_cache_and_runtime_on_launch_and_resume(tmp_path: Path) -> None:
    """Codex runs lane commands under inherit="core", so a DENO_DIR in Codex's own env never
    reaches `deno`: the variable must be in the shell policy, on launch and on every resume."""

    lanes = Lanes(tmp_path / "run")
    store = _read_only_dir(tmp_path / "dep-cache" / "deno-dir")
    runtime = _read_only_dir(tmp_path / "deno-2.7.14" / "bin")
    launched = lanes.lane("coder", "--cache-env", f"DENO_DIR={store}",
                          "--runtime-dir", str(runtime))
    assert launched.returncode == 0, launched.stdout + launched.stderr

    [call] = lanes.windows("coder")
    expected = ('shell_environment_policy={inherit="core",ignore_default_excludes=false,'
                f'set={{DENO_DIR="{store}"}}}}')
    assert _policy(call) == expected
    assert f"PATH={runtime}{os.pathsep}" in call
    row = read_chain(lanes.root / "tmux-lanes" / "coder-launch.jsonl")[-1]
    assert row["lane_env"] == {"cache_env": {"DENO_DIR": str(store)},
                               "runtime_dirs": [str(runtime)]}
    prompt = (lanes.root / "tmux-lanes" / "coder-prompt.txt").read_text()
    assert f"DENO_DIR={store} is set for your commands" in prompt

    (lanes.root / "tmux-lanes" / "coder-thread-id").write_text(
        "12345678-1234-4234-8234-123456789abc\n", encoding="utf-8")
    resumed = run(
        ["bash", str(HARNESS / "tmux_lane_message.sh"), "r1", "validator", "coder", "status",
         "--runs", str(lanes.root.parent)],
        lanes.operator, lanes.env,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    respawn = next(line for line in lanes.tmux_log.read_text().splitlines()
                   if "respawn-pane" in line)
    assert _policy(respawn) == expected
    assert f"PATH={runtime}{os.pathsep}" in respawn


def test_a_writable_or_reserved_lane_environment_is_refused(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path / "run")
    writable = tmp_path / "writable-cache"
    writable.mkdir()
    refused = lanes.lane("coder", "--cache-env", f"DENO_DIR={writable}")
    assert refused.returncode == 64 and "writable" in refused.stderr
    store = _read_only_dir(tmp_path / "store")
    reserved = lanes.lane("coder", "--cache-env", f"HOME={store}")
    assert reserved.returncode == 64 and "reserved" in reserved.stderr
    assert lanes.windows("coder") == []



@pytest.mark.parametrize("shape", ["linked-store", "linked-ancestor", "writable-file",
                                   "writable-subdir", "inner-link"])
def test_a_store_that_can_change_after_admission_is_refused(tmp_path: Path, shape: str) -> None:
    """Review of #31/#61: the read-only check covers every path component and every entry."""

    lanes = Lanes(tmp_path / "run")
    real = tmp_path / "real" / "deno-dir"
    (real / "deps").mkdir(parents=True)
    (real / "deps" / "mod.ts").write_text("export {};\n")
    store = real
    if shape == "linked-store":
        store = tmp_path / "store-link"
        store.symlink_to(real)
    elif shape == "linked-ancestor":
        (tmp_path / "parent-link").symlink_to(tmp_path / "real")
        store = tmp_path / "parent-link" / "deno-dir"
    elif shape == "inner-link":
        (real / "deps" / "outside").symlink_to(tmp_path)
    for path in [real / "deps" / "mod.ts", real / "deps", real]:
        path.chmod(0o444 if path.is_file() else 0o555)
    if shape == "writable-file":
        (real / "deps").chmod(0o755)
        (real / "deps" / "mod.ts").chmod(0o644)
        (real / "deps").chmod(0o555)
    elif shape == "writable-subdir":
        (real / "deps").chmod(0o755)
    try:
        refused = lanes.lane("coder", "--cache-env", f"DENO_DIR={store}")
    finally:
        for path in [real, real / "deps"]:
            path.chmod(0o755)
    assert refused.returncode == 64, refused.stdout + refused.stderr
    assert ("symlink" if "link" in shape else "writable") in refused.stderr
    assert lanes.windows("coder") == []

# -- 9. the Tester lane against the agreed Strategy (dogfood #36, #65, #73) ----------------


def test_a_tester_without_the_strategys_oracle_inputs_does_not_launch(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path / "run")
    upstream = b"route: /site\n"
    strategy = lanes.root / "artifacts" / "testing-strategy.md"
    strategy.write_text(strategy.read_text() + "oracle-input: tools/site-route.yaml sha256:"
                        + hashlib.sha256(upstream).hexdigest() + "\n")
    refused = lanes.lane("tester")
    assert refused.returncode == 70 and "tools/site-route.yaml (absent)" in refused.stderr
    assert lanes.windows("tester") == []
    (lanes.clone("tester") / "tools").mkdir()
    (lanes.clone("tester") / "tools" / "site-route.yaml").write_bytes(upstream)
    launched = lanes.lane("tester")
    assert launched.returncode == 0, launched.stdout + launched.stderr


def test_a_tester_with_a_critical_planned_case_unwritten_does_not_freeze(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path / "run")
    strategy = lanes.root / "artifacts" / "testing-strategy.md"
    strategy.write_text(strategy.read_text() + "planned-case: R16.11-a Critical\n"
                        "test-modules: *_test.ts\n")
    assert lanes.lane("tester").returncode == 0
    clone = lanes.clone("tester")
    (clone / "cap_test.ts").write_text('Deno.test("request cap", () => {});\n')

    def freeze() -> subprocess.CompletedProcess[str]:
        return run(["bash", str(HARNESS / "tmux_lane.sh"), "r1", "tester", "freeze",
                    "--runs", str(lanes.root.parent)], lanes.operator, lanes.env)

    refused = freeze()
    assert refused.returncode == 70 and "R16.11-a" in refused.stderr
    (clone / "cap_test.ts").write_text('Deno.test("R16.11-a: request cap", () => {});\n')
    frozen = freeze()
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    row = read_chain(lanes.root / "tmux-lanes" / "tester-launch.jsonl")[-1]
    assert row["traceability_gaps"] == {"Critical": [], "Standard": [], "Cosmetic": []}


def test_a_lane_window_keeps_remain_on_exit_as_a_window_option(tmp_path: Path) -> None:
    """Live drill: tmux 3.7 ignores the session-level remain-on-exit, so a finished lane's
    window vanished (the "window gone" family behind dogfood #29 and #69)."""
    lanes = Lanes(tmp_path)
    launched = lanes.lane("coder")
    assert launched.returncode == 0, launched.stdout + launched.stderr
    calls = lanes.tmux_log.read_text().splitlines()
    assert any("set-option -w -t r1:coder remain-on-exit on" in call for call in calls), calls
    assert not any(call.endswith("set-option -t r1 remain-on-exit on") for call in calls)


@pytest.mark.parametrize('purpose', ['review', 'wrapup'])
def test_review_and_wrapup_wait_for_exit_before_delivery(tmp_path, purpose):
    settings = {'runs_dir': '/runs', 'review_every_s': 600} if purpose == 'review' else RESUMABLE
    watched = Watched(tmp_path, caps={} if purpose == 'review' else {'wall_cap_s': 1800},
                      watch=settings, thread=True)
    _commit(watched.repo, T0 + 100, 'a')
    live = True

    def tmux(*args):
        return subprocess.CompletedProcess(args, 0, 'tester @1\n' if args[0] == 'list-windows'
                                           else '0\n' if live else '1\n', '')

    watched.watch.tmux = tmux
    at = 600 if purpose == 'review' else 1500
    assert watched.at(at) == 'stopping'
    assert not watched.delivered
    assert not any(r['kind'] == 'lane-review-due' for r in watched.events())
    assert watched.at(at + 1) == 'stopping'
    assert len(watched.stops) == 1
    live = False
    assert watched.at(at + 2) == ('paused' if purpose == 'review' else 'wrapping')
    assert len(watched.delivered) == (0 if purpose == 'review' else 1)
    assert json.loads(watched.watch.state_path.read_text())['stop_verified_ts'] == T0 + at + 2


def test_supersession_refuses_live_unknown_and_pending_old_round(tmp_path):
    watched = Watched(tmp_path, watch={'runs_dir': '/runs', 'review_every_s': 600}, thread=True)
    assert watched.at(600) == 'paused'
    def alive(*a):
        return subprocess.CompletedProcess(
            a, 0, 'tester @1\n' if a[0] == 'list-windows' else '0\n', '')
    for tmux, processes in [(alive, lambda: {}), (watched.watch.tmux, lambda: None)]:
        with pytest.raises(lane_watchdog.LaneWatchError, match='unverified'):
            lane_watchdog.close_superseded(watched.root, 'tester', 'tester.r2',
                                           tmux=tmux, processes=processes)
    assert not watched.rows('lane-output.jsonl')
    state = json.loads(watched.watch.state_path.read_text())
    state['status'] = 'stopping'
    watched.watch.state_path.write_text(json.dumps(state))
    with pytest.raises(lane_watchdog.LaneWatchError, match='unverified'):
        lane_watchdog.close_superseded(watched.root, 'tester', 'tester.r2',
                                       tmux=watched.watch.tmux, processes=lambda: {})


def test_kickoff_without_actual_process_binding_cannot_admit_an_author(tmp_path):
    lanes = Lanes(tmp_path)
    metadata = json.loads((lanes.root / 'harness.json').read_text())
    metadata['validator_agent'] = 'kickoff'
    (lanes.root / 'harness.json').write_text(json.dumps(metadata))
    result = lanes.lane('coder')
    assert result.returncode == 70
    assert 'executing Validator identity/control is unavailable' in result.stderr
    assert not lanes.windows('coder')


def test_replaced_window_cannot_certify_old_lane_exit(tmp_path):
    watched = Watched(tmp_path, watch={'runs_dir': '/runs', 'review_every_s': 600}, thread=True)
    assert watched.at(600) == 'paused'
    watched.watch.tmux = lambda *a: subprocess.CompletedProcess(
        a, 0, 'tester @999\n' if a[0] == 'list-windows' else '1\n', '')
    assert watched.at(601) == 'stopping'
    assert watched.at(602) == 'stopping'
    assert not watched.rows('lane-output.jsonl')
