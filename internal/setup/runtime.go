package setup

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"time"
)

// What the Factory needs once it runs, rather than to install: tmux for its
// sessions, GNU timeout for its mutation and flake checks, and every agent it
// starts signed in. runtimeToolsStep installs the tools where it can; what is
// left is a next step, as are the sign-ins.

// missingRuntimeTools are the tools a Factory run uses that this machine
// lacks: what each is for, and its Homebrew and apt/dnf packages.
func missingRuntimeTools(env *Env) (names, brew, apt []string) {
	if _, ok := env.Have("tmux"); !ok {
		names, brew, apt = append(names, "tmux, which runs the Factory's sessions"), append(brew, "tmux"), append(apt, "tmux")
	}
	// macOS ships no GNU timeout; coreutils installs it as gtimeout.
	if !haveAny(env, "timeout", "gtimeout") {
		names, brew, apt = append(names, "GNU timeout, which its mutation and flake checks run under"), append(brew, "coreutils"), append(apt, "coreutils")
	}
	return names, brew, apt
}

// RuntimeTools is a next step installing the tools a Factory run uses that
// this machine lacks, or nil.
func RuntimeTools(env *Env) *NextStep {
	if !env.Opts.Has(ComponentFactory) {
		return nil
	}
	names, brew, apt := missingRuntimeTools(env)
	if len(names) == 0 {
		return nil
	}
	next := &NextStep{Title: "Install what a Factory run uses", Text: "Missing: " + listed(names) + "."}
	if runtime.GOOS == "darwin" {
		next.Cmds = []string{"brew install " + strings.Join(brew, " ")}
		if _, ok := env.Have("brew"); !ok {
			next.Text += " Homebrew comes first: https://brew.sh."
		}
	} else if cmd := linuxInstall(env, apt, apt); cmd != "" {
		next.Cmds = []string{cmd}
	} else {
		next.Text += " Install them with your package manager."
	}
	return next
}

// runtimeToolsStep installs tmux and GNU timeout: with Homebrew on a Mac, and
// on Linux with apt or dnf as root, or through sudo with a person at the
// terminal to type the password. Anywhere else they stay a next step.
type runtimeToolsStep struct{}

func (runtimeToolsStep) ID() string    { return "factory.tools" }
func (runtimeToolsStep) Title() string { return "Install tmux and GNU timeout for Factory runs" }

func (runtimeToolsStep) Check(_ context.Context, env *Env) (Status, string, error) {
	cmd, ok := runtimeToolsInstall(env)
	switch {
	case cmd == "":
		return StatusDone, "", nil
	case !ok:
		return StatusSkipped, cmd, nil
	}
	return StatusMissing, cmd, nil
}

func (runtimeToolsStep) Apply(ctx context.Context, env *Env) error {
	cmd, _ := runtimeToolsInstall(env)
	return env.Shell(ctx, env.Opts.Home, cmd)
}

// runtimeToolsInstall is the command installing the missing runtime tools,
// "" when none are missing, and whether this run can run it.
func runtimeToolsInstall(env *Env) (string, bool) {
	_, brew, apt := missingRuntimeTools(env)
	if len(brew) == 0 {
		return "", true
	}
	if runtime.GOOS == "darwin" {
		_, ok := env.Have("brew")
		return "brew install " + strings.Join(brew, " "), ok
	}
	cmd := linuxInstall(env, apt, apt)
	return cmd, cmd != "" && (os.Geteuid() == 0 || env.Interactive)
}

// SignIns is a next step signing in each agent the install configured or a
// Factory seat runs, less those already signed in, or nil. Claude Code and
// Codex say whether they are; Antigravity keeps a Google sign-in in
// ~/.gemini, and `agy models` must list each model an agy seat runs; the
// Cursor CLI keeps its in the keychain, where it cannot be read without
// starting a login, so it is listed for anyone who has not yet.
func SignIns(ctx context.Context, env *Env) *NextStep {
	o := env.Opts
	parent := ctx
	uses := func(agent AgentID, seatAgents ...string) bool {
		if slices.Contains(o.Agents, agent) {
			return true
		}
		if !o.Has(ComponentFactory) {
			return false
		}
		for _, seat := range []Seat{o.Seats.Validator, o.Seats.Orchestrator, o.Seats.Coder, o.Seats.Tester} {
			if slices.Contains(seatAgents, seat.Agent) {
				return true
			}
		}
		return false
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	var parts []NextStep
	if uses(AgentClaude, "claude", "claude-ollama") && !claudeSignedIn(ctx, env) {
		parts = append(parts, NextStep{Title: "Claude Code", Cmds: []string{"claude auth login"}})
	}
	if uses(AgentCodex, "codex", "codex-interactive", "codex-ollama", "ollama") {
		if out, err := env.Output(ctx, "codex", "login", "status"); err != nil || !strings.Contains(out, "Logged in") {
			parts = append(parts, NextStep{Title: "The Codex CLI", Cmds: []string{"codex login"}})
		}
	}
	if uses(AgentAntigravity, "agy") {
		if !Exists(filepath.Join(o.Home, ".gemini", "oauth_creds.json")) {
			var models []string
			if o.Has(ComponentFactory) {
				models = agyModelsRun(o.Seats)
			}
			parts = append(parts, NextStep{
				Title: "The Antigravity CLI",
				Text:  "It has no login command: start it once and, when it asks, " + agySignIn(models...) + ".",
				Cmds:  []string{"agy"},
			})
		} else if o.Has(ComponentFactory) {
			parts = append(parts, agySeatModels(parent, env)...)
		}
	}
	if uses("", "cursor-agent") {
		parts = append(parts, NextStep{Title: "The Cursor CLI, if you have not yet", Cmds: []string{"cursor-agent login"}})
	}
	if len(parts) == 0 {
		return nil
	}
	return &NextStep{
		Title: "Sign in to your agents",
		Text:  "Each runs under your own account; the Factory starts them, but cannot sign them in.",
		Parts: parts,
	}
}

// claudeSignedIn reads `claude auth status`, which prints {"loggedIn": ...}.
func claudeSignedIn(ctx context.Context, env *Env) bool {
	out, err := env.Output(ctx, "claude", "auth", "status")
	if err != nil {
		return false
	}
	var status struct {
		LoggedIn bool `json:"loggedIn"`
	}
	return json.Unmarshal([]byte(out), &status) == nil && status.LoggedIn
}

// agyProject is Wander's Google Cloud project for Antigravity. Signed in on a
// personal plan instead, agy cannot run Wander's models: it quietly runs a
// Flash model, or stops at "Individual quota reached" for a week.
const agyProject = "antigravity-511118"

// agySignIn is the one wording of how to sign agy in on Wander's project:
// the wander.com account wherever a Google account is asked for, then the
// billing model, Google Cloud and the project. The "Subscriptions" billing
// model bills a Gemini subscription, not Wander's Cloud project. models, when
// given, are what `agy models` should list afterwards.
func agySignIn(models ...string) string {
	s := `sign in with your wander.com Google account: choose "Pay As You Go" (not "Subscriptions"), then "Continue with Google Cloud", and enter project ` + agyProject
	if len(models) > 0 {
		s += "; `agy models` should then list " + listed(models)
	}
	return s
}

// agyModelsRun are the models the seats run agy on, each once, in seat order.
func agyModelsRun(s Seats) []string {
	var models []string
	for _, seat := range []Seat{s.Validator, s.Orchestrator, s.Coder, s.Tester} {
		if seat.Agent == "agy" && seat.Model != "" && !slices.Contains(models, seat.Model) {
			models = append(models, seat.Model)
		}
	}
	return models
}

// agySeatModels is a next step for each Factory seat whose agy model
// `agy models` does not list. The listing gets its own minute, a closed stdin
// so a login prompt cannot wait on the terminal, and runs once for all seats.
func agySeatModels(ctx context.Context, env *Env) []NextStep {
	if _, ok := env.Have("agy"); !ok {
		return nil // MissingSeatTools says to install it.
	}
	s := env.Opts.Seats
	var out []NextStep
	var listed []string
	var why string
	ran := false
	for _, c := range []struct {
		role string
		seat Seat
	}{{"validator", s.Validator}, {"orchestrator", s.Orchestrator}, {"coder", s.Coder}, {"tester", s.Tester}} {
		if c.seat.Agent != "agy" || c.seat.Model == "" {
			continue
		}
		if !ran {
			listed, why = agyModels(ctx, env)
			ran = true
		}
		if slices.Contains(listed, c.seat.Model) {
			continue
		}
		if why == "" {
			why = "does not list it"
		}
		text := "The " + c.role + " seat runs agy on " + c.seat.Model + ", but `agy models` " + why + ", so agy would run a lesser model or none. "
		if strings.Contains(strings.ToLower(why), "quota") {
			text += "A quota limit usually means agy is signed in on a personal plan, not Wander's. "
		}
		text += "Sign out of agy (or move ~/.gemini/oauth_creds.json aside), then " + agySignIn(c.seat.Model) +
			". Or give the seat to another agent: --" + c.role + " <agent>:<model>."
		out = append(out, NextStep{Title: "Antigravity's " + c.seat.Model + ", for the " + c.role + " seat", Text: text, Cmds: []string{"agy"}})
	}
	return out
}

// agyModels lists the model ids `agy models` prints, one per line before a
// tab, or says why it could not.
func agyModels(ctx context.Context, env *Env) ([]string, string) {
	ctx, cancel := context.WithTimeout(ctx, time.Minute)
	defer cancel()
	out, err := env.Output(ctx, "agy", "models")
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return nil, "did not answer within a minute (offline, or waiting on a sign-in)"
	}
	var ids []string
	var quota string
	for _, line := range strings.Split(out, "\n") {
		if id, _, ok := strings.Cut(line, "\t"); ok {
			ids = append(ids, strings.TrimSpace(id))
		} else if quota == "" && strings.Contains(strings.ToLower(line), "quota") {
			quota = strings.TrimSpace(line)
		}
	}
	switch {
	case err != nil:
		return ids, "failed (" + strings.TrimSuffix(strings.TrimPrefix(err.Error(), "agy models: "), ": ") + ")"
	case quota != "":
		return ids, "reports a quota (" + quota + ")"
	case len(ids) == 0:
		return nil, "printed no models"
	}
	return ids, ""
}
