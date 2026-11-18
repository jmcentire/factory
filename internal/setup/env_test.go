package setup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// linkIntoDotfiles moves path's directory entry into a dotfiles tree under
// home and leaves a relative symlink in its place, the way stow deploys
// config. It returns the link's target.
func linkIntoDotfiles(t *testing.T, home, path, content string) string {
	t.Helper()
	target := filepath.Join(home, "dot-files", strings.TrimPrefix(path, home))
	must(t, os.MkdirAll(filepath.Dir(target), 0o755))
	must(t, os.WriteFile(target, []byte(content), 0o644))
	must(t, os.MkdirAll(filepath.Dir(path), 0o755))
	rel, err := filepath.Rel(filepath.Dir(path), target)
	must(t, err)
	must(t, os.Symlink(rel, path))
	return target
}

func assertStillLinked(t *testing.T, path string) {
	t.Helper()
	st, err := os.Lstat(path)
	must(t, err)
	if st.Mode()&os.ModeSymlink == 0 {
		t.Fatalf("%s was replaced by a regular file", path)
	}
}

func TestClaudeSettingsSymlinkIsWrittenThroughAndKept(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	env := testEnv(t, o)
	_, settings, _ := claudePaths(env.Opts)
	target := linkIntoDotfiles(t, env.Opts.Home, settings, `{"theme":"dark"}`)

	must(t, configureClaude(context.Background(), env, nil))

	assertStillLinked(t, settings)
	m := readJSONT(t, target)
	if m["theme"] != "dark" {
		t.Fatal("unrelated settings key lost in the link target")
	}
	if _, ok := m["hooks"]; !ok {
		t.Fatal("installer settings did not reach the link target")
	}
	if !Exists(target + BackupSuffix) {
		t.Fatal("the link target was not backed up beside itself")
	}
}

func TestCodexConfigTOMLSymlinkIsWrittenThroughAndKept(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	_, config, _ := codexPaths(env.Opts)
	target := linkIntoDotfiles(t, env.Opts.Home, config, "model = \"mine\"\n")

	must(t, configureCodex(context.Background(), env, nil))

	assertStillLinked(t, config)
	b, err := os.ReadFile(target)
	must(t, err)
	if !strings.Contains(string(b), `model = "mine"`) {
		t.Fatalf("user key lost in the link target:\n%s", b)
	}
	// The same checks configuredCodex makes, against the link's target.
	if !codexConfigHas(target, "features", "hooks") ||
		!codexConfigHas(target, "mcp_servers.kindex", "command", kindexCommand(env.Opts)) {
		t.Fatalf("installer's edits did not reach the link target:\n%s", b)
	}
}

func TestMemorySymlinkIsCleanedThroughAndKept(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentClaude}
	env := testEnv(t, o)
	path := memoryPath(env.Opts, AgentClaude)
	target := linkIntoDotfiles(t, env.Opts.Home, path, "- my rule\n\n"+memoryBegin+"\nold\n"+memoryEnd+"\n")

	s := &agentMemoryStep{agent: agents[0]}
	must(t, s.Apply(context.Background(), env))

	assertStillLinked(t, path)
	b, err := os.ReadFile(target)
	must(t, err)
	if string(b) != "- my rule\n" {
		t.Fatalf("memory section not removed from the link target:\n%s", b)
	}
	if st, _, _ := s.Check(context.Background(), env); st != StatusDone {
		t.Fatalf("expected done after writing through the link, got %v", st)
	}
}

func TestWriteConfigFileRefusesADanglingSymlink(t *testing.T) {
	env := testEnv(t, allComponents())
	path := filepath.Join(env.Opts.Home, ".claude", "settings.json")
	must(t, os.MkdirAll(filepath.Dir(path), 0o755))
	missing := filepath.Join(env.Opts.Home, "nowhere", "settings.json")
	must(t, os.Symlink(missing, path))

	err := env.WriteConfigFile(path, []byte("{}\n"), 0o600)
	if err == nil || !strings.Contains(err.Error(), "does not resolve") {
		t.Fatalf("expected a dangling-link error, got %v", err)
	}
	if Exists(missing) {
		t.Fatal("created a file at the dangling link's target")
	}
	assertStillLinked(t, path)
}

func TestWriteConfigFileNamesTheLinkWhenTheTargetIsReadOnly(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores directory permissions")
	}
	env := testEnv(t, allComponents())
	path := filepath.Join(env.Opts.Home, ".claude", "settings.json")
	target := linkIntoDotfiles(t, env.Opts.Home, path, "{}\n")
	// A read-only directory stands in for the Nix store: the atomic write
	// cannot create its temp file beside the target.
	must(t, os.Chmod(filepath.Dir(target), 0o555))
	t.Cleanup(func() { _ = os.Chmod(filepath.Dir(target), 0o755) })

	err := env.WriteConfigFile(path, []byte(`{"a":1}`+"\n"), 0o600)
	if err == nil || !strings.Contains(err.Error(), path) || !strings.Contains(err.Error(), target) {
		t.Fatalf("expected an error naming both the link and its target, got %v", err)
	}
	assertStillLinked(t, path)
}

func TestWriteFileStillRefusesSymlinks(t *testing.T) {
	env := testEnv(t, allComponents())
	dir := t.TempDir()
	real := filepath.Join(dir, "kin")
	must(t, os.WriteFile(real, []byte("#!/bin/sh\n"), 0o755))
	link := filepath.Join(dir, "link")
	must(t, os.Symlink(real, link))

	err := env.WriteFile(link, []byte("changed\n"), 0o755)
	if err == nil || !strings.Contains(err.Error(), "refusing to write through it") {
		t.Fatalf("expected WriteFile to refuse a symlink, got %v", err)
	}
	b, err := os.ReadFile(real)
	must(t, err)
	if string(b) != "#!/bin/sh\n" {
		t.Fatal("WriteFile changed a symlink's target")
	}
}
