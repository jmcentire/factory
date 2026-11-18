package setup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMemoryStepRemovesTheOldSectionAndKeepsUserRules(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	env := testEnv(t, o)
	path := memoryPath(env.Opts, AgentClaude)
	if path != filepath.Join(env.Opts.Home, ".claude", "CLAUDE.md") {
		t.Fatalf("claude memory path %q", path)
	}
	mine := "- never do zoom hover effects\n"
	later := "- later rule\n"
	old := memoryBegin + "\n## Software Factory: default workflow\n" + memoryEnd + "\n"
	writeTree(t, filepath.Dir(path), map[string]string{"CLAUDE.md": mine + "\n" + old + later})

	s := &agentMemoryStep{agent: agents[0]}
	if st, _, _ := s.Check(context.Background(), env); st != StatusMissing {
		t.Fatalf("expected missing while the old section is there, got %v", st)
	}
	must(t, s.Apply(context.Background(), env))
	b, err := os.ReadFile(path)
	must(t, err)
	if got := string(b); got != mine+later {
		t.Fatalf("section not removed cleanly:\n%q", got)
	}
	if st, _, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done after removal, got %v", st)
	}
	// A file without the section, or no file at all, is never touched.
	must(t, s.Apply(context.Background(), env))
	if b2, _ := os.ReadFile(path); string(b2) != mine+later {
		t.Fatalf("second apply changed the file:\n%q", b2)
	}
	must(t, os.Remove(path))
	if st, _, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done with no memory file, got %v", st)
	}
}

func TestMemoryStepDeletesAFileThatHeldOnlyTheSectionAndSkipsAnUnclosedOne(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	env := testEnv(t, o)
	path := memoryPath(env.Opts, AgentClaude)
	s := &agentMemoryStep{agent: agents[0]}

	writeTree(t, filepath.Dir(path), map[string]string{"CLAUDE.md": memoryBegin + "\nold\n" + memoryEnd + "\n"})
	must(t, s.Apply(context.Background(), env))
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("a file holding only the section should be removed, stat err %v", err)
	}

	unclosed := "- mine\n" + memoryBegin + "\nno end marker\n"
	writeTree(t, filepath.Dir(path), map[string]string{"CLAUDE.md": unclosed})
	if st, _, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("an unclosed section is not ours to cut, got %v", st)
	}
	must(t, s.Apply(context.Background(), env))
	if b, _ := os.ReadFile(path); string(b) != unclosed {
		t.Fatalf("unclosed section changed:\n%q", b)
	}
}

func TestMemoryPathsPerAgent(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", "/cfg")
	t.Setenv("CODEX_HOME", "/cx")
	t.Setenv("PRIME_AGENT_CODING_AGENT_DIR", "")
	o := Options{Home: "/h"}
	for id, want := range map[AgentID]string{
		AgentClaude:      "/cfg/CLAUDE.md",
		AgentCodex:       "/cx/AGENTS.md",
		AgentOpenCode:    "/h/.config/opencode/AGENTS.md",
		AgentPrime:       "/h/.prime/agent/AGENTS.md",
		AgentPi:          "/h/.pi/agent/AGENTS.md",
		AgentAntigravity: "/h/.gemini/GEMINI.md",
	} {
		if got := memoryPath(o, id); got != want {
			t.Errorf("%s: got %q, want %q", id, got, want)
		}
	}
}

func TestSeats(t *testing.T) {
	if s := ParseSeat("claude-ollama:glm-5.3:cloud"); s.Agent != "claude-ollama" || s.Model != "glm-5.3:cloud" {
		t.Fatalf("model with a colon: %+v", s)
	}
	if s := ParseSeat("codex"); s.Agent != "codex" || s.Model != "" || s.String() != "codex" {
		t.Fatalf("agent only: %+v", s)
	}
	must(t, DefaultSeats().Validate())
	for _, c := range []struct {
		mutate func(*Seats)
		want   string
	}{
		{func(s *Seats) { s.Orchestrator = ParseSeat("ollama:glm-5.3") }, "orchestrator agent"},
		{func(s *Seats) { s.Tester = ParseSeat("gemini") }, "tester agent"},
		{func(s *Seats) { s.Validator = ParseSeat("claude-ollama") }, "needs a model"},
	} {
		s := DefaultSeats()
		c.mutate(&s)
		if err := s.Validate(); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("want %q, got %v", c.want, err)
		}
	}
}
