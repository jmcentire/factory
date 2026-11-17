package setup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func factoryPrompt(t *testing.T, name string) FactoryPrompt {
	t.Helper()
	for _, p := range FactoryPrompts {
		if p.Name == name {
			return p
		}
	}
	t.Fatalf("no prompt %q", name)
	return FactoryPrompt{}
}

func TestFactoryLoaderNamesTheAgentRunningIt(t *testing.T) {
	o := Options{Home: "/h", CodeDir: "/h/Code"}
	for id, want := range map[AgentID]string{
		AgentClaude: "claude", AgentCodex: "codex", AgentAntigravity: "agy", AgentPrime: "prime-agent",
		AgentOpenCode: "", AgentPi: "",
	} {
		a, _ := agentByID(id)
		if got := seatHost(a); got != want {
			t.Fatalf("%s: seat %q, want %q", id, got, want)
		}
	}
	got := promptLoader(factoryPrompt(t, "factory"), o, "$ARGUMENTS", "codex")
	if !strings.Contains(got, "You run this as the Validator seat: `codex`.") {
		t.Fatalf("loader does not name its agent:\n%s", got)
	}
	if got := promptLoader(factoryPrompt(t, "factory"), o, "$ARGUMENTS", ""); strings.Contains(got, "Validator seat") {
		t.Fatalf("an agent with no seat was given one:\n%s", got)
	}
	// Role prompts (/validate, /engineer, …) are not run as the Validator.
	if got := promptLoader(factoryPrompt(t, "validate"), o, "$ARGUMENTS", "codex"); strings.Contains(got, "Validator seat") {
		t.Fatalf("a role prompt claimed the Validator seat:\n%s", got)
	}
}

func TestAnOlderLoaderWithoutTheSeatLineIsRewritten(t *testing.T) {
	o := Options{Home: t.TempDir()}
	codex, _ := agentByID(AgentCodex)
	path := factoryLoaderPath(o, AgentCodex)
	must(t, os.MkdirAll(filepath.Dir(path), 0o755))
	must(t, os.WriteFile(path, []byte(promptLoader(factoryPrompt(t, "factory"), o, "the user's request", "")), 0o644))
	if loaderNamesHost(o, codex) {
		t.Fatal("a loader from before the seat line counts as current, so an upgrade would keep it")
	}
	must(t, os.WriteFile(path, []byte(promptLoader(factoryPrompt(t, "factory"), o, "the user's request", "codex")), 0o644))
	if !loaderNamesHost(o, codex) {
		t.Fatalf("a current loader is reported stale: %s", filepath.Base(path))
	}
}

func renderSeats(t *testing.T, seats Seats, chosen bool) string {
	t.Helper()
	env := testEnv(t, allComponents())
	env.Opts.Seats, env.Opts.CoderChosen = seats, chosen
	b, err := renderFactorySkill(env)
	must(t, err)
	return string(b)
}

const swapClause = "the Coder above is your own family"

func TestTheCoderMovesToTheConfiguredValidatorsFamily(t *testing.T) {
	defaults := DefaultSeats()
	for _, c := range []struct {
		name       string
		seats      Seats
		host, swap string
	}{
		{"defaults", defaults, "codex", `export FACTORY_CODER_AGENT="claude" FACTORY_CODER_MODEL="claude-opus-5-5"`},
		{"agy validator", Seats{Validator: Seat{Agent: "agy"}, Orchestrator: defaults.Orchestrator,
			Coder: defaults.Coder, Tester: defaults.Tester}, "codex", `export FACTORY_CODER_AGENT="agy"`},
		{"interactive codex coder", Seats{Validator: defaults.Validator, Orchestrator: defaults.Orchestrator,
			Coder: Seat{Agent: "codex-interactive"}, Tester: defaults.Tester}, "codex", `export FACTORY_CODER_AGENT="claude"`},
	} {
		skill := renderSeats(t, c.seats, false)
		if !strings.Contains(skill, "loader names you `"+c.host+"`") || !strings.Contains(skill, c.swap) {
			t.Fatalf("%s: no swap to %s:\n%s", c.name, c.swap, skill)
		}
	}
}

func TestTheCoderStaysWhenThereIsNothingToSwap(t *testing.T) {
	d := DefaultSeats()
	for _, c := range []struct {
		name   string
		seats  Seats
		chosen bool
	}{
		{"coder named at setup", d, true},
		{"local Ollama coder", Seats{Validator: d.Validator, Orchestrator: d.Orchestrator, Coder: Seat{Agent: "codex-ollama", Model: "qwen"}, Tester: d.Tester}, false},
		{"cursor-agent coder", Seats{Validator: d.Validator, Orchestrator: d.Orchestrator, Coder: Seat{Agent: "cursor-agent"}, Tester: d.Tester}, false},
		{"validator in the coder's family", Seats{Validator: Seat{Agent: "codex"}, Orchestrator: d.Orchestrator, Coder: d.Coder, Tester: d.Tester}, false},
		{"tester already in the target family", Seats{Validator: d.Validator, Orchestrator: d.Orchestrator, Coder: d.Coder, Tester: Seat{Agent: "claude", Model: "claude-opus-5-5"}}, false},
		{"tester runs the target family's model through agy", Seats{Validator: d.Validator, Orchestrator: d.Orchestrator, Coder: d.Coder, Tester: Seat{Agent: "agy", Model: "claude-opus-5-5"}}, false},
	} {
		if skill := renderSeats(t, c.seats, c.chosen); strings.Contains(skill, swapClause) {
			t.Fatalf("%s: swapped anyway:\n%s", c.name, skill)
		}
	}
}

func TestAgentsOnlyInstallCountsAsConfiguredWithoutAFactory(t *testing.T) {
	// No Factory checkout means no /factory loader to hold to its seat line.
	env := testEnv(t, Options{Home: t.TempDir(), Components: []Component{ComponentAgents}})
	for _, id := range []AgentID{AgentClaude, AgentCodex, AgentAntigravity, AgentPrime} {
		a, _ := agentByID(id)
		must(t, a.configure(context.Background(), env, a))
		if !a.configured(env, a) {
			t.Fatalf("%s configured without a Factory still reports unconfigured", id)
		}
	}
}

func TestAnOlderPrimeLoaderIsRewritten(t *testing.T) {
	o := Options{Home: t.TempDir()}
	prime, _ := agentByID(AgentPrime)
	path := factoryLoaderPath(o, AgentPrime)
	must(t, os.MkdirAll(filepath.Dir(path), 0o755))
	must(t, os.WriteFile(path, []byte(promptLoader(factoryPrompt(t, "factory"), o, "$ARGUMENTS", "")), 0o644))
	if loaderNamesHost(o, prime) {
		t.Fatal("a prime-agent loader from before the seat line counts as current")
	}
}

func TestPrimeIsNotConfiguredWithAnOlderLoader(t *testing.T) {
	env := testEnv(t, allComponents())
	must(t, os.MkdirAll(filepath.Join(env.Opts.FactoryDir(), "prompts"), 0o755))
	must(t, os.WriteFile(filepath.Join(env.Opts.FactoryDir(), "prompts", "validate.md"), []byte("x"), 0o644))
	prime, _ := agentByID(AgentPrime)
	must(t, prime.configure(context.Background(), env, prime))
	if !prime.configured(env, prime) {
		t.Fatal("prime-agent not configured right after configure")
	}
	path := factoryLoaderPath(env.Opts, AgentPrime)
	must(t, os.WriteFile(path, []byte(promptLoader(factoryPrompt(t, "factory"), env.Opts, "$ARGUMENTS", "")), 0o644))
	if prime.configured(env, prime) {
		t.Fatal("an older prime-agent loader counts as configured, so it is never refreshed")
	}
}
