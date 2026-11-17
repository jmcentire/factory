package setup

import (
	"context"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
)

// Seat is the agent, and optionally the model, that holds one Factory role.
// An empty Model runs the agent's own configured default.
type Seat struct {
	Agent string
	Model string
}

// ParseSeat reads AGENT[:MODEL]. An agent never contains ':', so a model such
// as glm-5.3:cloud may.
func ParseSeat(text string) Seat {
	agent, model, _ := strings.Cut(strings.TrimSpace(text), ":")
	return Seat{Agent: agent, Model: model}
}

func (s Seat) String() string {
	if s.Model == "" {
		return s.Agent
	}
	return s.Agent + ":" + s.Model
}

// Seats binds every Factory role to the agent and model that runs it.
type Seats struct {
	Validator    Seat
	Orchestrator Seat
	Coder        Seat
	Tester       Seat
}

// DefaultSeats is the standard set factory-setup ships: Claude validates,
// Antigravity orchestrates, Codex codes, cursor-agent Grok tests. It is a
// default, not a rule: the flags, the menu, or the human at run time change it.
func DefaultSeats() Seats {
	return Seats{
		Validator:    Seat{Agent: "claude", Model: "claude-opus-5-5"},
		Orchestrator: Seat{Agent: "agy", Model: "gemini-4-carbon-high"},
		Coder:        Seat{Agent: "codex", Model: "gpt-6.1-sol"},
		Tester:       Seat{Agent: "cursor-agent", Model: "grok-4.7-high"},
	}
}

// The agents each seat accepts: the Validator is the agent /factory runs in; the
// Orchestrator is one the pinned Factory's harness/run_record.py resident_command
// can launch; Coder and Tester as harness/tmux_lane.sh lists them.
var (
	validatorAgents    = []string{"codex", "ollama", "claude", "claude-ollama", "agy", "muse", "cursor-agent", "prime-agent"}
	orchestratorAgents = []string{"agy", "codex", "claude", "muse", "cursor-agent", "prime-agent"}
	laneAgents         = []string{"codex", "codex-interactive", "codex-ollama", "cursor-agent", "claude", "claude-ollama", "agy", "muse", "prime-agent"}
	// Agents on a local Ollama model have no default to fall back on.
	agentsNeedingModel = []string{"ollama", "claude-ollama", "codex-ollama"}
)

// Validate rejects a seat the Factory harness cannot launch.
func (s Seats) Validate() error {
	for _, c := range []struct {
		role  string
		seat  Seat
		allow []string
	}{
		{"validator", s.Validator, validatorAgents},
		{"orchestrator", s.Orchestrator, orchestratorAgents},
		{"coder", s.Coder, laneAgents},
		{"tester", s.Tester, laneAgents},
	} {
		if !slices.Contains(c.allow, c.seat.Agent) {
			return fmt.Errorf("%s agent must be one of %s, got %q", c.role, strings.Join(c.allow, "|"), c.seat.Agent)
		}
		if c.seat.Model == "" && slices.Contains(agentsNeedingModel, c.seat.Agent) {
			return fmt.Errorf("%s on %s needs a model (%s:<model>)", c.role, c.seat.Agent, c.seat.Agent)
		}
	}
	return nil
}

// runs reports whether any seat runs agent.
func (s Seats) runs(agent string) bool {
	return slices.ContainsFunc([]Seat{s.Validator, s.Orchestrator, s.Coder, s.Tester}, func(x Seat) bool { return x.Agent == agent })
}

// SeatAgents are the agents the seats run that factory-setup installs and
// configures, in menu order. The Cursor CLI is not one: cursorInstallStep
// installs it.
func SeatAgents(s Seats) []AgentID {
	var out []AgentID
	for _, a := range agents {
		if slices.ContainsFunc([]Seat{s.Validator, s.Orchestrator, s.Coder, s.Tester}, func(x Seat) bool { return seatBinary(x.Agent) == a.Binary }) {
			out = append(out, a.ID)
		}
	}
	return out
}

// seatBinary is the executable a seat's agent runs as.
func seatBinary(agent string) string {
	switch agent {
	case "ollama", "codex-interactive", "codex-ollama": // ollama is Codex on a local model
		return "codex"
	case "claude-ollama":
		return "claude"
	case "cursor-agent":
		return "agent"
	}
	return agent
}

// seatInstall is how to install a seat's executable when it is missing.
func seatInstall(binary string) string {
	switch binary {
	case "claude", "codex", "agy", "prime-agent":
		for _, a := range agents {
			if a.Binary == binary {
				return a.Install
			}
		}
	case "agent":
		return "curl https://cursor.com/install -fsS | bash"
	case "ollama":
		return "https://ollama.com"
	}
	return "see harness/agents.py in the Factory checkout"
}

// seatToolName is how a person knows a seat's executable.
var seatToolName = map[string]string{
	"claude":      "Claude Code",
	"codex":       "the Codex CLI",
	"agy":         "the Antigravity CLI (agy)",
	"agent":       "the Cursor CLI (cursor-agent)",
	"prime-agent": "prime-agent",
	"ollama":      "Ollama",
}

// MissingSeatTools is a next step for each seat's executable that is not
// installed, once per executable, and for an `agent` on PATH that is not
// Cursor's: the tester seat runs whatever answers to that name.
func MissingSeatTools(env *Env) []NextStep {
	s := env.Opts.Seats
	seen := map[string]bool{}
	var out []NextStep
	for _, c := range []struct {
		role string
		seat Seat
	}{{"validator", s.Validator}, {"orchestrator", s.Orchestrator}, {"coder", s.Coder}, {"tester", s.Tester}} {
		bin := seatBinary(c.seat.Agent)
		if seen[bin] {
			continue
		}
		seen[bin] = true
		name := seatToolName[bin]
		if name == "" {
			name = bin
		}
		// An agent this run installs says itself what it still needs.
		if a := slices.IndexFunc(agents, func(a *Agent) bool { return a.Binary == bin }); a >= 0 && env.Opts.Has(ComponentAgents) && slices.Contains(env.Opts.Agents, agents[a].ID) {
			continue
		}
		path, ok := env.Have(bin)
		if ok && bin == "agent" && !isCursorAgent(path) {
			text := path + " is another tool named agent; the " + c.role + " seat runs agent and needs Cursor's command-line agent. Install it, or give the seat to another agent (for example --tester codex:gpt-6.1-sol)."
			if path == env.Opts.LocalBin("agent") {
				text += " Cursor's installer replaces " + path + "."
			}
			out = append(out, NextStep{
				Title: "Put the Cursor CLI first on your PATH, for the " + c.role + " seat",
				Text:  text,
				Cmds:  []string{seatInstall(bin)},
			})
			continue
		}
		if ok {
			continue
		}
		step := NextStep{Title: "Install " + name + " for the " + c.role + " seat"}
		if bin == "agent" {
			step.Text = "The command-line agent, not the Cursor desktop app."
		}
		if install := seatInstall(bin); strings.HasPrefix(install, "http") || strings.HasPrefix(install, "see ") {
			step.Text = strings.TrimSpace(step.Text + " " + strings.ToUpper(install[:1]) + install[1:] + ".")
		} else {
			step.Cmds = []string{install}
			// npm comes with Node.js, which a fresh machine lacks.
			if strings.HasPrefix(install, "npm ") {
				if _, ok := env.Have("npm"); !ok {
					step.Text = "It installs with npm, which comes with Node.js: install Node.js first (https://nodejs.org)."
				}
			}
		}
		out = append(out, step)
	}
	return out
}

// isCursorAgent reports whether an agent executable is Cursor's: its
// installer keeps every version under a cursor-agent directory and links
// agent and cursor-agent to it.
func isCursorAgent(path string) bool {
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		real = path
	}
	return strings.Contains(real, "cursor")
}

// cursorInstallStep installs the Cursor CLI, which a seat on cursor-agent runs
// as agent. It is not one of the configured agents: the Factory starts it
// with everything it needs.
type cursorInstallStep struct{}

func (cursorInstallStep) ID() string    { return "agent.cursor-agent.install" }
func (cursorInstallStep) Title() string { return "Install the Cursor CLI (cursor-agent)" }

func (cursorInstallStep) Check(_ context.Context, env *Env) (Status, string, error) {
	if p, ok := env.Have("agent"); ok {
		if isCursorAgent(p) {
			return StatusDone, p, nil
		}
		// Cursor's installer replaces ~/.local/bin/agent: another tool's
		// agent is the person's to give up, and MissingSeatTools says how.
		return StatusSkipped, p + " is another tool named agent", nil
	}
	return StatusMissing, seatInstall("agent"), nil
}

func (cursorInstallStep) Apply(ctx context.Context, env *Env) error {
	return env.Shell(ctx, env.Opts.Home, seatInstall("agent"))
}
