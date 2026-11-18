package setup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/BurntSushi/toml"
)

// The regression that motivated toml.go: a user's config.toml already had a
// [features] table, the installer appended a second one, and Codex refused
// to start on a duplicate key.
const userCodexConfig = `model = "gpt-5"
approval_policy = "on-request"

[mcp_servers.exa]
command = "npx"
args = ["-y", "exa-mcp-server"]

[projects."/Users/alex/Documents/polarity"]
trust_level = "trusted"

[features]
js_repl = false

[shell_environment_policy]
inherit = "all"
`

func decodeTOML(t *testing.T, path string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(path)
	must(t, err)
	var m map[string]any
	if _, err := toml.Decode(string(b), &m); err != nil {
		t.Fatalf("%s does not parse: %v\n%s", path, err, b)
	}
	return m
}

func TestCodexConfigMergesIntoExistingFeaturesTable(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	_, config, _ := codexPaths(env.Opts)
	must(t, os.MkdirAll(filepath.Dir(config), 0o700))
	must(t, os.WriteFile(config, []byte(userCodexConfig), 0o600))

	must(t, configureCodex(context.Background(), env, nil))

	b, err := os.ReadFile(config)
	must(t, err)
	body := string(b)
	if n := strings.Count(body, "[features]"); n != 1 {
		t.Fatalf("expected exactly one [features] table, got %d:\n%s", n, body)
	}
	m := decodeTOML(t, config)
	features := m["features"].(map[string]any)
	if features["js_repl"] != false || features["hooks"] != true {
		t.Fatalf("features table not merged: %v", features)
	}
	// User content preserved byte-for-byte where untouched.
	for _, want := range []string{`model = "gpt-5"`, `[mcp_servers.exa]`, `args = ["-y", "exa-mcp-server"]`, `[projects."/Users/alex/Documents/polarity"]`, `inherit = "all"`} {
		if !strings.Contains(body, want) {
			t.Errorf("user content lost: %q", want)
		}
	}
	servers := m["mcp_servers"].(map[string]any)
	for _, want := range []string{"exa", "kindex", "constrain", "pact", "transmogrifier", "signet"} {
		if servers[want] == nil {
			t.Errorf("mcp server %q missing", want)
		}
	}
	// Backup of the original exists and is the original.
	bak, err := os.ReadFile(config + BackupSuffix)
	must(t, err)
	if string(bak) != userCodexConfig {
		t.Fatal("backup is not the pre-edit file")
	}
	// Second pass: nothing changes, backup untouched.
	before := body
	must(t, configureCodex(context.Background(), env, nil))
	after, _ := os.ReadFile(config)
	if string(after) != before {
		t.Fatal("second configure changed config.toml")
	}
	if !configuredCodex(env, nil) {
		t.Fatal("configuredCodex false after configure")
	}
}

func TestCodexConfigLeavesUserServerOfSameName(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	_, config, _ := codexPaths(env.Opts)
	must(t, os.MkdirAll(filepath.Dir(config), 0o700))
	must(t, os.WriteFile(config, []byte("[mcp_servers.constrain]\ncommand = \"/opt/my/constrain-mcp\"\n"), 0o600))
	must(t, configureCodex(context.Background(), env, nil))
	m := decodeTOML(t, config)
	constrain := m["mcp_servers"].(map[string]any)["constrain"].(map[string]any)
	if constrain["command"] != "/opt/my/constrain-mcp" {
		t.Fatalf("user-defined server overwritten: %v", constrain)
	}
	if n := strings.Count(mustRead(t, config), "[mcp_servers.constrain]"); n != 1 {
		t.Fatalf("duplicate constrain table: %d", n)
	}
}

// kindex's server follows the kindex installed: one wired to an older kindex
// is rewired to the new one, in place, keeping the rest of the file.
func TestCodexConfigMovesAnOlderKindexToTheNewOne(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	_, config, _ := codexPaths(env.Opts)
	must(t, os.MkdirAll(filepath.Dir(config), 0o700))
	must(t, os.WriteFile(config, []byte("[mcp_servers.kindex]\ncommand = \"/opt/old/kin-mcp\"\n\n[model]\nname = \"mine\"\n"), 0o600))
	if configuredCodex(env, nil) {
		t.Fatal("an older kindex reads as configured")
	}
	must(t, configureCodex(context.Background(), env, nil))
	m := decodeTOML(t, config)
	if got := m["mcp_servers"].(map[string]any)["kindex"].(map[string]any)["command"]; got != kindexCommand(env.Opts) {
		t.Fatalf("kindex runs %v, want %s", got, kindexCommand(env.Opts))
	}
	if m["model"].(map[string]any)["name"] != "mine" {
		t.Fatal("the rest of config.toml was lost")
	}
	if n := strings.Count(mustRead(t, config), "[mcp_servers.kindex]"); n != 1 {
		t.Fatalf("duplicate kindex table: %d", n)
	}
	if !configuredCodex(env, nil) {
		t.Fatal("not configured after rewiring")
	}
}

func TestCodexConfigRefusesMalformedInput(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	_, config, _ := codexPaths(env.Opts)
	must(t, os.MkdirAll(filepath.Dir(config), 0o700))
	broken := "[features]\njs_repl = false\n[features]\nhooks = true\n"
	must(t, os.WriteFile(config, []byte(broken), 0o600))
	err := configureCodex(context.Background(), env, nil)
	if err == nil || !strings.Contains(err.Error(), "does not parse") {
		t.Fatalf("expected refusal on malformed TOML, got %v", err)
	}
	if mustRead(t, config) != broken {
		t.Fatal("malformed file was modified")
	}
}

func TestCodexConfigFromScratchParses(t *testing.T) {
	o := allComponents()
	o.Agents = []AgentID{AgentCodex}
	env := testEnv(t, o)
	_, config, _ := codexPaths(env.Opts)
	must(t, configureCodex(context.Background(), env, nil))
	m := decodeTOML(t, config)
	if m["features"].(map[string]any)["hooks"] != true {
		t.Fatalf("hooks not enabled: %v", m)
	}
	if _, err := os.Stat(config + BackupSuffix); err == nil {
		t.Fatal("no backup should be written when the file did not exist")
	}
}

func TestTOMLSetKeyReplacesInPlace(t *testing.T) {
	d := parseTOMLDoc("[features]\nhooks = false\nother = 1\n")
	if !d.setKey("features", "hooks", "true") {
		t.Fatal("expected change")
	}
	if d.setKey("features", "hooks", "true") {
		t.Fatal("expected no change on repeat")
	}
	out := d.String()
	if strings.Count(out, "hooks") != 1 || !strings.Contains(out, "hooks = true") || !strings.Contains(out, "other = 1") {
		t.Fatalf("unexpected result:\n%s", out)
	}
}

func TestWriteFileBacksUpExisting(t *testing.T) {
	env := testEnv(t, allComponents())
	p := filepath.Join(env.Opts.Home, "x.txt")
	must(t, env.WriteFile(p, []byte("one"), 0o644))
	if Exists(p + BackupSuffix) {
		t.Fatal("no backup for a new file")
	}
	must(t, env.WriteFile(p, []byte("one"), 0o644))
	if Exists(p + BackupSuffix) {
		t.Fatal("no backup when content is unchanged")
	}
	must(t, env.WriteFile(p, []byte("two"), 0o644))
	if mustRead(t, p+BackupSuffix) != "one" {
		t.Fatal("backup should hold the previous content")
	}
}

func mustRead(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	must(t, err)
	return string(b)
}
