package setup

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// fakeTools puts executables named for each tool in a temporary directory,
// each printing its version line when asked, and records every command run.
// `python -m venv DIR` creates DIR/bin/python, as the real one would.
func fakeTools(t *testing.T, env *Env, versions map[string]string) *[][]string {
	t.Helper()
	dir := t.TempDir()
	for name := range versions {
		must(t, os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/sh\n"), 0o755))
	}
	env.lookPath = func(name string) (string, error) {
		if _, ok := versions[name]; ok {
			return filepath.Join(dir, name), nil
		}
		return "", exec.ErrNotFound
	}
	var ran [][]string
	env.run = func(_ context.Context, c *exec.Cmd) error {
		ran = append(ran, c.Args)
		if filepath.Dir(c.Path) == dir {
			fmt.Fprintln(c.Stdout, versions[filepath.Base(c.Path)])
		}
		if i := slices.Index(c.Args, "venv"); i > 0 && c.Args[i-1] == "-m" {
			venv := c.Args[len(c.Args)-1]
			must(t, os.MkdirAll(filepath.Join(venv, "bin"), 0o755))
			must(t, os.WriteFile(filepath.Join(venv, "bin", "python"), []byte("#!/bin/sh\n"), 0o755))
			must(t, os.WriteFile(filepath.Join(venv, "pyvenv.cfg"), []byte("home = /usr/bin\n"), 0o644))
		}
		return nil
	}
	return &ran
}

func kindexOptions() Options {
	o := DefaultOptions()
	o.Components = []Component{ComponentFactory, ComponentKindex, ComponentAgents}
	o.Agents = []AgentID{AgentClaude}
	return o
}

func TestTheLeanPlanInstallsKindexAndKinbaseWithoutNix(t *testing.T) {
	env := testEnv(t, kindexOptions())
	p, err := BuildPlan(env.Opts)
	must(t, err)
	var ids []string
	for _, s := range p.Steps {
		ids = append(ids, s.ID())
	}
	kinbase, kindex := slices.Index(ids, "kinbase.install"), slices.Index(ids, "kindex.install")
	agents := slices.Index(ids, "agent.claude-code.configure")
	if kinbase < 0 || kindex < 0 || agents < kindex {
		t.Fatalf("kinbase and kindex must install before the agents are wired: %v", ids)
	}
	if ids[len(ids)-1] != "kinbase.connect" {
		t.Fatalf("connecting needs a grant most people lack, so it comes last: %v", ids)
	}
	for _, id := range ids {
		if strings.HasPrefix(id, "nix.") || strings.HasPrefix(id, "devshell.") {
			t.Fatalf("the lean plan pulls in %s: %v", id, ids)
		}
	}
}

func TestKinbaseBuildsAtTheDevshellPinWithCargo(t *testing.T) {
	env := testEnv(t, kindexOptions())
	o := env.Opts
	ran := fakeTools(t, env, map[string]string{"cargo": "cargo 1.99.0 (abc 2026-10-01)"})
	if st, detail, _ := (kinbaseInstallStep{}).Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("expected missing, got %v %q", st, detail)
	}
	*ran = nil
	must(t, kinbaseInstallStep{}.Apply(context.Background(), env))
	want := []string{"install", "--locked", "--force", "--git", kinbaseRepoURL, "--rev", PinnedKinbaseRev,
		"--root", o.KinbaseRoot(), "kinbase"}
	if got := (*ran)[len(*ran)-1]; !slices.Equal(got[1:], want) {
		t.Fatalf("cargo ran %q, want %q", got[1:], want)
	}
	if !FileContains(o.LocalBin("kinbase"), kinbaseMarker) || !FileContains(o.LocalBin("kinbase"), shq(o.KinbaseRaw())) {
		t.Errorf("%s is not the wrapper around %s", o.LocalBin("kinbase"), o.KinbaseRaw())
	}
	// What cargo install records, and a binary that runs, is done.
	writeTree(t, o.KinbaseRoot(), map[string]string{
		".crates.toml": fmt.Sprintf("[v1]\n\"kinbase 0.1.0 (git+%s?rev=%s#%s)\" = [\"kinbase\"]\n", kinbaseRepoURL, PinnedKinbaseRev, PinnedKinbaseRev),
		"bin/kinbase":  "#!/bin/sh\n",
	})
	must(t, os.Chmod(o.KinbaseRaw(), 0o755))
	if st, detail, _ := (kinbaseInstallStep{}).Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done at the pin, got %v %q", st, detail)
	}
}

// v0.1.9 installed kinbase with cargo straight into ~/.local/bin, where the
// wrapper now goes: cargo uninstalls it first, so its record goes with it.
func TestAV019KinbaseMakesWayForTheWrapper(t *testing.T) {
	env := testEnv(t, kindexOptions())
	o := env.Opts
	ran := fakeTools(t, env, map[string]string{"cargo": "cargo 1.99.0 (abc 2026-10-01)"})
	writeTree(t, filepath.Join(o.Home, ".local"), map[string]string{
		".crates.toml": fmt.Sprintf("[v1]\n\"kinbase 0.1.0 (git+%s?rev=%s#%s)\" = [\"kinbase\"]\n", kinbaseRepoURL, PinnedKinbaseRev, PinnedKinbaseRev),
		"bin/kinbase":  "\x7fELF the old binary",
	})
	if st, detail, _ := (kinbaseInstallStep{}).Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("expected missing, got %v %q", st, detail)
	}
	*ran = nil
	must(t, kinbaseInstallStep{}.Apply(context.Background(), env))
	if len(*ran) < 2 || !slices.Equal((*ran)[len(*ran)-2][1:], []string{"uninstall", "--root", filepath.Join(o.Home, ".local"), "kinbase"}) {
		t.Fatalf("the v0.1.9 kinbase was not uninstalled first: %q", *ran)
	}
}

func TestKinbaseLeavesSomeoneElsesKinbaseAlone(t *testing.T) {
	env := testEnv(t, kindexOptions())
	fakeTools(t, env, map[string]string{"cargo": "cargo 1.99.0 (abc 2026-10-01)"})
	writeTree(t, filepath.Dir(env.Opts.LocalBin("kinbase")), map[string]string{"kinbase": "#!/bin/sh\n# mine\n"})
	st, detail, _ := (kinbaseInstallStep{}).Check(context.Background(), env)
	if st != StatusBlocked || !strings.Contains(detail, env.Opts.LocalBin("kinbase")) {
		t.Fatalf("expected blocked naming the foreign kinbase, got %v %q", st, detail)
	}
}

// A virtualenv left without its pyvenv.cfg runs as the system's Python, which
// refuses pip ("externally-managed-environment"): it is rebuilt, not used.
func TestABrokenKindexVirtualenvIsRebuilt(t *testing.T) {
	env := testEnv(t, kindexOptions())
	o := env.Opts
	ran := fakeTools(t, env, map[string]string{"python3.13": "3 13"})
	writeTree(t, filepath.Join(o.KindexVenv(), "bin"), map[string]string{"python": "#!/bin/sh\n"})
	must(t, os.Chmod(filepath.Join(o.KindexVenv(), "bin", "python"), 0o755))
	must(t, kindexInstallStep{}.Apply(context.Background(), env))
	if !slices.ContainsFunc(*ran, func(a []string) bool { return slices.Equal(a[1:], []string{"-m", "venv", "--clear", o.KindexVenv()}) }) {
		t.Fatalf("the broken virtualenv was not rebuilt: %q", *ran)
	}
	// Done is done only with a virtualenv that is one.
	must(t, os.Remove(filepath.Join(o.KindexVenv(), "pyvenv.cfg")))
	if st, _, _ := (kindexInstallStep{}).Check(context.Background(), env); st == StatusDone {
		t.Fatal("a broken virtualenv reads as done")
	}
}

// Kinbase is reached through its gateway: kinbase is the binary itself, with
// every argument and the person's own config, and no tunnel opened first.
func TestTheKinbaseCommandRunsTheBinaryItself(t *testing.T) {
	env := testEnv(t, kindexOptions())
	o := env.Opts
	writeTree(t, filepath.Dir(o.KinbaseRaw()), map[string]string{"kinbase": "#!/bin/sh\necho \"raw XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-} $*\"\n"})
	writeTree(t, filepath.Dir(o.LocalBin("kinbase")), map[string]string{"kinbase": kinbaseWrapper(o)})
	must(t, os.Chmod(o.KinbaseRaw(), 0o755))
	must(t, os.Chmod(o.LocalBin("kinbase"), 0o755))
	c := exec.Command(o.LocalBin("kinbase"), "--json", "explain", "a key")
	c.Env = []string{"HOME=" + o.Home, "PATH=/usr/bin:/bin"}
	out, err := c.CombinedOutput()
	if err != nil || strings.TrimSpace(string(out)) != "raw XDG_CONFIG_HOME= --json explain a key" {
		t.Fatalf("kinbase ran %q (%v)", out, err)
	}
}

// The staging tunnel's kinbase-raw, from an earlier install, goes.
func TestTheStagingKinbaseRawIsRetired(t *testing.T) {
	env := testEnv(t, kindexOptions())
	o := env.Opts
	fakeTools(t, env, map[string]string{"cargo": "cargo 1.99.0 (abc 2026-10-01)"})
	writeTree(t, o.KinbaseRoot(), map[string]string{
		".crates.toml": fmt.Sprintf("[v1]\n\"kinbase 0.1.1 (git+%s?rev=%s#%s)\" = [\"kinbase\"]\n", kinbaseRepoURL, PinnedKinbaseRev, PinnedKinbaseRev),
		"bin/kinbase":  "#!/bin/sh\n",
	})
	must(t, os.Chmod(o.KinbaseRaw(), 0o755))
	writeTree(t, filepath.Dir(o.LocalBin("kinbase")), map[string]string{
		"kinbase":     "#!/usr/bin/env bash\n" + kinbaseMarker + " (kinbase 976d2b3)\n# the staging tunnel\n",
		"kinbase-raw": "#!/bin/sh\n" + kinbaseMarker + ": the kinbase binary itself\n",
	})
	if st, _, _ := (kinbaseInstallStep{}).Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("the staging wrapper reads as done: %v", st)
	}
	must(t, kinbaseInstallStep{}.Apply(context.Background(), env))
	if Exists(o.LocalBin("kinbase-raw")) || FileContains(o.LocalBin("kinbase"), "staging tunnel") {
		t.Fatal("the staging tunnel's commands are still in ~/.local/bin")
	}
	if st, detail, _ := (kinbaseInstallStep{}).Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done, got %v %q", st, detail)
	}
}

// An older kindex in ~/.local/bin (pipx, uv, pip) makes way for this one:
// moved off PATH, not deleted.
func TestAnOlderKindexMakesWayForTheNewOne(t *testing.T) {
	env := testEnv(t, kindexOptions())
	o := env.Opts
	fakeTools(t, env, map[string]string{"python3.13": "3 13"})
	writeTree(t, filepath.Dir(o.LocalBin("kin")), map[string]string{"kin": "#!/bin/sh\n# pipx kindex 0.40\n", "kin-mcp": "#!/bin/sh\n# pipx\n"})
	st, detail, _ := (kindexInstallStep{}).Check(context.Background(), env)
	if st != StatusMissing || !strings.Contains(detail, "older kindex") {
		t.Fatalf("expected the older kindex to be replaced, got %v %q", st, detail)
	}
	must(t, kindexInstallStep{}.Apply(context.Background(), env))
	if !FileContains(o.LocalBin("kin"), kindexMarker) {
		t.Fatal("~/.local/bin/kin is still the older kindex")
	}
	if b, err := os.ReadFile(filepath.Join(o.replacedDir(), "kin")); err != nil || !strings.Contains(string(b), "pipx kindex 0.40") {
		t.Fatalf("the older kin was not kept: %q %v", b, err)
	}
	if st, detail, _ := (kindexInstallStep{}).Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done, got %v %q", st, detail)
	}
}

func TestAgentsGetTheLeanKindexServerWithoutTheDevshell(t *testing.T) {
	env := testEnv(t, kindexOptions())
	a, _ := agentByID(AgentClaude)
	must(t, (&agentConfigureStep{agent: a}).Apply(context.Background(), env))
	_, _, mcp := claudePaths(env.Opts)
	m, err := readJSON(mcp)
	must(t, err)
	servers := subMap(m, "mcpServers")
	if got := subMap(servers, "kindex")["command"]; got != env.Opts.LocalBin("kin-mcp") {
		t.Fatalf("kindex server runs %v, want %s", got, env.Opts.LocalBin("kin-mcp"))
	}
	if len(servers) != 1 {
		t.Fatalf("only kindex comes without the devshell, got %v", servers)
	}
	// Beside a devshell, whose kindex can be older, the devshell's other
	// servers come in and kindex stays the new one.
	env.Opts.Components = append(env.Opts.Components, ComponentDevshell)
	byName := map[string]string{}
	for _, s := range mcpServers(env.Opts) {
		byName[s.Name] = s.Command
	}
	if byName["kindex"] != env.Opts.LocalBin("kin-mcp") || byName["constrain"] != env.Opts.DevshellBin("constrain-mcp") {
		t.Fatalf("with the devshell: %v", byName)
	}
}

// An agent already wired to an older kindex is not done: every JSON-configured
// agent's kindex entry is moved to the new one.
func TestAgentsWiredToAnOlderKindexAreRewired(t *testing.T) {
	for _, id := range []AgentID{AgentClaude, AgentOpenCode, AgentPi, AgentAntigravity} {
		o := kindexOptions()
		o.Agents = []AgentID{id}
		env := testEnv(t, o)
		a, _ := agentByID(id)
		step := &agentConfigureStep{agent: a}
		must(t, step.Apply(context.Background(), env))
		if st, detail, _ := step.Check(context.Background(), env); st != StatusDone {
			t.Fatalf("%s: not done after configuring: %v %q", id, st, detail)
		}
		// Point it at an older kindex, as an earlier install would have.
		for _, f := range ConfigFiles(env.Opts, id) {
			if b, err := os.ReadFile(f); err == nil && strings.Contains(string(b), env.Opts.LocalBin("kin-mcp")) {
				must(t, os.WriteFile(f, []byte(strings.ReplaceAll(string(b), env.Opts.LocalBin("kin-mcp"), "/opt/old/kin-mcp")), 0o600))
			}
		}
		if st, _, _ := step.Check(context.Background(), env); st == StatusDone {
			t.Fatalf("%s: an older kindex reads as configured", id)
		}
		must(t, step.Apply(context.Background(), env))
		if st, detail, _ := step.Check(context.Background(), env); st != StatusDone {
			t.Fatalf("%s: not rewired: %v %q", id, st, detail)
		}
	}
}
