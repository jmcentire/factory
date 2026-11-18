package setup

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"
)

// fakeMachine is a machine with exactly the given tools, each answering its
// commands from a table keyed by "args"; a tool with no answer for a command
// succeeds silently. Nothing from the host is found.
type fakeMachine map[string]map[string]string

// fail is an answer that makes the command exit non-zero.
const fail = "\x00fail"

func (m fakeMachine) install(t *testing.T, env *Env) {
	t.Helper()
	dir := t.TempDir()
	for tool := range m {
		must(t, os.WriteFile(filepath.Join(dir, tool), []byte("#!/bin/sh\n"), 0o755))
	}
	env.searchDirs = []string{}
	env.lookPath = func(name string) (string, error) {
		if _, ok := m[name]; ok {
			return filepath.Join(dir, name), nil
		}
		return "", exec.ErrNotFound
	}
	env.run = func(_ context.Context, c *exec.Cmd) error {
		for args, out := range m[filepath.Base(c.Path)] {
			if strings.Join(c.Args[1:], " ") == args {
				if out == fail {
					return errors.New("exit status 1")
				}
				fmt.Fprint(c.Stdout, out)
			}
		}
		return nil
	}
}

// sudo is the prefix a package install gets as this user.
func sudo() string {
	if os.Geteuid() == 0 {
		return ""
	}
	return "sudo "
}

func python312(extra map[string]string) map[string]string {
	py := map[string]string{
		"-c import sys; print(*sys.version_info[:2])":          "3 12",
		"-c import sys; print('%d.%d' % sys.version_info[:2])": "3.12",
	}
	for k, v := range extra {
		py[k] = v
	}
	return py
}

func TestPreflightNamesEveryMissingBuildToolAtOnce(t *testing.T) {
	env := testEnv(t, kindexOptions())
	fakeMachine{"git": nil, "curl": nil, "bash": nil, "apt-get": nil}.install(t, env)
	next := installGaps(env, (&preflightStep{}).gaps(context.Background(), env, "linux"), "linux")
	for _, want := range []string{"a C compiler", "make", "Python 3.12 or newer"} {
		if !strings.Contains(next.Text, want) {
			t.Errorf("%q not named: %s", want, next.Text)
		}
	}
	if len(next.Cmds) != 1 || !strings.HasSuffix(next.Cmds[0], "apt-get update && "+sudo()+"apt-get install -y build-essential python3 python3-venv") {
		t.Fatalf("one apt line for all of it, got %q", next.Cmds)
	}
}

// A Factory-only Linux install runs make but compiles nothing; only kinbase needs cc.
func TestPreflightForTheFactoryAloneNamesMakeNotACompiler(t *testing.T) {
	o := kindexOptions()
	o.Components = []Component{ComponentFactory}
	env := testEnv(t, o)
	fakeMachine{"git": nil, "curl": nil, "bash": nil, "apt-get": nil}.install(t, env)
	next := installGaps(env, (&preflightStep{}).gaps(context.Background(), env, "linux"), "linux")
	if !strings.Contains(next.Text, "make") || strings.Contains(next.Text, "a C compiler") {
		t.Fatalf("want make named and no C compiler: %s", next.Text)
	}
}

// Debian and Ubuntu ship Python without venv's pip bootstrap.
func TestPreflightFindsAPythonWithoutVenv(t *testing.T) {
	env := testEnv(t, kindexOptions())
	fakeMachine{"git": nil, "curl": nil, "bash": nil, "cc": nil, "make": nil, "apt-get": nil,
		"python3.12": python312(map[string]string{"-c import venv, ensurepip": fail})}.install(t, env)
	next := installGaps(env, (&preflightStep{}).gaps(context.Background(), env, "linux"), "linux")
	if !strings.Contains(next.Text, "venv module") || len(next.Cmds) != 1 || !strings.HasSuffix(next.Cmds[0], "apt-get install -y python3.12-venv") {
		t.Fatalf("expected python3.12-venv, got %q %q", next.Text, next.Cmds)
	}
}

// On a Mac cc and make exist before the Command Line Tools do, as stubs.
func TestPreflightOnAMacAsksForTheCommandLineTools(t *testing.T) {
	env := testEnv(t, kindexOptions())
	fakeMachine{"git": nil, "curl": nil, "bash": nil, "cc": nil, "make": nil, "xcode-select": {"-p": fail}}.install(t, env)
	next := installGaps(env, (&preflightStep{}).gaps(context.Background(), env, "darwin"), "darwin")
	cmds := strings.Join(next.Cmds, "\n")
	if !strings.Contains(next.Text, "Command Line Tools") || !strings.Contains(cmds, "xcode-select --install") || !strings.Contains(cmds, "brew install python@3.13") {
		t.Fatalf("expected the Command Line Tools and Python, got %q %q", next.Text, next.Cmds)
	}
}

func TestPreflightPassesAReadyMachine(t *testing.T) {
	env := testEnv(t, kindexOptions())
	fakeMachine{"git": nil, "curl": nil, "bash": nil, "cc": nil, "make": nil, "python3.12": python312(nil)}.install(t, env)
	if gaps := (&preflightStep{}).gaps(context.Background(), env, "linux"); len(gaps) != 0 {
		t.Fatalf("a ready machine has gaps: %+v", gaps)
	}
}

// A blocked preflight hands the summary its install commands.
func TestABlockedPreflightCarriesItsNextStep(t *testing.T) {
	env := testEnv(t, kindexOptions())
	fakeMachine{"git": nil, "curl": nil, "bash": nil}.install(t, env)
	outcomes, err := (&Runner{Env: env}).Run(context.Background(), &Plan{Options: env.Opts, Steps: []Step{&preflightStep{}}})
	if err == nil || outcomes[0].Status != StatusBlocked || outcomes[0].Next == nil || !strings.Contains(outcomes[0].Next.Text, "Python") {
		t.Fatalf("no next step on the blocked preflight: %v %+v", err, outcomes)
	}
}

func TestTheTesterSeatNamesTheCursorCLI(t *testing.T) {
	env := testEnv(t, allComponents())
	fakeMachine{"claude": nil, "agy": nil, "codex": nil}.install(t, env)
	next := MissingSeatTools(env)
	if len(next) != 1 || !strings.Contains(next[0].Title, "Cursor CLI (cursor-agent)") || !strings.Contains(next[0].Text, "not the Cursor desktop app") {
		t.Fatalf("got %+v", next)
	}
	// An agent that is not Cursor's, like the Grok CLI, is not the tester.
	env = testEnv(t, allComponents())
	fakeMachine{"claude": nil, "agy": nil, "codex": nil, "agent": nil}.install(t, env)
	next = MissingSeatTools(env)
	if len(next) != 1 || !strings.Contains(next[0].Title, "Put the Cursor CLI first") {
		t.Fatalf("a non-Cursor agent passed for the tester: %+v", next)
	}
}

func TestAFactoryRunNeedsTmuxAndGNUTimeout(t *testing.T) {
	env := testEnv(t, allComponents())
	fakeMachine{"brew": nil, "apt-get": nil}.install(t, env)
	next := RuntimeTools(env)
	if next == nil || !strings.Contains(next.Text, "tmux") || !strings.Contains(next.Text, "GNU timeout") || !strings.Contains(strings.Join(next.Cmds, " "), "tmux") {
		t.Fatalf("got %+v", next)
	}
	env = testEnv(t, allComponents())
	fakeMachine{"tmux": nil, "gtimeout": nil}.install(t, env)
	if next := RuntimeTools(env); next != nil {
		t.Fatalf("nothing missing, yet %+v", next)
	}
}

func TestOnlyAgentsNotSignedInAreListed(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude, AgentCodex}
	env := testEnv(t, o)
	fakeMachine{
		"claude": {"auth status": `{"loggedIn": true, "authMethod": "claude.ai"}`},
		"codex":  {"login status": fail},
	}.install(t, env)
	got := flat(*SignIns(context.Background(), env))
	if strings.Contains(got, "claude auth login") {
		t.Errorf("Claude Code is signed in:\n%s", got)
	}
	for _, want := range []string{"codex login", "Antigravity", "cursor-agent login"} {
		if !strings.Contains(got, want) {
			t.Errorf("lacks %q:\n%s", want, got)
		}
	}
	// Antigravity's Google sign-in is a file.
	writeTree(t, filepath.Join(env.Opts.Home, ".gemini"), map[string]string{"oauth_creds.json": "{}"})
	if strings.Contains(flat(*SignIns(context.Background(), env)), "Antigravity") {
		t.Error("Antigravity is signed in")
	}
}

func TestAgySignedInWithoutTheSeatsModelIsAskedToSignInThroughGoogleCloud(t *testing.T) {
	signIns := func(env *Env, ctx context.Context) string {
		writeTree(t, filepath.Join(env.Opts.Home, ".gemini"), map[string]string{"oauth_creds.json": "{}"})
		next := SignIns(ctx, env)
		if next == nil {
			return ""
		}
		return flat(*next)
	}
	o := allComponents()
	o.Agents = []AgentID{AgentAntigravity}
	o.Seats.Tester = Seat{Agent: "codex"} // no cursor-agent sign-in in the way

	// On Wander's Google Cloud project the seat's model is listed.
	env := testEnv(t, o)
	fakeMachine{"agy": {"models": "Fetching available models...\ngemini-4-carbon-high\tGemini 4 Carbon (High)\ngemini-3.8-flash\tGemini 3.8 Flash"}}.install(t, env)
	if got := signIns(env, context.Background()); strings.Contains(got, "Antigravity") {
		t.Errorf("agy can run its model:\n%s", got)
	}

	readStdin := false
	for name, c := range map[string]struct {
		run  func(ctx context.Context, c *exec.Cmd) error
		ctx  func() (context.Context, context.CancelFunc)
		want []string
	}{
		"personal plan": {want: []string{"runs agy on gemini-4-carbon-high, but `agy models` does not list it"}},
		"quota": {
			run: func(_ context.Context, c *exec.Cmd) error {
				fmt.Fprint(c.Stdout, "Individual quota reached. Resets in 6d.")
				return nil
			},
			want: []string{"reports a quota (Individual quota reached. Resets in 6d.)", "personal plan"},
		},
		"failing": {
			run:  func(context.Context, *exec.Cmd) error { return errors.New("exit status 1") },
			want: []string{"failed (exit status 1)"},
		},
		"unreadable listing": {
			run: func(_ context.Context, c *exec.Cmd) error {
				fmt.Fprint(c.Stdout, "Fetching available models...")
				return nil
			},
			want: []string{"printed no models"},
		},
		// A login prompt or no network: the listing is cut off, not waited on.
		"hanging": {
			run: func(ctx context.Context, c *exec.Cmd) error {
				readStdin = c.Stdin != nil
				<-ctx.Done()
				return ctx.Err()
			},
			ctx: func() (context.Context, context.CancelFunc) {
				return context.WithTimeout(context.Background(), 10*time.Millisecond)
			},
			want: []string{"did not answer within a minute"},
		},
	} {
		t.Run(name, func(t *testing.T) {
			env := testEnv(t, o)
			fakeMachine{"agy": {"models": "Fetching available models...\ngemini-3.8-flash\tGemini 3.8 Flash"}}.install(t, env)
			if c.run != nil {
				env.run = c.run
			}
			ctx, cancel := context.Background(), context.CancelFunc(func() {})
			if c.ctx != nil {
				ctx, cancel = c.ctx()
			}
			defer cancel()
			got := signIns(env, ctx)
			for _, want := range append(c.want, "the orchestrator seat", agySignIn("gemini-4-carbon-high"), "--orchestrator <agent>:<model>") {
				if !strings.Contains(got, want) {
					t.Errorf("lacks %q:\n%s", want, got)
				}
			}
			if readStdin {
				t.Error("agy models could read the terminal")
			}
			if env.Opts.Seats.Orchestrator != (Seat{Agent: "agy", Model: "gemini-4-carbon-high"}) {
				t.Errorf("changed the seat to %v", env.Opts.Seats.Orchestrator)
			}
		})
	}
}

func TestOutputReturnsOnceAChildLeftHoldingThePipesOutlivesWaitDelay(t *testing.T) {
	env := testEnv(t, allComponents())
	env.lookPath = exec.LookPath
	env.run = func(_ context.Context, c *exec.Cmd) error { return c.Run() }
	start := time.Now()
	out, err := env.Output(context.Background(), "sh", "-c", "echo hi; sleep 15 & exit 0")
	if err != nil || out != "hi" {
		t.Fatalf("got %q, %v", out, err)
	}
	if d := time.Since(start); d > 10*time.Second {
		t.Fatalf("waited %v on the child", d)
	}
}

// People who picked "Subscriptions", or a personal account, got no Carbon or a
// quota: the sign-in names the account, then Pay As You Go, Google Cloud and
// the project, ending on what to check.
func TestAgySignInGoesAccountThenPayAsYouGoThenGoogleCloudThenProject(t *testing.T) {
	got := agySignIn("gemini-4-carbon-high")
	at := -1
	for _, want := range []string{"wander.com Google account", `"Pay As You Go" (not "Subscriptions")`, `"Continue with Google Cloud"`, "project antigravity-511118", "`agy models` should then list gemini-4-carbon-high"} {
		i := strings.Index(got, want)
		if i <= at {
			t.Fatalf("%q missing or out of order in:\n%s", want, got)
		}
		at = i
	}
	if strings.Contains(agySignIn(), "agy models") {
		t.Errorf("names a model to check with no agy seat: %s", agySignIn())
	}
	if got := agySignIn("a", "b"); !strings.HasSuffix(got, "should then list a and b") {
		t.Errorf("two models: %s", got)
	}
}

func TestTheFirstAgySignInSaysHowBeforeItHappens(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentAntigravity}
	env := testEnv(t, o)
	if got := flat(*SignIns(context.Background(), env)); !strings.Contains(got, agySignIn("gemini-4-carbon-high")) {
		t.Errorf("first sign-in lacks the Wander billing steps:\n%s", got)
	}
	// Without the Factory no seat names a model to look for.
	o.Components = []Component{ComponentAgents}
	env = testEnv(t, o)
	if got := flat(*SignIns(context.Background(), env)); !strings.Contains(got, agySignIn()+".") {
		t.Errorf("first sign-in without the Factory:\n%s", got)
	}
	// Two agy seats on different models: both are named, once each.
	o = allComponents()
	o.Seats.Coder = Seat{Agent: "agy", Model: "gemini-4-flash"}
	o.Seats.Tester = Seat{Agent: "agy", Model: "gemini-4-carbon-high"}
	env = testEnv(t, o)
	if got := flat(*SignIns(context.Background(), env)); !strings.Contains(got, agySignIn("gemini-4-carbon-high", "gemini-4-flash")+".") {
		t.Errorf("first sign-in with two agy models:\n%s", got)
	}
	// The Factory with no seat on agy names no model.
	o.Agents = []AgentID{AgentAntigravity}
	o.Seats = Seats{Validator: Seat{Agent: "claude"}, Orchestrator: Seat{Agent: "codex"}, Coder: Seat{Agent: "codex"}, Tester: Seat{Agent: "claude"}}
	env = testEnv(t, o)
	if got := flat(*SignIns(context.Background(), env)); !strings.Contains(got, agySignIn()+".") {
		t.Errorf("first sign-in with no agy seat:\n%s", got)
	}
}

func TestTheProcedurePreflightChecksAgyListsTheSeatsModel(t *testing.T) {
	env := testEnv(t, allComponents())
	got := strings.Join(seatPreflight(env, env.Opts.Seats), "\n")
	if !strings.Contains(got, "agy models </dev/null | cut -f1 | grep -Fx gemini-4-carbon-high") || !strings.Contains(got, agySignIn("gemini-4-carbon-high")) {
		t.Errorf("no agy model check:\n%s", got)
	}
}

// planIDs are the step IDs a plan for o runs, in order.
func planIDs(t *testing.T, o Options) []string {
	t.Helper()
	p, err := BuildPlan(o)
	must(t, err)
	var ids []string
	for _, s := range p.Steps {
		ids = append(ids, s.ID())
	}
	return ids
}

// recordShell records the scripts env runs through bash.
func recordShell(env *Env) *[]string {
	var scripts []string
	answer := env.run
	env.run = func(ctx context.Context, c *exec.Cmd) error {
		if filepath.Base(c.Path) == "bash" {
			scripts = append(scripts, c.Args[len(c.Args)-1])
		}
		return answer(ctx, c)
	}
	return &scripts
}

func TestTheShippedCommandInstallsTheTesterSeatsCursorCLI(t *testing.T) {
	o := allComponents()
	o.Components = []Component{ComponentFactory, ComponentKindex, ComponentAgents}
	o.Agents = []AgentID{AgentClaude}
	if ids := planIDs(t, o); !slices.Contains(ids, "agent.cursor-agent.install") || !slices.Contains(ids, "factory.tools") {
		t.Fatalf("the plan has no Cursor CLI or runtime tools: %v", ids)
	}
	// A tester on another agent needs no Cursor CLI; nor does a run without
	// the Factory.
	o.Seats.Tester = Seat{Agent: "codex", Model: "gpt-6.1-sol"}
	if ids := planIDs(t, o); slices.Contains(ids, "agent.cursor-agent.install") {
		t.Fatalf("installs the Cursor CLI for no seat: %v", ids)
	}
	o = allComponents()
	o.Components = []Component{ComponentKindex, ComponentAgents}
	o.Agents = []AgentID{AgentClaude}
	if ids := planIDs(t, o); slices.Contains(ids, "agent.cursor-agent.install") || slices.Contains(ids, "factory.tools") {
		t.Fatalf("installs Factory tools without the Factory: %v", ids)
	}

	env := testEnv(t, allComponents())
	fakeMachine{"bash": nil}.install(t, env)
	scripts := recordShell(env)
	if st, _, _ := (cursorInstallStep{}).Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("no agent, yet %v", st)
	}
	must(t, (cursorInstallStep{}).Apply(context.Background(), env))
	if len(*scripts) != 1 || !strings.Contains((*scripts)[0], "https://cursor.com/install") {
		t.Fatalf("ran %q", *scripts)
	}

	// Cursor's installer links agent to a version under cursor-agent.
	env = testEnv(t, allComponents())
	real := filepath.Join(env.Opts.Home, ".local", "share", "cursor-agent", "versions", "1", "cursor-agent")
	writeTree(t, filepath.Dir(real), map[string]string{"cursor-agent": "#!/bin/sh\n"})
	must(t, os.Chmod(real, 0o755))
	must(t, os.MkdirAll(filepath.Dir(env.Opts.LocalBin("agent")), 0o755))
	must(t, os.Symlink(real, env.Opts.LocalBin("agent")))
	env.searchDirs = []string{filepath.Dir(env.Opts.LocalBin("agent"))}
	if st, _, _ := (cursorInstallStep{}).Check(context.Background(), env); st != StatusDone {
		t.Fatalf("Cursor's agent is installed, yet %v", st)
	}
}

// Cursor's installer replaces ~/.local/bin/agent: another tool's agent there,
// like the Grok CLI's, is left to the person.
func TestTheCursorCLINeverReplacesAnotherAgent(t *testing.T) {
	env := testEnv(t, allComponents())
	fakeMachine{"bash": nil, "claude": nil, "agy": nil, "codex": nil}.install(t, env)
	grok := filepath.Join(env.Opts.Home, ".grok", "bin", "agent")
	writeTree(t, filepath.Dir(grok), map[string]string{"agent": "#!/bin/sh\n"})
	must(t, os.Chmod(grok, 0o755))
	must(t, os.MkdirAll(filepath.Dir(env.Opts.LocalBin("agent")), 0o755))
	must(t, os.Symlink(grok, env.Opts.LocalBin("agent")))
	env.searchDirs = []string{filepath.Dir(env.Opts.LocalBin("agent"))}

	st, detail, _ := (cursorInstallStep{}).Check(context.Background(), env)
	if st != StatusSkipped || !strings.Contains(detail, "another tool named agent") {
		t.Fatalf("got %v %q", st, detail)
	}
	next := MissingSeatTools(env)
	if len(next) != 1 || !strings.Contains(next[0].Title, "Put the Cursor CLI first") || !strings.Contains(next[0].Text, "Cursor's installer replaces "+env.Opts.LocalBin("agent")) {
		t.Fatalf("no next step for it: %+v", next)
	}
}

func TestTmuxAndGNUTimeoutAreInstalled(t *testing.T) {
	env := testEnv(t, allComponents())
	env.Interactive = true
	fakeMachine{"bash": nil, "brew": nil, "apt-get": nil}.install(t, env)
	scripts := recordShell(env)
	st, cmd, _ := (runtimeToolsStep{}).Check(context.Background(), env)
	if st != StatusMissing || !strings.Contains(cmd, "tmux coreutils") {
		t.Fatalf("got %v %q", st, cmd)
	}
	must(t, (runtimeToolsStep{}).Apply(context.Background(), env))
	if len(*scripts) != 1 || (*scripts)[0] != cmd {
		t.Fatalf("ran %q, not %q", *scripts, cmd)
	}

	env = testEnv(t, allComponents())
	fakeMachine{"tmux": nil, "gtimeout": nil}.install(t, env)
	if st, _, _ := (runtimeToolsStep{}).Check(context.Background(), env); st != StatusDone {
		t.Fatalf("nothing missing, yet %v", st)
	}

	// With no way to install them, they are a next step and the run goes on.
	env = testEnv(t, allComponents())
	tools := fakeMachine{"bash": nil}
	if runtime.GOOS != "darwin" {
		// The package manager would ask for a password no one is there to type.
		tools["apt-get"] = nil
		if os.Geteuid() == 0 {
			t.Skip("root types no password")
		}
	}
	tools.install(t, env)
	if st, _, _ := (runtimeToolsStep{}).Check(context.Background(), env); st != StatusSkipped {
		t.Fatalf("got %v", st)
	}
	if next := RuntimeTools(env); next == nil || !strings.Contains(next.Text, "tmux") {
		t.Fatalf("no next step: %+v", next)
	}
}

// Codex installs with npm; without Node.js the install says so and goes on.
func TestAnAgentNeedingNodeWaitsForIt(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	fakeMachine{"bash": nil}.install(t, env)
	a, _ := agentByID(AgentCodex)
	outcomes, err := (&Runner{Env: env}).Run(context.Background(), &Plan{Options: env.Opts, Steps: []Step{&agentInstallStep{agent: a}}})
	if err != nil || outcomes[0].Status != StatusSkipped || outcomes[0].Next == nil || !strings.Contains(outcomes[0].Next.Text, "Node.js") || outcomes[0].Next.Cmds[0] != a.Install {
		t.Fatalf("got %v %+v", err, outcomes)
	}
	// The install step's next step is the only one for the coder seat.
	for _, n := range MissingSeatTools(env) {
		if strings.Contains(n.Title, "Codex") {
			t.Fatalf("Codex twice: %+v", n)
		}
	}
	fakeMachine{"bash": nil, "npm": nil}.install(t, env)
	if st, _, _ := (&agentInstallStep{agent: a}).Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("npm is there, yet %v", st)
	}
}
