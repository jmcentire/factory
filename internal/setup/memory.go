package setup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
)

// Earlier releases wrote the Factory workflow into each agent's global memory
// file (CLAUDE.md, AGENTS.md, GEMINI.md) as one marked section, which put the
// factory into every session, not only factory runs. The workflow now lives in
// the /factory skill alone, so this step only removes that section; the rest
// of the file, the user's own rules, is left alone.

const (
	memoryBegin = "<!-- factory-setup: factory workflow (begin) -->"
	memoryEnd   = "<!-- factory-setup: factory workflow (end) -->"
)

// memoryPath is the global instructions file each agent loads at startup.
func memoryPath(o Options, id AgentID) string {
	switch id {
	case AgentClaude:
		if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
			return filepath.Join(absDir(dir), "CLAUDE.md")
		}
		return filepath.Join(o.Home, ".claude", "CLAUDE.md")
	case AgentCodex:
		return filepath.Join(codexHome(o), "AGENTS.md")
	case AgentOpenCode:
		return filepath.Join(o.Home, ".config", "opencode", "AGENTS.md")
	case AgentPrime:
		return filepath.Join(primeRoot(o), "AGENTS.md")
	case AgentPi:
		return filepath.Join(piRoot(o), "AGENTS.md")
	case AgentAntigravity:
		return filepath.Join(o.Home, ".gemini", "GEMINI.md")
	}
	return ""
}

// withoutMemoryBlock removes the marked section from cur, with the blank line
// an earlier release put before it.
func withoutMemoryBlock(cur string) string {
	i := strings.Index(cur, memoryBegin)
	if i < 0 {
		return cur
	}
	j := strings.Index(cur[i:], memoryEnd)
	if j < 0 {
		return cur
	}
	end := i + j + len(memoryEnd)
	if end < len(cur) && cur[end] == '\n' {
		end++
	}
	start := i
	if strings.HasSuffix(cur[:start], "\n\n") {
		start--
	}
	return cur[:start] + cur[end:]
}

// agentMemoryStep removes the old workflow section from one agent's memory file.
type agentMemoryStep struct{ agent *Agent }

func (s *agentMemoryStep) ID() string { return "agent." + string(s.agent.ID) + ".memory" }
func (s *agentMemoryStep) Title() string {
	return "Remove the old Factory workflow section from " + s.agent.Name + "'s memory"
}

func (s *agentMemoryStep) Check(_ context.Context, env *Env) (Status, string, error) {
	path := memoryPath(env.Opts, s.agent.ID)
	cur, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return StatusBlocked, err.Error(), nil
	}
	if string(cur) == withoutMemoryBlock(string(cur)) {
		return StatusDone, path, nil
	}
	return StatusMissing, path, nil
}

func (s *agentMemoryStep) Apply(_ context.Context, env *Env) error {
	path := memoryPath(env.Opts, s.agent.ID)
	cur, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	mode := os.FileMode(0o644)
	if st, err := os.Stat(path); err == nil {
		mode = st.Mode().Perm()
	}
	next := withoutMemoryBlock(string(cur))
	if next == string(cur) {
		return nil
	}
	// An earlier release created the file to hold only the section.
	if strings.TrimSpace(next) == "" {
		return os.Remove(path)
	}
	return env.WriteConfigFile(path, []byte(next), mode)
}
