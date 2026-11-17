package setup

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func write(t *testing.T, path, content string) {
	t.Helper()
	must(t, os.MkdirAll(filepath.Dir(path), 0o755))
	must(t, os.WriteFile(path, []byte(content), 0o755))
}

// fakeAgent is a CLI that reports version and whether it is "current". It has its
// own `update` only when selfUpdate, which makes it current.
func fakeAgent(version string, current, selfUpdate bool, log string) string {
	s := "#!/bin/sh\n" + `here=$(realpath "$0")` + "\n" +
		`if [ "$1" = --version ]; then echo "` + version + `"; exit 0; fi` + "\n"
	if current {
		s += `if [ "$1" = contract ]; then exit 0; fi` + "\n"
	} else {
		s += `if [ "$1" = contract ]; then exit 1; fi` + "\n"
	}
	if selfUpdate {
		s += `if [ "$1" = --help ]; then echo "Commands:"; echo "  update   Update to the latest version"; exit 0; fi` + "\n"
		s += `if [ "$1" = update ]; then echo "self-update" >> ` + log + `; ` +
			`sed -i.bak 's/then exit 1; fi/then exit 0; fi/' "$here"; exit 0; fi` + "\n"
	} else {
		// An old CLI takes an unknown word as a prompt; this one would start a session.
		s += `if [ "$1" = --help ]; then echo "Usage: x [PROMPT]"; exit 0; fi` + "\n"
		s += `if [ "$1" = update ]; then echo "STARTED A SESSION" >> ` + log + `; exit 0; fi` + "\n"
	}
	return s
}

// sandbox lays out a factory checkout whose agent_contract.sh asks the agent itself
// (`<binary> contract`), the agent's real file at real linked from bin/<binary>, and
// brew/npm stand-ins that log and make it current.
type sandbox struct{ root, bin, factory, log string }

func newSandbox(t *testing.T, binary, real string, current, selfUpdate bool) sandbox {
	t.Helper()
	root := t.TempDir()
	sb := sandbox{root, filepath.Join(root, "bin"), filepath.Join(root, "factory"), filepath.Join(root, "calls.log")}
	realPath := filepath.Join(root, real)
	write(t, realPath, fakeAgent(binary+" 1.0", current, selfUpdate, sb.log))
	must(t, os.MkdirAll(sb.bin, 0o755))
	must(t, os.Symlink(realPath, filepath.Join(sb.bin, binary)))
	write(t, filepath.Join(sb.factory, agentContract), "#!/bin/sh\n"+
		`b="${2:-$1}"; [ "$1" = cursor-agent ] && b=agent; "$b" contract || { echo "$1 falls short ($(command -v "$b"))" >&2; exit 70; }; echo contract; "$b" --version`+"\n")
	pm := "echo \"$(basename \"$0\") $*\" >> " + sb.log + "\nsed -i.bak 's/then exit 1; fi/then exit 0; fi/' " + realPath + "\n"
	write(t, filepath.Join(sb.bin, "brew"), "#!/bin/sh\n"+pm)
	write(t, filepath.Join(sb.bin, "npm"), sb.npmRoot()+pm)
	return sb
}

// npmRoot is a shell stub's first lines answering `npm root -g` with the sandbox's lib/node_modules.
func (sb sandbox) npmRoot() string {
	return "#!/bin/sh\nif [ \"$1\" = root ]; then echo " + filepath.Join(sb.root, "lib", "node_modules") + "; exit 0; fi\n"
}

func (sb sandbox) run(t *testing.T, script string) (string, error) {
	t.Helper()
	cmd := exec.Command("bash", "-euo", "pipefail", "-c", script) // as Env.Shell runs it
	cmd.Env = []string{"PATH=" + sb.bin + ":/usr/bin:/bin", "FACTORY_HOME=" + sb.factory}
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func (sb sandbox) calls() string {
	b, _ := os.ReadFile(sb.log)
	return strings.TrimSpace(string(b))
}

func TestPreflightUpdatesAnAgentThatFallsShortThenChecksAgain(t *testing.T) {
	for _, c := range []struct {
		name, agent, binary, real string
		selfUpdate                bool
		want                      string
	}{
		// Cam's case: an old codex with no `update`, from Homebrew, first on the app's PATH.
		{"codex too old to self-update, Homebrew", "codex", "codex", "Cellar/codex/0.138.0/bin/codex", false, "brew uninstall --formula codex\nbrew install --cask codex"},
		{"codex too old to self-update, Homebrew cask", "codex", "codex", "Caskroom/codex/0.138.0/codex", false, "brew upgrade --cask codex"},
		{"codex too old to self-update, npm", "codex", "codex", "lib/node_modules/@openai/codex/bin/codex.js", false, "npm install -g @openai/codex@latest"},
		{"codex that updates itself", "codex", "codex", "opt/codex/codex", true, "self-update"},
		{"claude too old to self-update, npm", "claude", "claude", "lib/node_modules/@anthropic-ai/claude-code/cli.js", false, "npm install -g @anthropic-ai/claude-code@latest"},
		{"cursor-agent updates itself", "cursor-agent", "agent", "share/cursor-agent/agent", true, "self-update"},
		{"agy updates itself", "agy", "agy", "share/agy/agy", true, "self-update"},
	} {
		t.Run(c.name, func(t *testing.T) {
			sb := newSandbox(t, c.binary, c.real, false, c.selfUpdate)
			out, err := sb.run(t, contractPreflight(c.agent))
			if err != nil {
				t.Fatalf("preflight still failing after the update: %v\n%s", err, out)
			}
			if got := sb.calls(); got != c.want {
				t.Fatalf("ran %q, want %q", got, c.want)
			}
			if !strings.Contains(out, "contract") {
				t.Fatalf("second check did not report the contract:\n%s", out)
			}
		})
	}
}

func TestPreflightLeavesACurrentAgentAlone(t *testing.T) {
	sb := newSandbox(t, "codex", "Cellar/codex/0.167.0/bin/codex", true, true)
	if out, err := sb.run(t, contractPreflight("codex")); err != nil {
		t.Fatalf("current agent failed: %v\n%s", err, out)
	}
	if got := sb.calls(); got != "" {
		t.Fatalf("updated a current agent: %q", got)
	}
}

func TestPreflightNamesACopyItCannotUpdate(t *testing.T) {
	sb := newSandbox(t, "codex", "opt/codex/codex", false, false)
	out, err := sb.run(t, contractPreflight("codex"))
	if err == nil {
		t.Fatalf("an agent nothing could update passed:\n%s", out)
	}
	if sb.calls() != "" || !strings.Contains(out, "by hand") || !strings.Contains(out, "falls short") {
		t.Fatalf("calls %q; did not say what to do:\n%s", sb.calls(), out)
	}
}

func TestSeatPreflightChecksEachSeatAgentOnce(t *testing.T) {
	env := testEnv(t, allComponents())
	got := strings.Join(seatPreflight(env, DefaultSeats()), "\n")
	for _, agent := range []string{"claude", "agy", "codex", "cursor-agent"} {
		if n := strings.Count(got, contractPreflight(agent)); n != 1 {
			t.Fatalf("%s checked %d times:\n%s", agent, n, got)
		}
	}
	claudeOnly := Seats{Validator: Seat{Agent: "claude"}, Orchestrator: Seat{Agent: "claude"},
		Coder: Seat{Agent: "claude"}, Tester: Seat{Agent: "claude"}}
	only := strings.Join(seatPreflight(env, claudeOnly), "\n")
	if strings.Count(only, agentContract) != 2 || strings.Contains(only, "codex") {
		t.Fatalf("claude-only seats should check claude alone:\n%s", only)
	}
}

func TestPreflightLeavesAnAbsentAgentToItsInstallLine(t *testing.T) {
	sb := newSandbox(t, "codex", "opt/codex/codex", false, false)
	must(t, os.Remove(filepath.Join(sb.bin, "codex")))
	out, _ := sb.run(t, contractPreflight("codex"))
	if strings.TrimSpace(out) != "" || sb.calls() != "" {
		t.Fatalf("calls %q; an absent codex printed:\n%s", sb.calls(), out)
	}
}

// sandboxEnv is an Env whose commands run with the sandbox first on PATH.
func sandboxEnv(t *testing.T, sb sandbox) *Env {
	t.Helper()
	env := testEnv(t, allComponents())
	env.Opts.CodeDir = sb.root // FactoryDir() is <CodeDir>/factory
	env.run = func(_ context.Context, c *exec.Cmd) error {
		c.Env = append(c.Env, "PATH="+sb.bin+":/usr/bin:/bin")
		return c.Run()
	}
	env.lookPath = func(name string) (string, error) {
		if name == "codex" {
			return filepath.Join(sb.bin, name), nil
		}
		return exec.LookPath(name)
	}
	return env
}

func TestInstallStepUpdatesAnAgentOlderThanThePinnedFactory(t *testing.T) {
	for _, current := range []bool{false, true} {
		sb := newSandbox(t, "codex", "lib/node_modules/@openai/codex/bin/codex.js", current, false)
		codex, _ := agentByID(AgentCodex)
		st, detail, err := (&agentInstallStep{agent: codex}).Check(context.Background(), sandboxEnv(t, sb))
		must(t, err)
		if want := map[bool]Status{false: StatusMissing, true: StatusDone}[current]; st != want {
			t.Fatalf("current=%v: status %v (%s), want %v", current, st, detail, want)
		}
	}
}

func TestInstallStepApplyUpdatesThenChecksAgain(t *testing.T) {
	for _, c := range []struct {
		name, real string
		fixes      bool
		want       string
	}{
		{"npm", "lib/node_modules/@openai/codex/bin/codex.js", true, "npm install -g @openai/codex@latest"},
		{"Homebrew formula", "Cellar/codex/0.138.0/bin/codex", true, "brew uninstall --formula codex\nbrew install --cask codex"},
		{"update leaves it short", "lib/node_modules/@openai/codex/bin/codex.js", false, "npm install -g @openai/codex@latest"},
	} {
		t.Run(c.name, func(t *testing.T) {
			sb := newSandbox(t, "codex", c.real, false, false)
			if !c.fixes {
				write(t, filepath.Join(sb.bin, "npm"), sb.npmRoot()+"echo \"npm $*\" >> "+sb.log+"\n")
			}
			env := sandboxEnv(t, sb)
			codex, _ := agentByID(AgentCodex)
			step := &agentInstallStep{agent: codex}
			st, detail, err := step.Check(context.Background(), env)
			must(t, err)
			if st != StatusMissing {
				t.Fatalf("status %v (%s), want missing", st, detail)
			}
			err = step.Apply(context.Background(), env)
			if got := sb.calls(); got != c.want {
				t.Fatalf("ran %q, want %q", got, c.want)
			}
			if c.fixes && err != nil {
				t.Fatalf("apply: %v", err)
			}
			if !c.fixes && (err == nil || !strings.Contains(err.Error(), "still falls short")) {
				t.Fatalf("an update that left codex short returned %v", err)
			}
		})
	}
}

func TestInstallStepSkipsAnAgentNothingCanUpdate(t *testing.T) {
	sb := newSandbox(t, "codex", "opt/codex/codex", false, false)
	codex, _ := agentByID(AgentCodex)
	step := &agentInstallStep{agent: codex}
	st, detail, err := step.Check(context.Background(), sandboxEnv(t, sb))
	must(t, err)
	if st != StatusSkipped || !strings.Contains(step.NextStep().Text, "first on PATH") ||
		!strings.Contains(detail, "codex falls short ("+filepath.Join(sb.bin, "codex")+")") {
		t.Fatalf("status %v (%s), next %q; want skipped with a next step", st, detail, step.NextStep().Text)
	}
	if sb.calls() != "" {
		t.Fatalf("Check ran %q", sb.calls())
	}
}

// bun and yarn install under a node_modules too; npm cannot update those copies.
func TestANodeModulesCopyNpmDidNotInstallIsLeftToThePerson(t *testing.T) {
	const bun = ".bun/install/global/node_modules/@openai/codex/bin/codex.js"
	sb := newSandbox(t, "codex", bun, false, false)
	out, err := sb.run(t, contractPreflight("codex"))
	if err == nil || sb.calls() != "" || !strings.Contains(out, "by hand") {
		t.Fatalf("err %v, calls %q:\n%s", err, sb.calls(), out)
	}
	codex, _ := agentByID(AgentCodex)
	st, detail, err := (&agentInstallStep{agent: codex}).Check(context.Background(), sandboxEnv(t, sb))
	must(t, err)
	if st != StatusSkipped || sb.calls() != "" {
		t.Fatalf("status %v (%s), calls %q; want skipped", st, detail, sb.calls())
	}
}
