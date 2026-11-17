// Package setup installs and configures Wander's agent development tooling on a
// developer machine: Determinate Nix, the tooling-devshell flake, the Software
// Factory, the signet-eval policy hook, and the per-agent configuration that
// makes all of it reachable from Claude Code, Codex, OpenCode, prime-agent, pi,
// and Antigravity.
//
// Every action is a Step with an idempotent Check and an Apply. A Plan is the
// ordered list of steps selected from Options; Runner executes it, honouring
// dry-run, and reports each outcome so a re-run only does what is missing.
package setup

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Component is a top-level installable selected in the menu.
type Component string

const (
	ComponentNix      Component = "nix"
	ComponentDevshell Component = "devshell"
	ComponentFactory  Component = "factory"
	ComponentPolicy   Component = "policy"
	ComponentAgents   Component = "agents"
	// ComponentKindex is Kindex and Kinbase without Nix, for a machine that
	// skips the devshell. It is not in AllComponents: the devshell already
	// carries both.
	ComponentKindex Component = "kindex"
)

// AllComponents lists components in install order.
var AllComponents = []Component{
	ComponentNix, ComponentDevshell, ComponentFactory, ComponentPolicy, ComponentAgents,
}

// Options is everything the user chose, from the TUI or from flags.
type Options struct {
	Components []Component
	Agents     []AgentID

	// CodeDir is where repositories are cloned.
	CodeDir string

	// DevshellRepo/Ref and FactoryRepo/Ref pin what is cloned.
	DevshellRepo string
	DevshellRef  string
	FactoryRepo  string
	FactoryRef   string

	// GlobalProfile also adds the devshell to the user's nix profile so the
	// tools are on PATH outside direnv-managed repos.
	GlobalProfile bool

	// Seats are the agent and model for each Factory role, baked into the
	// /factory skill and the agents' memory as the default.
	Seats Seats
	// CoderChosen is set when the human named the Coder seat (flag or menu):
	// /factory then never swaps it off the Validator's family.
	CoderChosen bool

	DryRun bool
	Home   string
}

// DefaultOptions returns the options a fresh machine would use.
func DefaultOptions() Options {
	home, _ := os.UserHomeDir()
	return Options{
		Components:   AllComponents,
		Agents:       nil,
		CodeDir:      filepath.Join(home, "Code"),
		DevshellRepo: "https://github.com/wandercom/tooling-devshell",
		DevshellRef:  PinnedDevshellRev,
		FactoryRepo:  "https://github.com/wandercom/chiron",
		FactoryRef:   PinnedFactoryRev,
		Home:         home,
		Seats:        DefaultSeats(),
	}
}

// nowUTC is the timestamp written into generated files.
func nowUTC() string { return time.Now().UTC().Format(time.RFC3339) }

// Pinned revisions the installer was verified against. Both repositories are
// moving targets the installer does not own; cloning a branch head would let
// an upstream change silently alter what a fresh machine gets. Bump these
// deliberately, re-run the contract checks, and update the assumptions in
// contract.go if they changed.
const (
	// wandercom/tooling-devshell dev, 2026-11-06 ("Merge pull request #108"): the policy hook at its
	// main, which keeps the agy adapter fix and drops the GitHub identity guard that blocked everyone
	// but its author from git and gh. Also kinbase 0.1.2 and kindex 0.50.1 (#106).
	PinnedDevshellRev = "cef6c1a9fc25e050e3f08e48eaffaace4937bee7"
	// wandercom/chiron main, 2026-11-16 ("Merge pull request #119"): Factory 0.8.30,
	// consolidated ordinary and hardened startup.
	PinnedFactoryRev = "ecddf63b6d1cfd83fb290c488bed1ca43279ae3d"
)

// Has reports whether a component was selected.
func (o Options) Has(c Component) bool {
	for _, x := range o.Components {
		if x == c {
			return true
		}
	}
	return false
}

// HasAgent reports whether an agent was selected.
func (o Options) HasAgent(id AgentID) bool {
	for _, x := range o.Agents {
		if x == id {
			return true
		}
	}
	return false
}

// DevshellDir is the tooling-devshell checkout.
func (o Options) DevshellDir() string { return filepath.Join(o.CodeDir, "tooling-devshell") }

// FactoryDir is the Software Factory checkout.
func (o Options) FactoryDir() string { return filepath.Join(o.CodeDir, "factory") }

// StateDir holds installer-owned artifacts such as the built devshell link.
func (o Options) StateDir() string {
	return filepath.Join(o.Home, ".local", "state", "wander")
}

// DevshellLink is a stable out-link to the built devshell environment. Agent
// configuration references binaries under it so MCP servers keep working after
// the nix store path changes.
func (o Options) DevshellLink() string { return filepath.Join(o.StateDir(), "devshell") }

// DevshellBin returns the path a devshell binary will have once built.
func (o Options) DevshellBin(name string) string {
	return filepath.Join(o.DevshellLink(), "bin", name)
}

// Validate rejects option combinations that cannot work.
func (o Options) Validate() error {
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		return fmt.Errorf("factory-setup supports macOS and Linux; running on %s", runtime.GOOS)
	}
	if o.CodeDir == "" || o.Home == "" {
		return errors.New("code directory and home must be set")
	}
	if len(o.Components) == 0 {
		return errors.New("nothing selected")
	}
	if o.Has(ComponentAgents) && len(o.Agents) == 0 {
		return errors.New("agents component selected but no agent chosen")
	}
	for _, id := range o.Agents {
		if _, ok := agentByID(id); !ok {
			return fmt.Errorf("unknown agent %q", id)
		}
	}
	return nil
}

// Status is the result of a Step's Check.
type Status int

const (
	StatusMissing Status = iota // Apply is needed
	StatusDone                  // already in the desired state
	StatusBlocked               // cannot proceed; Reason explains
	// StatusSkipped is a step this machine cannot do yet for want of something
	// only the person can get (an access grant), which the rest of the install
	// does not need. The run goes on; the detail says what to get.
	StatusSkipped
)

// Step is one idempotent unit of installation.
type Step interface {
	// ID is a stable short identifier used in logs and --only/--skip flags.
	ID() string
	// Title is the human-readable one-liner.
	Title() string
	// Check reports the current state without changing anything.
	Check(ctx context.Context, env *Env) (Status, string, error)
	// Apply moves the machine to the desired state. It is only called when
	// Check returned StatusMissing.
	Apply(ctx context.Context, env *Env) error
}

// Plan is the ordered steps derived from Options.
type Plan struct {
	Options Options
	Steps   []Step
}

// BuildPlan expands Options into concrete steps, in dependency order.
func BuildPlan(o Options) (*Plan, error) {
	if err := o.Validate(); err != nil {
		return nil, err
	}
	p := &Plan{Options: o}
	add := func(s ...Step) { p.Steps = append(p.Steps, s...) }

	add(&preflightStep{})
	if o.Has(ComponentNix) {
		add(&nixInstallStep{}, &nixFlakesStep{})
	}
	if o.Has(ComponentDevshell) {
		add(
			&cloneStep{id: "devshell.clone", title: "Clone tooling-devshell", repo: o.DevshellRepo, ref: o.DevshellRef, dest: o.DevshellDir()},
			&contractStep{c: devshellContract()},
			&devshellBootstrapStep{},
			&direnvHookStep{},
			&devshellBuildStep{},
			&devshellBinariesStep{},
		)
		if o.GlobalProfile {
			add(&devshellProfileStep{})
		}
	}
	if o.Has(ComponentFactory) {
		add(
			&cloneStep{id: "factory.clone", title: "Clone the Software Factory", repo: o.FactoryRepo, ref: o.FactoryRef, dest: o.FactoryDir()},
			&contractStep{c: factoryContract()},
			&factoryVenvStep{},
			&factorySkillStep{},
			&runtimeToolsStep{},
		)
	}
	if o.Has(ComponentKindex) {
		// kinbase builds with cargo, so Rust goes in first.
		add(&rustInstallStep{minor: kinbaseRustMinor}, &kinbaseInstallStep{}, &kindexInstallStep{})
	}
	if o.Has(ComponentPolicy) {
		add(&policyRulesStep{})
	}
	if o.Has(ComponentAgents) {
		for _, id := range o.Agents {
			a, _ := agentByID(id)
			add(&agentInstallStep{agent: a})
			add(&agentConfigureStep{agent: a})
			add(&agentMemoryStep{agent: a})
		}
		if o.Has(ComponentFactory) && o.Seats.runs("cursor-agent") {
			add(&cursorInstallStep{})
		}
	}
	if o.Has(ComponentKindex) || o.Has(ComponentDevshell) {
		// Last: it needs Doppler access and a browser sign-in, which not
		// every machine has yet, and nothing else waits on it.
		add(&kinbaseConnectStep{})
	}
	return p, nil
}

// Outcome records what happened to one step.
type Outcome struct {
	Step    Step
	Status  Status
	Applied bool
	Detail  string
	Err     error
	// Next is what to do about a skipped or blocked step.
	Next *NextStep
}

// Runner executes a Plan.
type Runner struct {
	Env *Env
	// OnStart and OnFinish, when set, receive progress for a UI.
	OnStart  func(Step)
	OnFinish func(Outcome)
}

// Run checks and applies each step in order. It stops at the first error or
// blocked step, because later steps depend on earlier ones.
func (r *Runner) Run(ctx context.Context, p *Plan) ([]Outcome, error) {
	var out []Outcome
	var firstBlocked error
	for _, s := range p.Steps {
		if r.OnStart != nil {
			r.OnStart(s)
		}
		oc := Outcome{Step: s}
		status, detail, err := s.Check(ctx, r.Env)
		oc.Status, oc.Detail, oc.Err = status, detail, err
		switch {
		case err != nil:
		case status == StatusMissing:
			if r.Env.DryRun {
				oc.Detail = joinDetail("would apply", detail)
			} else if err := s.Apply(ctx, r.Env); err != nil {
				oc.Err = err
			} else {
				oc.Applied = true
				oc.Status = StatusDone
			}
		}
		if n, ok := s.(nexter); ok && (oc.Status == StatusSkipped || oc.Status == StatusBlocked) {
			next := n.NextStep()
			oc.Next = &next
		}
		if r.OnFinish != nil {
			r.OnFinish(oc)
		}
		out = append(out, oc)
		if oc.Err != nil {
			return out, fmt.Errorf("%s: %w", s.ID(), oc.Err)
		}
		if oc.Status == StatusBlocked {
			if r.Env.DryRun {
				// Keep going so the user sees the whole plan; the summary
				// still reports the first blocker.
				if firstBlocked == nil {
					firstBlocked = fmt.Errorf("%s: blocked: %s", s.ID(), oc.Detail)
				}
				continue
			}
			return out, fmt.Errorf("%s: blocked: %s", s.ID(), oc.Detail)
		}
	}
	return out, firstBlocked
}

func joinDetail(parts ...string) string {
	var keep []string
	for _, p := range parts {
		if strings.TrimSpace(p) != "" {
			keep = append(keep, p)
		}
	}
	return strings.Join(keep, ": ")
}
