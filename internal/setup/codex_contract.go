package setup

import (
	"context"
	"path/filepath"
)

// The pinned Factory states what each lane agent's CLI must offer in
// harness/agent_contract.sh, the check tmux_lane.sh runs at launch. Setup runs
// the same script, so a newer Factory's needs apply without a change here, and
// updates an agent that falls short. An agent app's PATH can find an older copy
// than the person's shell does, so /factory's preflight runs it where the run will.

const agentContract = "harness/agent_contract.sh"

// contractAgents are the seat agents agent_contract.sh knows.
var contractAgents = map[string]bool{
	"codex": true, "codex-ollama": true, "codex-interactive": true, "cursor-agent": true,
	"claude": true, "claude-ollama": true, "agy": true, "muse": true, "prime-agent": true,
}

// packageManaged names how Homebrew and npm ship a binary, for copies too old to
// update themselves. Homebrew ships both only as casks now, so a Cellar copy is
// from a retired formula that `brew upgrade` cannot move forward.
var packageManaged = map[string]struct{ brew, npm string }{
	"codex":  {"codex", "@openai/codex"},
	"claude": {"claude-code", "@anthropic-ai/claude-code"},
}

// selfUpdates succeeds when binary's help lists an update command. An older codex
// takes an unknown word as a prompt, so `codex update` there would start a
// session. Some CLIs (agy) print help on stderr.
func selfUpdates(binary string) string {
	return binary + ` --help 2>&1 </dev/null | grep -qE '^[[:space:]]+update\b'`
}

// npmRoot sets $n to npm's global node_modules, so only a copy npm installed is
// updated with npm: bun and yarn also install under a node_modules.
const npmRoot = `n=$(npm root -g 2>/dev/null) && [ -n "$n" ] && n=$(realpath "$n" 2>/dev/null) || n=/nonexistent; `

// agentUpdate is the shell that updates binary: its own update command first,
// then the package manager that installed the copy on PATH.
func agentUpdate(binary string) string {
	fallback := `echo "update $(command -v ` + binary + `) by hand, or put a current ` + binary + ` first on PATH"`
	if pm, ok := packageManaged[binary]; ok {
		fallback = `p=$(realpath "$(command -v ` + binary + `)" 2>/dev/null) || p=; ` + npmRoot + `case "$p" in ` +
			`*/Cellar/*) brew uninstall --formula ` + pm.brew + ` && brew install --cask ` + pm.brew + ` ;; ` +
			`*/Caskroom/*) brew upgrade --cask ` + pm.brew + ` ;; ` +
			`"$n"/*) npm install -g ` + pm.npm + `@latest ;; ` +
			`*) echo "update ${p:-` + binary + `} by hand, or put a current ` + binary + ` first on PATH" ;; esac`
	}
	return "{ { " + selfUpdates(binary) + " && " + binary + " update </dev/null; } || { " + fallback + "; }; }"
}

// agentUpdatable is the shell that succeeds when agentUpdate has a way to update
// the copy on PATH.
func agentUpdatable(binary string) string {
	if _, ok := packageManaged[binary]; !ok {
		return selfUpdates(binary)
	}
	return selfUpdates(binary) + ` || { ` + npmRoot + `case "$(realpath "$(command -v ` + binary + `)" 2>/dev/null)" in ` +
		`*/Cellar/*|*/Caskroom/*|"$n"/*) true ;; *) false ;; esac; }`
}

// contractPreflight checks a seat agent against this Factory, updates it when it
// falls short, and checks again; the second check's output is the answer. An
// absent agent is left to its install line.
func contractPreflight(agent string) string {
	b := seatBinary(agent)
	check := `"$FACTORY_HOME/` + agentContract + `" ` + agent + " " + b
	return "command -v " + b + " >/dev/null && { " + check + " >/dev/null 2>&1 || " + agentUpdate(b) + "; " + check + "; }"
}

// laneAgent is the agent_contract.sh name of an installable agent, if it has one.
func laneAgent(id AgentID) (string, bool) {
	switch id {
	case AgentCodex:
		return "codex", true
	case AgentClaude:
		return "claude", true
	}
	return "", false
}

// agentShortfall is why the installed agent falls short of the pinned Factory's
// contract, in agent_contract.sh's words, or "" when it meets it. Without a
// Factory checkout (or a script) there is nothing to hold it to.
func agentShortfall(ctx context.Context, env *Env, a *Agent) string {
	lane, ok := laneAgent(a.ID)
	if !ok {
		return ""
	}
	script := filepath.Join(env.Opts.FactoryDir(), agentContract)
	if _, ok := env.Have(script); !ok {
		return ""
	}
	out, err := env.Output(ctx, "bash", "-c", `r=$("$0" "$@" 2>&1 >/dev/null) || echo "${r:-$0 failed}"`, script, lane, a.Binary)
	if err != nil {
		return err.Error()
	}
	return out
}
