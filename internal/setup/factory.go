package setup

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// factoryVenvStep runs the Factory's own `make dev`, which creates its
// repo-managed virtualenv and installs the package with dev tooling.
type factoryVenvStep struct{}

func (factoryVenvStep) ID() string    { return "factory.venv" }
func (factoryVenvStep) Title() string { return "Install the Factory runtime (make dev)" }

// pythonCandidates are the interpreters tried for the Factory's venv and
// kindex's, newest first.
var pythonCandidates = []string{"python3.14", "python3.13", "python3.12", "python3"}

// findPython is the newest interpreter at or above the Factory's 3.12 floor,
// or, when none is, what was found ("/usr/bin/python3 3.9") for the message.
func findPython(ctx context.Context, env *Env) (string, []string) {
	var tooOld []string
	for _, name := range pythonCandidates {
		p, ok := env.Have(name)
		if !ok {
			continue
		}
		var major, minor int
		v, err := env.Output(ctx, p, "-c", "import sys; print(*sys.version_info[:2])")
		if _, scanErr := fmt.Sscan(v, &major, &minor); err != nil || scanErr != nil {
			tooOld = append(tooOld, p+" (does not run)")
			continue
		}
		if major > 3 || major == 3 && minor >= 12 {
			return p, nil
		}
		tooOld = append(tooOld, fmt.Sprintf("%s %d.%d", p, major, minor))
	}
	return "", tooOld
}

func pythonBlocked(tooOld []string) string {
	found := ""
	if len(tooOld) > 0 {
		found = "; found " + strings.Join(tooOld, ", ")
	}
	return "python 3.12+ not found" + found + " (" + osHint("python") + ")"
}

func (s factoryVenvStep) Check(ctx context.Context, env *Env) (Status, string, error) {
	dir := env.Opts.FactoryDir()
	if !IsDir(dir) {
		return StatusMissing, "", nil
	}
	if py, tooOld := findPython(ctx, env); py == "" {
		return StatusBlocked, pythonBlocked(tooOld), nil
	}
	if _, ok := env.Have("make"); !ok {
		return StatusBlocked, "make not found (" + osHint("make") + ")", nil
	}
	if !Exists(filepath.Join(dir, ".venv", "bin", "factory")) {
		return StatusMissing, "", nil
	}
	// The Makefile re-syncs dependencies when pyproject.toml is newer than
	// its stamp, which is what a moved pin looks like.
	stamp, err1 := os.Stat(filepath.Join(dir, ".venv", ".factory-deps"))
	pyproject, err2 := os.Stat(filepath.Join(dir, "pyproject.toml"))
	if err1 != nil || err2 != nil || pyproject.ModTime().After(stamp.ModTime()) {
		return StatusMissing, "pyproject.toml changed; re-syncing the venv", nil
	}
	return StatusDone, ".venv/bin/factory", nil
}

// Apply hands the Makefile the interpreter Check approved. On its own it
// looks only for python3.12 and otherwise takes python3, which on a Mac is
// Apple's 3.9: a machine with Homebrew's 3.13 passed Check and then failed
// here. PY_BOOTSTRAP, not the PY its message suggests: an explicit PY turns
// off the repo-managed venv and pip-installs into that interpreter, which
// Homebrew's refuses.
func (s factoryVenvStep) Apply(ctx context.Context, env *Env) error {
	py, tooOld := findPython(ctx, env)
	if py == "" {
		return fmt.Errorf("%s", pythonBlocked(tooOld))
	}
	return env.Run(ctx, env.Opts.FactoryDir(), "make", "dev", "PY_BOOTSTRAP="+py)
}

// FactoryPrompt is one lane prompt the installer exposes as a slash command in
// every selected agent. The files are read from the checkout at run time, so
// an upstream edit propagates without regenerating anything.
type FactoryPrompt struct {
	Name        string // slash command name
	File        string // relative to the factory checkout; empty when Path is set
	Description string
	// Path, when set, names an installer-generated file outside the checkout.
	Path func(Options) string
}

// FactoryPrompts lists the canonical prompts from prompts/README.md plus the
// machine-specific /factory operating skill the installer generates.
var FactoryPrompts = []FactoryPrompt{
	{Name: "factory", Description: "Use the Software Factory to complete a task: task file, factory.sh start, lanes, verdict", Path: Options.SkillPath},
	{Name: "validate", File: "prompts/validate.md", Description: "Factory Validator lane: owns the human relationship, the specification, independent tests and the verdict"},
	{Name: "engineer", File: "prompts/engineer.md", Description: "Factory Coder lane: the implementation, against the specification"},
	{Name: "test", File: "prompts/test.md", Description: "Factory Tester lane: the tests, against the specification; never reads the implementation"},
	{Name: "build", File: "prompts/build.md", Description: "Single-agent research-plan-build-ship pipeline; collapsed roles, so cosmetic and small standard work only"},
	{Name: "orchestrate", File: "prompts/orchestrate.md", Description: "Factory orchestrator seat beside the enforcing dispatcher scripts"},
	{Name: "code-review", File: "prompts/code-review.md", Description: "The code-review standard every reviewing agent binds to"},
	{Name: "diff-intent-gate", File: "prompts/diff-intent-gate.md", Description: "Standing directive: diffs are checked against declared intent"},
	{Name: "review", File: "skills/review.md", Description: "Alignment review of built work: the Validator's adversarial review, or an independent post-run check"},
}

// resolve returns the absolute file a loader points at.
func (p FactoryPrompt) resolve(o Options) string {
	if p.Path != nil {
		return p.Path(o)
	}
	return filepath.Join(o.FactoryDir(), p.File)
}

// promptLoader renders the thin loader body used for agents that accept
// markdown prompt files with $ARGUMENTS substitution. The loader instructs the
// agent to read the canonical file rather than embedding a copy, matching the
// upstream "thin loaders, no fan-out" design.
// host is the seat name of the agent the loader is for ("claude", "codex", …), or ""
// when that agent holds no Factory seat; the /factory loader states it, because the
// agent running /factory is the Validator.
func promptLoader(p FactoryPrompt, o Options, argToken, host string) string {
	path := p.resolve(o)
	var b strings.Builder
	if p.Path != nil {
		fmt.Fprintf(&b, "Read %s in full and follow it as the operating procedure for this request.\n\n", path)
		fmt.Fprintf(&b, "It names the Factory checkout, the runs root and the seats for this machine, and how to start and run the task. Their words are the authority; never claim agreement they did not give.\n\n")
		if host != "" {
			fmt.Fprintf(&b, "You run this as the Validator seat: `%s`.\n\n", host)
		}
		fmt.Fprintf(&b, "The task: %s\n", argToken)
		return b.String()
	}
	fmt.Fprintf(&b, "Read %s in full and act as that role for the rest of this session.\n\n", path)
	fmt.Fprintf(&b, "That file is the canonical source; do not summarise it, follow it.\n\n")
	// The prompts cite the checkout as $FACTORY_HOME (prompts/README.md,
	// "Locations"); the loader states the actual path once.
	fmt.Fprintf(&b, "$FACTORY_HOME on this machine is %s.\n\n", o.FactoryDir())
	fmt.Fprintf(&b, "Arguments: %s\n", argToken)
	return b.String()
}
