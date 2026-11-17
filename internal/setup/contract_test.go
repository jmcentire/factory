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
	"time"
)

// writeTree creates files under root from relative path -> content.
func writeTree(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for rel, content := range files {
		p := filepath.Join(root, rel)
		must(t, os.MkdirAll(filepath.Dir(p), 0o755))
		must(t, os.WriteFile(p, []byte(content), 0o644))
	}
}

func goodFactoryTree() map[string]string {
	files := map[string]string{
		"Makefile": "VENV ?= .venv\nVENV_STAMP := $(VENV)/.factory-deps\n\nvenv: ## x\n\tpython -m venv $(VENV)\n\n" +
			"dev: venv ## y\n\t$(VENV)/bin/pip install -e .\n",
		"pyproject.toml":               "[project]\nname='factory-core'\n",
		"prompts/README.md":            "| `$FACTORY_HOME` | This repository's checkout | The root of this checkout |\n",
		"harness/factory.sh":           "#!/bin/sh\n--repo run_record.py\n",
		"harness/run_record.py":        "if agent == \"kickoff\":\n    brief = root / role / \"BRIEF.md\"\n",
		"harness/model_settings.py":    "env[f\"FACTORY_{role.upper()}_{field.upper()}\"]\n",
		"harness/endgame.sh":           "#!/bin/sh\nexec python validate_candidate.py\n",
		"harness/status.sh":            "#!/bin/sh\n",
		"harness/tmux_lane.sh":         "#!/bin/sh\n[--agent codex|cursor-agent] [--model <model>]\n",
		"harness/tmux_lane_message.sh": "#!/bin/sh\n",
		"harness/role_inputs.py":       "\n",
		"docs/SOFTWARE-FACTORY.md":     "# Software Factory\n",
	}
	for _, p := range FactoryPrompts {
		if p.File != "" {
			files[p.File] = "Read `$FACTORY_HOME/docs/SOFTWARE-FACTORY.md` first.\n"
		}
	}
	return files
}

func goodDevshellTree() map[string]string {
	return map[string]string{
		"flake.nix":        "default = pkgs.buildEnv { }; signet-eval kindex constrain pact-agents transmogrifier",
		"flake.lock":       "{}",
		"nix-bootstrap.sh": "#!/bin/bash\nnix profile add 'nixpkgs#direnv' 'nixpkgs#nix-direnv'\necho '# tooling-devshell: source nix-direnv when its helpers are not already loaded'\n",
		".envrc":           "use flake\n",
	}
}

func TestFactoryContractPassesOnExpectedLayout(t *testing.T) {
	env := testEnv(t, allComponents())
	writeTree(t, env.Opts.FactoryDir(), goodFactoryTree())
	problems, warnings := factoryContract().verify(env.Opts)
	if len(problems) != 0 || len(warnings) != 0 {
		t.Fatalf("problems=%v warnings=%v", problems, warnings)
	}
	st, detail, err := (&contractStep{c: factoryContract()}).Check(context.Background(), env)
	must(t, err)
	if st != StatusDone {
		t.Fatalf("got %v %q", st, detail)
	}
}

func TestFactoryContractBlocksWhenPromptRemoved(t *testing.T) {
	env := testEnv(t, allComponents())
	tree := goodFactoryTree()
	delete(tree, "prompts/engineer.md")
	tree["Makefile"] = strings.Replace(tree["Makefile"], "dev: venv", "install: venv", 1)
	writeTree(t, env.Opts.FactoryDir(), tree)

	st, detail, err := (&contractStep{c: factoryContract()}).Check(context.Background(), env)
	must(t, err)
	if st != StatusBlocked {
		t.Fatalf("expected blocked, got %v", st)
	}
	for _, want := range []string{"missing prompts/engineer.md", "Makefile lacks target dev", "internal/setup/factory.go", "--factory-ref"} {
		if !strings.Contains(detail, want) {
			t.Errorf("block message lacks %q:\n%s", want, detail)
		}
	}
}

func TestFactoryContractWarnsWhenLocationNameGone(t *testing.T) {
	env := testEnv(t, allComponents())
	tree := goodFactoryTree()
	tree["prompts/README.md"] = "Prompts cite the checkout as $FACTORY_ROOT now.\n"
	writeTree(t, env.Opts.FactoryDir(), tree)
	problems, warnings := factoryContract().verify(env.Opts)
	if len(problems) != 0 {
		t.Fatalf("unexpected problems %v", problems)
	}
	if len(warnings) != 1 || !strings.Contains(warnings[0], "$FACTORY_HOME") {
		t.Fatalf("expected one warning about $FACTORY_HOME, got %v", warnings)
	}
	st, detail, _ := (&contractStep{c: factoryContract()}).Check(context.Background(), env)
	if st != StatusDone || !strings.Contains(detail, "warnings") {
		t.Fatalf("warnings must not block: %v %q", st, detail)
	}
}

func TestDevshellContractBlocksOnFlakeRefactor(t *testing.T) {
	env := testEnv(t, allComponents())
	tree := goodDevshellTree()
	tree["flake.nix"] = "default = pkgs.buildEnv { }; kindex constrain pact-agents transmogrifier" // signet-eval dropped
	delete(tree, "nix-bootstrap.sh")
	writeTree(t, env.Opts.DevshellDir(), tree)
	st, detail, _ := (&contractStep{c: devshellContract()}).Check(context.Background(), env)
	if st != StatusBlocked {
		t.Fatalf("expected blocked, got %v", st)
	}
	for _, want := range []string{"missing nix-bootstrap.sh", `flake.nix no longer contains "signet-eval"`, "--tooling-devshell-ref"} {
		if !strings.Contains(detail, want) {
			t.Errorf("block message lacks %q:\n%s", want, detail)
		}
	}
}

func TestContractsMatchRealCheckouts(t *testing.T) {
	// Verify the contracts against real checkouts at the pinned revisions, so
	// a pin bump that breaks an assumption fails here first.
	// DEVSHELL_CHECKOUT and FACTORY_CHECKOUT name checkouts in directories
	// called tooling-devshell and factory; the devshell defaults to a sibling
	// of chiron. A checkout at another revision says nothing about the pin
	// and is skipped.
	devshell := os.Getenv("DEVSHELL_CHECKOUT")
	if devshell == "" {
		devshell = filepath.Join("..", "..", "..", "tooling-devshell")
	}
	for _, c := range []struct {
		dir, name, pin string
		contract       repoContract
	}{
		{devshell, "tooling-devshell", PinnedDevshellRev, devshellContract()},
		{os.Getenv("FACTORY_CHECKOUT"), "factory", PinnedFactoryRev, factoryContract()},
	} {
		if c.dir == "" || !IsDir(filepath.Join(c.dir, ".git")) {
			continue
		}
		if filepath.Base(c.dir) != c.name {
			t.Errorf("%s must be a directory named %s", c.dir, c.name)
			continue
		}
		head, err := exec.Command("git", "-C", c.dir, "rev-parse", "HEAD").Output()
		if err != nil || strings.TrimSpace(string(head)) != c.pin {
			t.Logf("%s is not at the pinned %s; skipping its contract", c.dir, shortRef(c.pin))
			continue
		}
		o := DefaultOptions()
		o.CodeDir = filepath.Dir(c.dir)
		if p, w := c.contract.verify(o); len(p) != 0 || len(w) != 0 {
			t.Errorf("%s contract at the pin: problems %v, warnings %v", c.name, p, w)
		}
	}
}

func TestDevshellBinariesStepNamesMissing(t *testing.T) {
	env := testEnv(t, allComponents())
	bin := filepath.Join(env.Opts.DevshellLink(), "bin")
	must(t, os.MkdirAll(bin, 0o755))
	for _, name := range []string{"kin-mcp", "constrain-mcp", "pact-mcp", "transmog-mcp", "kin", "constrain", "pact", "advocate", "transmogrify", "sim", "meditate", "gum", "doppler"} {
		must(t, os.WriteFile(filepath.Join(bin, name), []byte("#!/bin/sh\n"), 0o755))
	}
	st, detail, _ := devshellBinariesStep{}.Check(context.Background(), env)
	if st != StatusBlocked || !strings.Contains(detail, "signet-eval") {
		t.Fatalf("expected block naming signet-eval, got %v %q", st, detail)
	}
	must(t, os.WriteFile(filepath.Join(bin, "signet-eval"), []byte("#!/bin/sh\n"), 0o755))
	st, _, _ = devshellBinariesStep{}.Check(context.Background(), env)
	if st != StatusDone {
		t.Fatalf("expected done, got %v", st)
	}
}

func TestPinnedRevisionsLookLikeCommits(t *testing.T) {
	for _, rev := range []string{PinnedDevshellRev, PinnedFactoryRev} {
		if len(rev) != 40 || !shaRE.MatchString(rev) {
			t.Errorf("pin %q is not a full commit sha", rev)
		}
	}
	o := DefaultOptions()
	if o.DevshellRef != PinnedDevshellRev || o.FactoryRef != PinnedFactoryRev {
		t.Fatal("defaults must use the pins")
	}
}

// fakePythons puts executables named for each interpreter in a temporary
// directory, each reporting its version ("3 13") the way factoryVenvStep asks,
// and records every command the step runs.
func fakePythons(t *testing.T, env *Env, versions map[string]string) *[][]string {
	t.Helper()
	dir := t.TempDir()
	for name := range versions {
		must(t, os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/sh\n"), 0o755))
	}
	env.lookPath = func(name string) (string, error) {
		if _, ok := versions[name]; ok {
			return filepath.Join(dir, name), nil
		}
		if name == "make" {
			return "/fake/make", nil
		}
		return "", exec.ErrNotFound
	}
	var ran [][]string
	env.run = func(_ context.Context, c *exec.Cmd) error {
		ran = append(ran, c.Args)
		if filepath.Dir(c.Path) == dir {
			fmt.Fprintln(c.Stdout, versions[filepath.Base(c.Path)])
		}
		return nil
	}
	return &ran
}

// The Factory's Makefile finds only python3.12 by name and otherwise takes
// python3, Apple's 3.9 on a Mac. With Homebrew's 3.13 installed, make dev has
// to be told to build the venv from it.
func TestFactoryVenvBuildsFromAPythonThatMeetsTheFloor(t *testing.T) {
	env := testEnv(t, allComponents())
	writeTree(t, env.Opts.FactoryDir(), map[string]string{"pyproject.toml": "[project]\n"})
	ran := fakePythons(t, env, map[string]string{"python3": "3 9", "python3.13": "3 13"})
	if st, detail, _ := (factoryVenvStep{}).Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("expected missing with 3.13 present, got %v %q", st, detail)
	}
	*ran = nil
	must(t, factoryVenvStep{}.Apply(context.Background(), env))
	var makeArgs []string
	for _, args := range *ran {
		if args[0] == "/fake/make" {
			makeArgs = args[1:]
		}
	}
	if len(makeArgs) != 2 || makeArgs[0] != "dev" || !strings.HasPrefix(makeArgs[1], "PY_BOOTSTRAP=") || filepath.Base(makeArgs[1]) != "python3.13" {
		t.Fatalf("make dev not pointed at python3.13: %q", makeArgs)
	}
}

func TestFactoryVenvBlocksWhenEveryPythonIsTooOld(t *testing.T) {
	env := testEnv(t, allComponents())
	writeTree(t, env.Opts.FactoryDir(), map[string]string{"pyproject.toml": "[project]\n"})
	fakePythons(t, env, map[string]string{"python3": "3 9"})
	st, detail, _ := (factoryVenvStep{}).Check(context.Background(), env)
	if st != StatusBlocked || !strings.Contains(detail, "3.12+") || !strings.Contains(detail, "python3 3.9") {
		t.Fatalf("expected blocked naming the 3.9 found, got %v %q", st, detail)
	}
	if err := (factoryVenvStep{}).Apply(context.Background(), env); err == nil {
		t.Fatal("apply ran make with no qualifying python")
	}
}

// rustup.rs puts cargo in ~/.cargo/bin, which a shell only has on PATH
// after its rc file is re-read.
func TestHaveFindsWhatRustupInstalled(t *testing.T) {
	env := testEnv(t, allComponents())
	writeTree(t, filepath.Join(env.Opts.Home, ".cargo", "bin"), map[string]string{"cargo-factory-probe": "#!/bin/sh\n"})
	must(t, os.Chmod(filepath.Join(env.Opts.Home, ".cargo", "bin", "cargo-factory-probe"), 0o755))
	if p, ok := env.Have("cargo-factory-probe"); !ok || p != filepath.Join(env.Opts.Home, ".cargo", "bin", "cargo-factory-probe") {
		t.Fatalf("~/.cargo/bin not searched: %q %v", p, ok)
	}
	for _, keg := range []string{"/opt/homebrew/opt/rustup/bin", "/usr/local/opt/rustup/bin"} {
		if !slices.Contains(rustDirs(env.Opts.Home), keg) {
			t.Errorf("Homebrew's keg-only rustup at %s is not searched", keg)
		}
	}
}

func TestFactoryVenvStepResyncsWhenPyprojectMoves(t *testing.T) {
	env := testEnv(t, allComponents())
	dir := env.Opts.FactoryDir()
	writeTree(t, dir, map[string]string{
		"pyproject.toml":      "[project]\n",
		".venv/bin/factory":   "#!/bin/sh\n",
		".venv/.factory-deps": "",
	})
	fakePythons(t, env, map[string]string{"python3.13": "3 13"})
	old := time.Now().Add(-time.Hour)
	must(t, os.Chtimes(filepath.Join(dir, "pyproject.toml"), old, old))
	if st, detail, _ := (factoryVenvStep{}).Check(context.Background(), env); st != StatusDone {
		t.Fatalf("fresh stamp should be done, got %v %q", st, detail)
	}
	// A checkout that moved its pin rewrites pyproject.toml after the stamp.
	must(t, os.Chtimes(filepath.Join(dir, "pyproject.toml"), time.Now().Add(time.Hour), time.Now().Add(time.Hour)))
	if st, detail, _ := (factoryVenvStep{}).Check(context.Background(), env); st != StatusMissing || !strings.Contains(detail, "pyproject.toml") {
		t.Fatalf("expected a re-sync, got %v %q", st, detail)
	}
}

func TestDirenvRCResolvesLikeNixBootstrap(t *testing.T) {
	home := t.TempDir()
	t.Setenv("DIRENV_CONFIG", "")
	t.Setenv("XDG_CONFIG_HOME", "")
	if got, want := direnvRC(home), filepath.Join(home, ".config", "direnv", "direnvrc"); got != want {
		t.Fatalf("default: got %s want %s", got, want)
	}
	// Only the legacy file exists: direnv reads it.
	legacy := filepath.Join(home, ".direnvrc")
	must(t, os.WriteFile(legacy, nil, 0o644))
	if got := direnvRC(home); got != legacy {
		t.Fatalf("legacy: got %s want %s", got, legacy)
	}
	xdg := filepath.Join(home, "xdg")
	t.Setenv("XDG_CONFIG_HOME", xdg)
	writeTree(t, filepath.Join(xdg, "direnv"), map[string]string{"direnvrc": ""})
	if got, want := direnvRC(home), filepath.Join(xdg, "direnv", "direnvrc"); got != want {
		t.Fatalf("xdg: got %s want %s", got, want)
	}
	t.Setenv("DIRENV_CONFIG", filepath.Join(home, "dc"))
	writeTree(t, filepath.Join(home, "dc"), map[string]string{"direnvrc": ""})
	if got, want := direnvRC(home), filepath.Join(home, "dc", "direnvrc"); got != want {
		t.Fatalf("DIRENV_CONFIG: got %s want %s", got, want)
	}
}
