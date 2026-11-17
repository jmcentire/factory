package setup

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// testEnv returns an Env rooted in a temporary home with every external
// command stubbed out, so configuration writers can run for real.
func testEnv(t *testing.T, o Options) *Env {
	t.Helper()
	home := t.TempDir()
	o.Home = home
	// Always confine repositories to the temporary home. DefaultOptions fills
	// CodeDir with the real ~/Code, and a test must never write there.
	o.CodeDir = filepath.Join(home, "Code")
	t.Setenv("HOME", home)
	// A developer's shell may point Claude Code at another config dir; tests
	// must stay inside the temporary home.
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	t.Setenv("CODEX_HOME", "")
	env := NewEnv(o)
	env.lookPath = func(string) (string, error) { return "", exec.ErrNotFound }
	env.run = func(context.Context, *exec.Cmd) error { return nil }
	return env
}

func allComponents() Options {
	o := DefaultOptions()
	o.Components = AllComponents
	return o
}

func TestConfigureEveryAgentIsIdempotent(t *testing.T) {
	for _, a := range Agents() {
		t.Run(string(a.ID), func(t *testing.T) {
			o := allComponents()
			o.Agents = []AgentID{a.ID}
			env := testEnv(t, o)
			if a.configured(env, a) {
				t.Fatal("reported configured before configuring")
			}
			if err := a.configure(context.Background(), env, a); err != nil {
				t.Fatalf("configure: %v", err)
			}
			if !a.configured(env, a) {
				t.Fatal("not configured after configure")
			}
			// Snapshot, run again, compare: nothing may change.
			before := snapshot(t, env.Opts.Home)
			if err := a.configure(context.Background(), env, a); err != nil {
				t.Fatalf("second configure: %v", err)
			}
			after := snapshot(t, env.Opts.Home)
			if before != after {
				t.Fatalf("second configure changed files:\n%s\n---\n%s", before, after)
			}
		})
	}
}

func TestClaudeConfigMergesExistingKeys(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	env := testEnv(t, o)
	_, settings, mcp := claudePaths(env.Opts)
	must(t, os.MkdirAll(filepath.Dir(settings), 0o755))
	must(t, os.WriteFile(settings, []byte(`{"theme":"dark","hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"echo hi"}]}]}}`), 0o600))
	must(t, os.WriteFile(mcp, []byte(`{"mcpServers":{"other":{"type":"stdio","command":"x"}},"foo":1}`), 0o600))

	must(t, configureClaude(context.Background(), env, nil))

	m := readJSONT(t, settings)
	if m["theme"] != "dark" {
		t.Fatal("unrelated settings key lost")
	}
	list := m["hooks"].(map[string]any)["PreToolUse"].([]any)
	if len(list) != 2 {
		t.Fatalf("expected existing hook kept and ours added, got %d entries", len(list))
	}
	if !hasHook(m, "PreToolUse", policyHookCommand(env.Opts, "")) {
		t.Fatal("policy hook missing")
	}
	mm := readJSONT(t, mcp)
	servers := mm["mcpServers"].(map[string]any)
	for _, want := range []string{"other", "kindex", "constrain", "pact", "transmogrifier", "signet"} {
		if servers[want] == nil {
			t.Fatalf("mcp server %q missing", want)
		}
	}
	if mm["foo"] != float64(1) {
		t.Fatal("unrelated mcp key lost")
	}
}

func TestPiMCPServersMergeIntoMCPJSON(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentPi}
	env := testEnv(t, o)
	_, mcp, _ := piLayout(piRoot(env.Opts), piMCPFile)
	must(t, os.MkdirAll(filepath.Dir(mcp), 0o755))
	must(t, os.WriteFile(mcp, []byte(`{"mcpServers":{"docs":{"url":"https://example.com/mcp"}},"foo":1}`), 0o600))
	must(t, configurePi(context.Background(), env, nil))
	if !configuredPi(env, nil) {
		t.Fatal("pi not reported configured after configure")
	}
	m := readJSONT(t, mcp)
	servers := m["mcpServers"].(map[string]any)
	if servers["docs"] == nil || m["foo"] != float64(1) {
		t.Fatalf("user entries lost: %v", m)
	}
	kin, _ := servers["kindex"].(map[string]any)
	if kin["command"] != env.Opts.DevshellBin("kin-mcp") || kin["type"] != "stdio" {
		t.Fatalf("kindex entry wrong: %v", kin)
	}
	for _, name := range []string{"constrain", "pact", "transmogrifier", "signet"} {
		if servers[name] == nil {
			t.Errorf("mcp server %q missing", name)
		}
	}
}

func TestPromptLoadersPointAtCheckout(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	must(t, configureCodex(context.Background(), env, nil))
	skills, config, hooks := codexPaths(env.Opts)
	b, err := os.ReadFile(filepath.Join(skills, "validate", "SKILL.md"))
	must(t, err)
	if !strings.Contains(string(b), filepath.Join(env.Opts.FactoryDir(), "prompts", "validate.md")) {
		t.Fatalf("loader does not reference checkout:\n%s", b)
	}
	if !strings.HasPrefix(string(b), "---\nname: validate\ndescription: \"") {
		t.Fatalf("missing skill frontmatter:\n%s", b)
	}
	// Lanes are explicit-only; /factory stays reachable by "use the factory".
	policy, err := os.ReadFile(filepath.Join(skills, "build", "agents", "openai.yaml"))
	must(t, err)
	if !strings.Contains(string(policy), "allow_implicit_invocation: false") {
		t.Fatalf("build skill is implicitly invocable:\n%s", policy)
	}
	if Exists(filepath.Join(skills, "factory", "agents", "openai.yaml")) {
		t.Fatal("/factory must stay implicitly invocable")
	}
	cfg, err := os.ReadFile(config)
	must(t, err)
	for _, want := range []string{"[mcp_servers.kindex]", "[mcp_servers.signet]", `args = ["serve"]`, "[features]", "hooks = true"} {
		if !strings.Contains(string(cfg), want) {
			t.Fatalf("config.toml missing %q:\n%s", want, cfg)
		}
	}
	h := readJSONT(t, hooks)
	if !hasHook(h, "PreToolUse", policyHookCommand(env.Opts, "codex")) || !hasHook(h, "PermissionRequest", policyHookCommand(env.Opts, "codex-permission")) {
		t.Fatal("codex hooks missing")
	}
}

func TestEveryLoaderFrontmatterIsValidYAML(t *testing.T) {
	// Descriptions contain ": ", which an unquoted YAML scalar cannot hold, so
	// a strict YAML parser rejects the frontmatter. Every description must be
	// a double-quoted scalar that round-trips.
	o := allComponents()
	o.Agents = nil
	for _, a := range Agents() {
		o.Agents = append(o.Agents, a.ID)
	}
	env := testEnv(t, o)
	for _, a := range Agents() {
		must(t, a.configure(context.Background(), env, a))
	}
	byDesc := map[string]bool{}
	for _, p := range FactoryPrompts {
		byDesc[p.Description] = true
	}
	checked := 0
	err := filepath.WalkDir(env.Opts.Home, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".md") {
			return err
		}
		b, err := os.ReadFile(path)
		if err != nil || !strings.HasPrefix(string(b), "---\n") {
			return err
		}
		front := strings.SplitN(string(b)[4:], "\n---\n", 2)[0]
		for _, line := range strings.Split(front, "\n") {
			v, ok := strings.CutPrefix(line, "description: ")
			if !ok {
				continue
			}
			var desc string
			if err := json.Unmarshal([]byte(v), &desc); err != nil || !byDesc[desc] {
				t.Errorf("%s: description is not a quoted scalar that round-trips: %s", path, line)
			}
			checked++
		}
		return nil
	})
	must(t, err)
	// Every agent writes one loader per prompt.
	if want := len(FactoryPrompts) * len(Agents()); checked != want {
		t.Fatalf("checked %d descriptions, want %d", checked, want)
	}
	guarded := false
	for d := range byDesc {
		guarded = guarded || strings.Contains(d, ": ")
	}
	if !guarded {
		t.Fatal(`no description contains ": "; the test no longer guards anything`)
	}
}

func TestDryRunWritesNothing(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude, AgentOpenCode, AgentAntigravity}
	o.DryRun = true
	env := testEnv(t, o)
	env.DryRun = true
	for _, id := range o.Agents {
		a, _ := agentByID(id)
		must(t, a.configure(context.Background(), env, a))
	}
	must(t, policyRulesStep{}.Apply(context.Background(), env))
	if s := snapshot(t, env.Opts.Home); s != "" {
		t.Fatalf("dry run wrote files:\n%s", s)
	}
}

func TestBuildPlanOrdersAndValidates(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	o.Home = t.TempDir()
	p, err := BuildPlan(o)
	must(t, err)
	var ids []string
	for _, s := range p.Steps {
		ids = append(ids, s.ID())
	}
	got := strings.Join(ids, " ")
	for _, pair := range [][2]string{
		{"nix.install", "devshell.clone"},
		{"devshell.build", "policy.rules"},
		{"factory.clone", "factory.venv"},
		{"factory.venv", "factory.skill"},
		{"policy.rules", "agent.claude-code.configure"},
		{"agent.claude-code.configure", "agent.claude-code.memory"},
	} {
		if strings.Index(got, pair[0]) > strings.Index(got, pair[1]) {
			t.Fatalf("%s must precede %s: %s", pair[0], pair[1], got)
		}
	}
	o.Agents = nil
	if _, err := BuildPlan(o); err == nil {
		t.Fatal("agents component without agents should fail validation")
	}
	o.Agents = []AgentID{"nope"}
	if _, err := BuildPlan(o); err == nil {
		t.Fatal("unknown agent should fail validation")
	}
}

func TestFactorySkillRendersBindingsAndIsIdempotent(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude, AgentCodex}
	env := testEnv(t, o)
	s := factorySkillStep{}
	if st, _, _ := s.Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("expected missing, got %v", st)
	}
	must(t, s.Apply(context.Background(), env))
	b, err := os.ReadFile(env.Opts.SkillPath())
	must(t, err)
	body := string(b)
	for _, want := range []string{
		env.Opts.FactoryDir() + "/prompts/validate.md",
		env.Opts.FactoryDir() + "/docs/standards/",
		`export FACTORY_HOME="` + env.Opts.FactoryDir() + `" FACTORY_RUNS_DIR="<target>/.factory/runs"`,
		`export FACTORY_VALIDATOR_AGENT="kickoff"`,
		`harness/factory.sh <run> "<target>/.factory/tasks/<run>.md" --repo "<work>" --runs "$FACTORY_RUNS_DIR"`,
		`"` + env.Opts.RunCheckoutPath() + `" start "<target>" <run> <base> <branch>`,
		`"` + env.Opts.RunCheckoutPath() + `" check "<target>" <run>`,
		`"` + env.Opts.RunCheckoutPath() + `" finish "<target>" <run>`,
		`"` + env.Opts.RunCheckoutPath() + `" list "<target>"`,
		`"` + env.Opts.RunCheckoutPath() + `" clean "<target>" <run>`,
		"ask which runs to clean up",
		"Never clean a run on your own.",
		`--repo "<work>" -- <the project's relevant check command>`,
		"Never commit, switch,\nreset, stash, rebase, merge, pull, clean or push in `<target>`",
		"validator/BRIEF.md",
		"## 2a. Pull context before the run",
		"Pull context on the ticket with Kinbase",
		"RULINGS are ratified direction and govern",
		"`[ticket/SNC-123]`",
		"Ask the human only what\nthe run cannot decide",
		"exact retry command",
		"harness/role_inputs.py prepare",
		"factory-setup --check-update",
		"in their own terminal",
		"harness/factory.sh <run>",
		"open the PR ready for review, never as a draft",
		"`gh pr create --repo <owner/repo> --base <base> --head <branch> --label Factory --title <title> --body-file <file>`",
		"`isResolved == false`",
		"each bot's latest\n   review body",
		"is an impasse only the human can clear",
		"| Validator seat | you, the agent your `/factory` loader names (setup's default: `claude` on `claude-opus-5-5`)",
		"| Orchestrator | `agy` on `gemini-4-carbon-high` |",
		"| Coder lane | `codex` on `gpt-6.1-sol` |",
		"| Tester lane | `cursor-agent` on `grok-4.7-high` |",
		"not a fixed rule",
		`FACTORY_CODER_AGENT="codex" FACTORY_CODER_MODEL="gpt-6.1-sol"`,
		`FACTORY_TESTER_AGENT="cursor-agent" FACTORY_TESTER_MODEL="grok-4.7-high"`,
		`FACTORY_ORCHESTRATOR_AGENT="agy" FACTORY_ORCHESTRATOR_MODEL="gemini-4-carbon-high"`,
		"coder  launch --repo <coder tree>  --prompt coder-brief.md  --agent codex --model gpt-6.1-sol",
		"tester launch --repo <tester tree> --prompt tester-brief.md --agent cursor-agent --model grok-4.7-high",
		"command -v agent && agent --version",
		"never post, comment or send\n  anything as them",
		"1. One ticket per run. Never hand a run the list, a backlog, or several tickets",
		"words for that one ticket: the part of their message naming it, plus any",
		"1; a bare \"yes\" names nothing. Never run \"part 1\".",
		"Claude Code (/factory)",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("skill lacks %q", want)
		}
	}
	// Nothing from the signed-genesis era: no machine-wide authority, no
	// human signing step, no Playbook.
	for _, gone := range []string{".factory/authority", "factory-setup ceremony", "<human>.key", "PLAYBOOK", "Stop for the human",
		"--human-receipt", "{{", `--repo "<target>"`, "<candidate checkout>", "ollama pull", "record-statement", "Stage R", "genesis", "--engagement", "Tessera"} {
		if strings.Contains(body, gone) {
			t.Errorf("skill still mentions %q", gone)
		}
	}
	// Second check is a no-op even though the timestamp differs.
	if st, _, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done after render, got %v", st)
	}
	// The run-checkout script the skill calls is installed, executable, and
	// re-installed when it loses its execute bit.
	if script, err := os.ReadFile(env.Opts.RunCheckoutPath()); err != nil || string(script) != string(runCheckoutScript) {
		t.Fatalf("run-checkout script not installed: %v", err)
	}
	must(t, os.Chmod(env.Opts.RunCheckoutPath(), 0o644))
	if st, _, _ := s.Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("a non-executable run-checkout script must re-apply, got %v", st)
	}
	must(t, s.Apply(context.Background(), env))
	if st, _, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done once the script is executable again, got %v", st)
	}
	// Changing a seat re-renders.
	env.Opts.Seats.Orchestrator = ParseSeat("codex")
	if st, _, _ := s.Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("seat change must re-render, got %v", st)
	}
	// An agent the harness cannot launch blocks with the reason.
	env.Opts.Seats.Coder = ParseSeat("gpt:gpt-6.1-sol")
	if st, detail, _ := s.Check(context.Background(), env); st != StatusBlocked || !strings.Contains(detail, "coder agent") {
		t.Fatalf("expected blocked on bad seat, got %v %q", st, detail)
	}
}

func TestFactoryLoaderPointsAtGeneratedSkill(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	env := testEnv(t, o)
	must(t, configureClaude(context.Background(), env, nil))
	commands, _, _ := claudePaths(env.Opts)
	b, err := os.ReadFile(filepath.Join(commands, "factory.md"))
	must(t, err)
	if !strings.Contains(string(b), env.Opts.SkillPath()) || !strings.Contains(string(b), "The task: $ARGUMENTS") {
		t.Fatalf("/factory loader wrong:\n%s", b)
	}
	// Role loaders bind $FACTORY_HOME, the one location the prompts cite.
	for _, name := range []string{"validate", "build"} {
		b, err := os.ReadFile(filepath.Join(commands, name+".md"))
		must(t, err)
		body := string(b)
		if !strings.Contains(body, filepath.Join(env.Opts.FactoryDir(), "prompts", name+".md")) ||
			!strings.Contains(body, "$FACTORY_HOME on this machine is "+env.Opts.FactoryDir()) ||
			strings.Contains(body, "~/Code") || strings.Contains(body, "PLAYBOOK") {
			t.Fatalf("/%s loader wrong:\n%s", name, body)
		}
	}
}

func TestNormalizeRepo(t *testing.T) {
	cases := map[string]string{
		"https://github.com/wandercom/tooling-devshell":     "github.com/wandercom/tooling-devshell",
		"https://github.com/wandercom/tooling-devshell.git": "github.com/wandercom/tooling-devshell",
		"git@github.com:wandercom/tooling-devshell.git":     "github.com/wandercom/tooling-devshell",
		"ssh://git@github.com/wandercom/tooling-devshell":   "github.com/wandercom/tooling-devshell",
	}
	for in, want := range cases {
		if got := normalizeRepo(in); got != want {
			t.Errorf("normalizeRepo(%q) = %q, want %q", in, got, want)
		}
	}
}

// snapshot renders every regular file under root with its content so tests
// can assert idempotence and dry-run purity.
func snapshot(t *testing.T, root string) string {
	t.Helper()
	var b strings.Builder
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type().IsRegular() {
			c, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			rel, _ := filepath.Rel(root, path)
			b.WriteString(rel + "\n" + string(c) + "\n")
		}
		return nil
	})
	must(t, err)
	return b.String()
}

func readJSONT(t *testing.T, path string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(path)
	must(t, err)
	m := map[string]any{}
	must(t, json.Unmarshal(b, &m))
	return m
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

// A plugin from before the tool-name mapping or the ask block must be rewritten on the next run.
func TestOutdatedPolicyPluginIsRewritten(t *testing.T) {
	for _, id := range []AgentID{AgentOpenCode, AgentPi} {
		o := allComponents()
		o.Agents = []AgentID{id}
		env := testEnv(t, o)
		a, _ := agentByID(id)
		must(t, a.configure(context.Background(), env, a))
		_, _, plugin := opencodePaths(env.Opts)
		if id == AgentPi {
			_, _, plugin = piLayout(piRoot(env.Opts), piMCPFile)
		}
		must(t, os.WriteFile(plugin, []byte(`const tool_name = TOOLS.get(input.tool); if (d === "deny") {}`+"\n"), 0o644))
		if a.configured(env, a) {
			t.Errorf("%s: outdated policy plugin reported configured", id)
		}
	}
}

// The policy's rules match Claude Code tool names (^Bash$) and file_path, so
// the OpenCode and pi hooks must translate their own lowercase tool ids.
func TestPolicyPluginsSendCanonicalToolNames(t *testing.T) {
	node, err := exec.LookPath("node")
	if err != nil {
		t.Skip("node not installed")
	}
	dir := t.TempDir()
	policy := filepath.Join(dir, "policy")
	got := filepath.Join(dir, "payload.json")
	must(t, os.WriteFile(policy, []byte("#!/bin/sh\ncat > '"+got+"'\n"+
		`echo '{"hookSpecificOutput":{"permissionDecision":"'"${DECISION:-deny}"'","permissionDecisionReason":"no"}}'`+"\n"), 0o755))
	opencode := filepath.Join(dir, "opencode.mjs")
	pi := filepath.Join(dir, "pi.mjs")
	must(t, os.WriteFile(opencode, []byte(fmt.Sprintf(opencodePolicyPlugin, policy)), 0o644))
	must(t, os.WriteFile(pi, []byte(fmt.Sprintf(piPolicyExtension, policy)), 0o644))

	const driver = `
const [plugin, agent, tool, args] = process.argv.slice(1);
const m = await import(plugin);
if (agent === "opencode") {
  const hook = (await m.WanderPolicy())["tool.execute.before"];
  try { await hook({ tool }, { args: JSON.parse(args) }); console.log("allow"); } catch (e) { console.log(e.message === "wander policy: no" ? "deny" : e.message); }
} else {
  let handler;
  m.default({ on: (_, fn) => { handler = fn; } });
  const r = await handler({ toolName: tool, input: JSON.parse(args) });
  console.log(r?.block ? (r.reason === "no" ? "deny" : r.reason) : "allow");
}`
	for _, c := range []struct {
		plugin, agent, tool, args, wantTool, wantKey, wantValue string
	}{
		{opencode, "opencode", "bash", `{"command":"sudo ls"}`, "Bash", "command", "sudo ls"},
		{opencode, "opencode", "edit", `{"filePath":"/r/.env","oldString":"a","newString":"b"}`, "Edit", "file_path", "/r/.env"},
		{opencode, "opencode", "write", `{"filePath":"/r/.env","content":"x"}`, "Write", "file_path", "/r/.env"},
		{opencode, "opencode", "read", `{"filePath":"/r/a"}`, "Read", "file_path", "/r/a"},
		{opencode, "opencode", "write", `{"filePath":"/r/.env","file_path":"/r/ok","content":"x"}`, "Write", "file_path", "/r/.env"},
		{opencode, "opencode", "kindex_search", `{"query":"q"}`, "kindex_search", "query", "q"},
		{pi, "pi", "bash", `{"command":"rm -rf /"}`, "Bash", "command", "rm -rf /"},
		{pi, "pi", "powershell", `{"command":"Remove-Item x"}`, "Bash", "command", "Remove-Item x"},
		{pi, "pi", "read", `{"path":"/r/a"}`, "Read", "file_path", "/r/a"},
		{pi, "pi", "edit", `{"path":"/r/.env","edits":[]}`, "Edit", "file_path", "/r/.env"},
		{pi, "pi", "write", `{"path":"/r/.env","file_path":"/r/ok","content":"x"}`, "Write", "file_path", "/r/.env"},
		{pi, "pi", "write", `{"path":"/r/.env","content":"x"}`, "Write", "file_path", "/r/.env"},
		{pi, "pi", "find", `{"pattern":"*.go"}`, "Glob", "pattern", "*.go"},
	} {
		cmd := exec.Command(node, "--input-type=module", "-e", driver, c.plugin, c.agent, c.tool, c.args)
		cmd.Env = append(os.Environ(), "DECISION=deny")
		out, err := cmd.CombinedOutput()
		if err != nil || strings.TrimSpace(string(out)) != "deny" {
			t.Errorf("%s %s: want the policy's deny to block, got %q (%v)", c.agent, c.tool, out, err)
			continue
		}
		var p struct {
			ToolName  string         `json:"tool_name"`
			ToolInput map[string]any `json:"tool_input"`
		}
		b, err := os.ReadFile(got)
		must(t, err)
		must(t, json.Unmarshal(b, &p))
		if p.ToolName != c.wantTool || p.ToolInput[c.wantKey] != c.wantValue {
			t.Errorf("%s %s: sent %s", c.agent, c.tool, b)
		}
	}

	// Neither host can prompt from the hook, so an ask must block with its reason.
	for _, c := range []struct{ plugin, agent, decision, want string }{
		{opencode, "opencode", "ask", "deny"}, {pi, "pi", "ask", "deny"},
		{opencode, "opencode", "allow", "allow"}, {pi, "pi", "allow", "allow"},
	} {
		cmd := exec.Command(node, "--input-type=module", "-e", driver, c.plugin, c.agent, "bash", `{"command":"git push origin main"}`)
		cmd.Env = append(os.Environ(), "DECISION="+c.decision)
		if out, err := cmd.CombinedOutput(); err != nil || strings.TrimSpace(string(out)) != c.want {
			t.Errorf("%s %s: want %s, got %q (%v)", c.agent, c.decision, c.want, out, err)
		}
	}
}

func TestCodexPathsHonourCodexHome(t *testing.T) {
	// A Codex profile (CODEX_HOME) keeps its config, hooks and skills out of ~/.codex.
	o := Options{Home: "/h"}
	t.Setenv("CODEX_HOME", "/cx")
	if s, c, h := codexPaths(o); s != "/cx/skills" || c != "/cx/config.toml" || h != "/cx/hooks.json" {
		t.Fatalf("CODEX_HOME ignored: %s %s %s", s, c, h)
	}
	// A relative CODEX_HOME names one directory, not one per working directory.
	t.Setenv("CODEX_HOME", "rel-codex")
	if _, c, _ := codexPaths(o); !filepath.IsAbs(c) || !strings.HasSuffix(c, "/rel-codex/config.toml") {
		t.Fatalf("relative CODEX_HOME not made absolute: %s", c)
	}
	t.Setenv("CODEX_HOME", "")
	if _, c, _ := codexPaths(o); c != "/h/.codex/config.toml" {
		t.Fatalf("default moved: %s", c)
	}
}
