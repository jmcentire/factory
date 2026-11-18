package main

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"golang.org/x/sys/unix"

	"github.com/wandercom/chiron/internal/setup"
)

// What a machine without staging access sees: the longest steps there are.
var sampleSteps = []setup.NextStep{
	pathStep([]string{"kin", "kinbase", "factory-setup"}),
	{Title: "Restart your coding agents", Text: "Claude Code, OpenAI Codex CLI, OpenCode, prime-agent (Prime Intellect), pi coding agent and Google Antigravity CLI (agy) load the new commands, MCP servers and memory when they start."},
	{
		Title: "Connect kinbase to the staging Company store",
		Text:  "kindex's kinbase tools read it. kubectl has no context for the staging cluster yet.",
		Parts: []setup.NextStep{
			{Title: "Get Kinbase staging access, if you don't have it yet", Text: "Ask Alex or Shane in Slack. They add you to the kinbase-reader RoleBinding in the staging cluster."},
			{Title: "Sign in to Google Cloud with your Wander account", Cmds: []string{"gcloud auth login"}},
			{Title: "Add the staging cluster to kubectl", Cmds: []string{"gcloud container clusters get-credentials staging --region us-central1 --project wander-staging-464018"}},
			{Title: "Connect", Text: "Reads the shared reader token from the cluster, writes your staging profile to ~/.config/kinbase-staging and checks the connection with a fresh signed snapshot. Nothing else is reinstalled.", Cmds: []string{"~/.local/bin/factory-setup --components kindex"}},
		},
	},
}

// The terminal wraps a line that reaches its edge back to column 0, in the
// middle of a word; nothing the summary prints may reach it.
func TestNextStepsFitTheTerminal(t *testing.T) {
	for width := 50; width <= 120; width++ {
		out := renderNextSteps(sampleSteps, width)
		for _, line := range strings.Split(out, "\n") {
			if lipgloss.Width(line) > width {
				t.Fatalf("width %d: a %d-column line:\n%s", width, lipgloss.Width(line), out)
			}
		}
		if strings.Contains(out, "prime-\n") {
			t.Fatalf("width %d: a word split at its hyphen:\n%s", width, out)
		}
		// A command broken across lines reads as one again without its
		// continuations.
		joined := regexp.MustCompile(` \\\n *`).ReplaceAllString(out, " ")
		for _, want := range []string{"Alex or Shane in Slack", "$ gcloud auth login", "$ ~/.local/bin/factory-setup --components kindex"} {
			if !strings.Contains(joined, want) {
				t.Fatalf("width %d lacks %q:\n%s", width, want, out)
			}
		}
	}
}

func TestALongCommandStillPastesAsOne(t *testing.T) {
	for _, cmd := range []string{
		"gcloud container clusters get-credentials staging --region us-central1 --project wander-staging-464018",
		`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`,
	} {
		for limit := 20; limit <= len(cmd)+5; limit++ {
			lines := commandLines(cmd, limit)
			if joined := strings.ReplaceAll(strings.Join(lines, "\n"), " \\\n", " "); joined != cmd {
				t.Fatalf("limit %d: %q does not paste back as %q", limit, lines, cmd)
			}
			for _, l := range lines {
				// Only a single word longer than the limit may overflow it.
				if len(l) > limit && strings.Contains(strings.TrimSuffix(l, " \\"), " ") && !strings.Contains(l, "'") {
					t.Fatalf("limit %d: %q is %d long", limit, l, len(l))
				}
			}
		}
	}
	// A quoted string is one word.
	if lines := commandLines(`echo 'a b c d e f' >> x`, 10); !strings.Contains(strings.Join(lines, "\n"), "'a b c d e f'") {
		t.Fatalf("split inside quotes: %q", lines)
	}
}

// Typing kin should run the new kindex; an older one earlier on PATH is named,
// with the command for the tool that installed it.
func TestAnOlderKinEarlierOnPathIsNamed(t *testing.T) {
	home, old := t.TempDir(), t.TempDir()
	o := setup.Options{Home: home}
	venv := filepath.Join(old, "pipx", "venvs", "kindex", "bin")
	must(t, os.MkdirAll(venv, 0o755))
	must(t, os.WriteFile(filepath.Join(venv, "kin"), []byte("#!/bin/sh\n"), 0o755))
	must(t, os.MkdirAll(filepath.Join(old, "bin"), 0o755))
	must(t, os.Symlink(filepath.Join(venv, "kin"), filepath.Join(old, "bin", "kin")))
	must(t, os.MkdirAll(filepath.Dir(o.LocalBin("kin")), 0o755))
	must(t, os.WriteFile(o.LocalBin("kin"), []byte("#!/bin/sh\n"), 0o755))

	t.Setenv("PATH", filepath.Join(old, "bin")+":"+filepath.Dir(o.LocalBin("kin")))
	step, ok := shadowingKindex(o)
	if !ok || !strings.Contains(step.Title, filepath.Join(old, "bin", "kin")) || len(step.Cmds) != 1 || step.Cmds[0] != "pipx uninstall kindex" {
		t.Fatalf("the older kin is not named with its uninstall: %+v %v", step, ok)
	}
	t.Setenv("PATH", filepath.Dir(o.LocalBin("kin"))+":"+filepath.Join(old, "bin"))
	if _, ok := shadowingKindex(o); ok {
		t.Fatal("the new kin comes first, yet a step was made")
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

// The command shared for a fresh machine names the agents component and no
// agent: it sets up every agent a default seat runs, beside those already
// installed, and Claude Code at least.
func TestTheSharedCommandSetsUpEverySeatsAgent(t *testing.T) {
	o := setup.DefaultOptions()
	o.Components = []setup.Component{setup.ComponentFactory, setup.ComponentKindex, setup.ComponentAgents}
	seats := []setup.AgentID{setup.AgentClaude, setup.AgentCodex, setup.AgentAntigravity}
	if got := defaultAgents(nil, o); !slices.Equal(got, seats) {
		t.Fatalf("a machine with no agents gets %v", got)
	}
	// As on the screenshot: Claude Code and Codex there, Antigravity not.
	if got := defaultAgents([]setup.AgentID{setup.AgentClaude, setup.AgentCodex}, o); !slices.Equal(got, seats) {
		t.Fatalf("Antigravity, the orchestrator, is left out: %v", got)
	}
	if got := defaultAgents([]setup.AgentID{setup.AgentPi}, o); !slices.Equal(got, []setup.AgentID{setup.AgentClaude, setup.AgentCodex, setup.AgentPi, setup.AgentAntigravity}) {
		t.Fatalf("detected agents were dropped: %v", got)
	}
	// Without the Factory there are no seats to fill.
	o.Components = []setup.Component{setup.ComponentKindex, setup.ComponentAgents}
	if got := defaultAgents(nil, o); !slices.Equal(got, []setup.AgentID{setup.AgentClaude}) {
		t.Fatalf("a machine with no agents gets %v", got)
	}
	if got := defaultAgents([]setup.AgentID{setup.AgentCodex}, o); !slices.Equal(got, []setup.AgentID{setup.AgentCodex}) {
		t.Fatalf("detected agents were replaced: %v", got)
	}
}

// A run stopped by a step that knows what it needs ends with that step's
// commands, then the rerun.
func TestAStoppedRunEndsWithWhatToInstall(t *testing.T) {
	need := &setup.NextStep{Title: "Install the tools this machine is missing", Cmds: []string{"xcode-select --install"}}
	steps := nextSteps(&setup.Env{}, []setup.Outcome{{Status: setup.StatusBlocked, Next: need}}, errors.New("preflight: blocked"))
	if len(steps) != 2 || steps[0].Cmds[0] != "xcode-select --install" || !strings.Contains(steps[1].Title, "run the same command again") {
		t.Fatalf("got %+v", steps)
	}
}

// kinbase refuses to start with descriptors it did not expect, and an agent
// shell can hand factory-setup some. A descriptor opened without
// close-on-exec, as an inherited one is, must not reach a child.
func TestInheritedFilesDoNotReachChildren(t *testing.T) {
	// A high number nothing else holds: ls lists the descriptor it reads
	// /dev/fd through, which takes the lowest free one.
	opened, err := syscall.Open(os.DevNull, syscall.O_RDONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(opened)
	const fd = 77
	if err := unix.Dup2(opened, fd); err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(fd)
	sees := func() bool {
		out, err := exec.Command("/bin/sh", "-c", "ls /dev/fd").Output()
		if err != nil {
			t.Fatal(err)
		}
		return slices.Contains(strings.Fields(string(out)), strconv.Itoa(fd))
	}
	if !sees() {
		t.Skip("this platform closes inherited descriptors already")
	}
	closeInheritedFiles()
	if sees() {
		t.Fatalf("descriptor %d reached the child", fd)
	}
}

// namedStep is a step known only by its ID.
type namedStep string

func (s namedStep) ID() string    { return string(s) }
func (s namedStep) Title() string { return string(s) }
func (namedStep) Check(context.Context, *setup.Env) (setup.Status, string, error) {
	return setup.StatusDone, "", nil
}
func (namedStep) Apply(context.Context, *setup.Env) error { return nil }

// The Factory starts the Cursor CLI as agent, from ~/.local/bin where its
// installer links it.
func TestThePathStepNamesTheCursorCLI(t *testing.T) {
	o := setup.Options{Home: t.TempDir(), Components: []setup.Component{setup.ComponentFactory, setup.ComponentAgents}}
	cursor := namedStep("agent.cursor-agent.install")
	if names := localBinTools(o, []setup.Outcome{{Step: cursor, Status: setup.StatusDone, Applied: true}}); !slices.Equal(names, []string{"Cursor's agent", "factory-setup"}) {
		t.Fatalf("got %q", names)
	}
	if names := localBinTools(o, []setup.Outcome{{Step: cursor, Status: setup.StatusDone, Detail: o.LocalBin("agent")}}); !slices.Equal(names, []string{"Cursor's agent", "factory-setup"}) {
		t.Fatalf("got %q", names)
	}
	// Installed elsewhere, or not at all, it needs nothing on PATH.
	if names := localBinTools(o, []setup.Outcome{{Step: cursor, Status: setup.StatusDone, Detail: "/opt/homebrew/bin/agent"}}); names != nil {
		t.Fatalf("got %q", names)
	}
	if names := localBinTools(o, []setup.Outcome{{Step: cursor, Status: setup.StatusSkipped}}); names != nil {
		t.Fatalf("got %q", names)
	}
	o.Components = append(o.Components, setup.ComponentKindex)
	if names := localBinTools(o, nil); !slices.Equal(names, []string{"kin", "kinbase", "factory-setup"}) {
		t.Fatalf("got %q", names)
	}
	if step := pathStep([]string{"Cursor's agent", "factory-setup"}); !strings.HasPrefix(step.Text, "Cursor's agent and factory-setup live there.") {
		t.Fatalf("got %q", step.Text)
	}
}
