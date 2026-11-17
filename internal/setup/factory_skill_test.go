package setup

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func renderedSkill(t *testing.T) string {
	t.Helper()
	b, err := renderFactorySkill(testEnv(t, allComponents()))
	must(t, err)
	return string(b)
}

func skillLine(t *testing.T, skill, prefix string) string {
	t.Helper()
	for _, line := range strings.Split(skill, "\n") {
		if strings.HasPrefix(line, prefix) {
			return line
		}
	}
	t.Fatalf("skill has no line starting %q", prefix)
	return ""
}

func TestPreflightSaysWhenTheSessionHasNoNetwork(t *testing.T) {
	line := skillLine(t, renderedSkill(t), "curl -fsS -o /dev/null --max-time 8 https://registry.npmjs.org/")
	for _, online := range []bool{true, false} {
		bin := t.TempDir()
		code := "0"
		if !online {
			code = "6" // curl: could not resolve host
		}
		must(t, os.WriteFile(filepath.Join(bin, "curl"), []byte("#!/bin/sh\nexit "+code+"\n"), 0o755))
		cmd := exec.Command("bash", "-c", line)
		cmd.Env = []string{"PATH=" + bin + ":/usr/bin:/bin"}
		out, _ := cmd.CombinedOutput()
		if said := strings.Contains(string(out), "CANNOT REACH"); said == online {
			t.Fatalf("online=%v: output %q", online, out)
		}
	}
}

// fakeFactory lays out a Factory checkout as the pinned one ships it: a Python
// program without the exec bit, an executable script and a document.
func fakeFactory(t *testing.T, env *Env) {
	t.Helper()
	dir := env.Opts.FactoryDir()
	for path, mode := range map[string]os.FileMode{
		"prompts/validate.md": 0o644, "harness/dispatcher.py": 0o644, "harness/status.sh": 0o755,
		"harness/README.md": 0o644,
	} {
		full := filepath.Join(dir, path)
		must(t, os.MkdirAll(filepath.Dir(full), 0o755))
		must(t, os.WriteFile(full, []byte("x\n"), mode))
	}
}

func allowList(t *testing.T, path string) []string {
	t.Helper()
	m, err := readJSON(path)
	must(t, err)
	var out []string
	for _, v := range subMap(m, "permissions")["allow"].([]any) {
		out = append(out, v.(string))
	}
	return out
}

func TestCoordinatorsMayRunTheFactorysProgramsOnly(t *testing.T) {
	env := testEnv(t, allComponents())
	fakeFactory(t, env)
	dir := resolved(env.Opts.FactoryDir()) // macOS temp dirs sit behind /var -> /private/var
	harness := filepath.Join(dir, "harness")
	venv := filepath.Join(dir, ".venv", "bin", "python")
	settings := antigravitySettingsPath(env.Opts)
	must(t, os.MkdirAll(filepath.Dir(settings), 0o755))
	must(t, os.WriteFile(settings, []byte(`{"allowNonWorkspaceAccess": false, "permissions": {"allow": ["command(mine)"]}}`), 0o600))
	agy, _ := agentByID(AgentAntigravity)
	if agy.configured(env, agy) {
		t.Fatal("configured before the coordinator rules were written")
	}
	for range 2 { // idempotent
		must(t, agy.configure(context.Background(), env, agy))
	}
	got := allowList(t, settings)
	for _, want := range []string{"command(mine)", "command(python harness/dispatcher.py)",
		"command(python " + harness + "/dispatcher.py)", "command(" + venv + " " + harness + "/dispatcher.py)",
		"command(python3 harness/dispatcher.py)", "command(python3 " + harness + "/dispatcher.py)",
		"command(" + harness + "/status.sh)", "read_file(" + dir + ")"} {
		if n := slicesCount(got, want); n != 1 {
			t.Fatalf("%q appears %d times in %v", want, n, got)
		}
	}
	for _, never := range []string{"command(" + harness + "/README.md)", "command(" + harness + "/dispatcher.py)", "command(" + venv + ")"} {
		if slicesCount(got, never) != 0 {
			t.Fatalf("%q was allowed: %v", never, got)
		}
	}
	m, _ := readJSON(settings)
	if m["allowNonWorkspaceAccess"] != false {
		t.Fatalf("the user's own setting was changed: %v", m)
	}
	if !agy.configured(env, agy) {
		t.Fatal("not configured after writing the rules")
	}

	claude, _ := agentByID(AgentClaude)
	must(t, claude.configure(context.Background(), env, claude))
	_, claudeSettings, _ := claudePaths(env.Opts)
	cg := allowList(t, claudeSettings)
	for _, want := range []string{"Bash(python harness/dispatcher.py:*)", "Bash(python3 " + harness + "/dispatcher.py:*)", "Bash(" + harness + "/status.sh:*)", "Read(/" + dir + "/**)"} {
		if slicesCount(cg, want) != 1 {
			t.Fatalf("claude settings lack %q: %v", want, cg)
		}
	}
}

func TestCoordinatorRulesRefuseSettingsTheyWouldOverwrite(t *testing.T) {
	for _, body := range []string{`{"permissions": {"allow": "command(mine)"}}`, `{"permissions": "ask"}`} {
		env := testEnv(t, allComponents())
		fakeFactory(t, env)
		settings := antigravitySettingsPath(env.Opts)
		must(t, os.MkdirAll(filepath.Dir(settings), 0o755))
		must(t, os.WriteFile(settings, []byte(body), 0o600))
		agy, _ := agentByID(AgentAntigravity)
		if err := agy.configure(context.Background(), env, agy); err == nil {
			t.Fatalf("%s: configure overwrote the user's permissions", body)
		}
		if b, _ := os.ReadFile(settings); string(b) != body {
			t.Fatalf("%s: settings changed to %s", body, b)
		}
	}
}

func slicesCount(list []string, v string) int {
	n := 0
	for _, s := range list {
		if s == v {
			n++
		}
	}
	return n
}
